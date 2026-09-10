#!/usr/bin/env python3
"""Review one Handoff session on a token-protected loopback page."""

from __future__ import annotations

import argparse
import glob
import hmac
import importlib.util
import json
import math
import re
import secrets
import sys
import threading
import webbrowser
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from handoff_runtime import inject, job_state, read_meta


def load_module(name):
    spec = importlib.util.spec_from_file_location(name.replace('-', '_'), SCRIPT_DIR / f'{name}.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


transcript = load_module('render-transcript')
cost = load_module('render-cost-receipt')
TEMPLATE = SCRIPT_DIR.parent / 'assets' / 'session-view.html'
SAFE_ID = re.compile(r'[A-Za-z0-9._-]+')
CSP = {
    'shell': "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; frame-src 'self'; connect-src 'self'; frame-ancestors 'none'",
    'svg': "default-src 'none'; style-src 'unsafe-inline'",
    'frame': "default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'",
}
JOB_INPUTS = ('log.jsonl', 'meta', 'prompt.md', 'exit_code', 'pid', 'cancelled')


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.utcoffset() is None:
        raise ValueError('timestamp must be offset-aware')
    return parsed.timestamp()


def elapsed(start, finish):
    """The receipt's own duration grammar, so the page and the receipt agree."""
    if start is None or finish is None or finish < start:
        return 'not recorded'
    whole = int(finish - start)  # truncate, as make-receipt.py does, so both agree
    return f'{whole // 60}min {whole % 60:02d}sec'


def finite(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('coordinate must be finite')
    return value


def parse_map(svg):
    """Reject the whole map on structural failure; reconciliation is separate."""
    try:
        root = ET.fromstring(svg)
        if root.tag not in ('svg', '{http://www.w3.org/2000/svg}svg'):
            raise ValueError('not an SVG root')
        box = [finite(float(v)) for v in re.split(r'[\s,]+', root.attrib['viewBox'].strip())]
        if len(box) != 4 or box[2] <= 0 or box[3] <= 0:
            raise ValueError('invalid viewBox')
        axis = json.loads(root.attrib['data-axis'])
        lanes = json.loads(root.attrib['data-lanes'])
        if not isinstance(axis, list) or not axis or not isinstance(lanes, list):
            raise ValueError('axis and lanes must be lists')
        segments = []
        for entry in axis:
            segment = {k: finite(entry[k]) for k in ('x0', 'x1')}
            segment.update({k: timestamp(entry[k]) for k in ('t0', 't1')})
            if segment['t1'] <= segment['t0'] or segment['x1'] <= segment['x0']:
                raise ValueError('empty segment')
            if segments and (segment['t0'] < segments[-1]['t1'] or segment['x0'] < segments[-1]['x1']):
                raise ValueError('unordered or overlapping segments')
            segments.append(segment)
        bands, jobs, parsed_lanes = [], set(), []
        driver_seen = False
        for entry in lanes:
            lane = {k: finite(entry[k]) for k in ('y0', 'y1')}
            if lane['y1'] <= lane['y0'] or any(lane['y0'] < hi and lane['y1'] > lo for lo, hi in bands):
                raise ValueError('empty or overlapping lane')
            bands.append((lane['y0'], lane['y1']))
            if entry.get('lane') == 'driver':
                if driver_seen or any(k in entry for k in ('job', 't0', 't1')):
                    raise ValueError('driver carries no job or window')
                driver_seen = True
                lane['lane'] = 'driver'
            else:
                job = entry['job']
                if 'lane' in entry or not isinstance(job, str) or not job or job in jobs:
                    raise ValueError('invalid or duplicate job')
                jobs.add(job)
                lane.update(job=job, t0=timestamp(entry['t0']), t1=timestamp(entry['t1']))
                if lane['t1'] <= lane['t0']:
                    raise ValueError('empty lane window')
            parsed_lanes.append(lane)
        return {'viewBox': box, 'axis': segments, 'lanes': parsed_lanes}
    except (ET.ParseError, KeyError, ValueError, TypeError, AttributeError, OverflowError):
        return None


def rectangles(lane, lane_map):
    ox, oy, width, height = lane_map['viewBox']
    rects = []
    for segment in lane_map['axis']:
        start = max(lane['t0'], segment['t0']) if 'job' in lane else segment['t0']
        end = min(lane['t1'], segment['t1']) if 'job' in lane else segment['t1']
        if end <= start:
            continue
        scale = (segment['x1'] - segment['x0']) / (segment['t1'] - segment['t0'])
        rects.append({'left': (segment['x0'] + (start - segment['t0']) * scale - ox) / width * 100,
                      'top': (lane['y0'] - oy) / height * 100,
                      'width': (end - start) * scale / width * 100,
                      'height': (lane['y1'] - lane['y0']) / height * 100})
    return rects


def reconcile(lane_map, jobs):
    indexed = {j['job_id']: j for j in jobs}
    declared = {lane['job'] for lane in lane_map['lanes'] if 'job' in lane}
    issues = [f'{job}: missing lane' for job in sorted(indexed.keys() - declared)]
    hotspots = []
    for lane in lane_map['lanes']:
        job_id = lane.get('job')
        if job_id is not None:
            job = indexed.get(job_id)
            if job is None:
                issues.append(f'{job_id}: unknown lane')
                continue
            if job['state'] == 'running':
                issues.append(f'{job_id}: unverifiable (job running)')
                continue
            if job['t0'] is None or job['t1'] is None:
                issues.append(f'{job_id}: unverifiable (window not recorded)')
                continue
            if any(abs(lane[key] - job[key]) > 60 for key in ('t0', 't1')):
                issues.append(f'{job_id}: declared window differs from measured window')
                continue
        rects = rectangles(lane, lane_map)
        if not rects:
            issues.append(f'{job_id or "driver"}: outside the drawn axis')
        hotspots.extend(dict(rect, job=job_id) for rect in rects)
    banner = (f'{len(issues)} lane map discrepancies: ' + '; '.join(issues) if issues
              else f'Lane map matches the {len(indexed)} indexed jobs')
    return {'hotspots': hotspots, 'banner': banner}


def read_goal(path, indexed):
    raw = path.read_text(encoding='utf-8', errors='replace') if path.is_file() else ''
    sections = {}
    heading = None
    for line in raw.splitlines():
        if line.startswith('## '):
            heading = line[3:].strip()
            sections[heading] = []
        elif heading:
            sections[heading].append(line)
    sections = {key: '\n'.join(lines).strip() for key, lines in sections.items()}
    task_text = sections.get('Tasks', '')
    lines = [line.strip() for line in task_text.splitlines() if line.strip().startswith('|')]
    table = None
    if len(lines) >= 2:
        cells = [[cell.strip() for cell in line.strip('|').split('|')] for line in lines]
        headers = cells[0]
        if ('jobId' in headers and len(set(headers)) == len(headers)
                and all(len(row) == len(headers) for row in cells)
                and all(re.fullmatch(r':?-{3,}:?', cell) for cell in cells[1])):
            table = {'headers': headers, 'rows': cells[2:]}
    job_ids = ({row[table['headers'].index('jobId')].strip('`') for row in table['rows']}
               if table else set())
    return {'matched': bool(job_ids & set(indexed)), 'sections': sections,
            'tasks': table, 'raw': raw}


def roles_used(raw):
    if raw == 'none':
        return 'none'
    try:
        roles = json.loads(raw)
    except (ValueError, TypeError):
        return 'not recorded'
    if not isinstance(roles, list):
        return 'not recorded'
    return [entry if isinstance(entry, dict)
            and all(isinstance(entry.get(key), str) for key in ('role', 'host', 'model', 'effort'))
            and isinstance(entry.get('verified'), bool) else 'not recorded' for entry in roles]


def denials(job, backend):
    if backend == 'codex':
        return {'state': 'not reported (codex)', 'items': []}
    events = [event for event in cost.read_events(job) if isinstance(event, dict)]
    if backend == 'claude':
        results = [event for event in events if event.get('type') == 'result']
        if not results:
            return {'state': 'not yet available', 'items': []}
        items = results[-1].get('permission_denials')
        if not isinstance(items, list):
            return {'state': 'not reported (claude)', 'items': []}
        return {'state': 'reported', 'items': [
            {'tool': item.get('tool_name', 'details unavailable'),
             'command': item.get('tool_input', 'details unavailable')}
            for item in items if isinstance(item, dict)]}
    starts = {}
    for event in events:
        data = event.get('data')
        if event.get('type') == 'tool.execution_start' and isinstance(data, dict) and data.get('toolCallId'):
            starts[data['toolCallId']] = data
    items = []
    for event in events:
        data = event.get('data')
        if event.get('type') != 'tool.execution_complete' or not isinstance(data, dict):
            continue
        error = data.get('error')
        if not isinstance(error, dict) or error.get('code') != 'denied':
            continue
        start = starts.get(data.get('toolCallId'), {})
        items.append({'tool': start.get('toolName', 'details unavailable'),
                      'command': start.get('arguments', 'details unavailable'),
                      'toolCallId': data.get('toolCallId', 'not recorded'), 'error': error})
    return {'state': 'reported', 'items': items}


class Session:
    def __init__(self, repo, receipt):
        self.repo = repo.resolve()
        self.receipt_path = cost.resolve_receipt(self.repo, receipt).resolve()
        self.receipt = cost.load_receipt(self.receipt_path.read_text(encoding='utf-8'), self.repo)
        self.indexed = {job['job_id'] for job in self.receipt['jobs']}
        self.locks = {}
        self.lock_guard = threading.Lock()
        self.cache = {}

    def job_dir(self, job_id):
        if not SAFE_ID.fullmatch(job_id) or job_id in ('.', '..'):
            raise ValueError('job id is not a safe path segment')
        if job_id not in self.indexed:
            raise ValueError('job is not indexed by this receipt')
        root = (self.repo / '.handoff' / 'jobs').resolve()
        path = (root / job_id).resolve()
        if root not in path.parents or not path.is_dir():
            raise ValueError('job directory is outside the jobs directory or missing')
        return path

    def diagram(self):
        folder = self.repo / '.handoff' / 'receipts' / 'diagram'
        matches = sorted(folder.glob(self.receipt_path.stem + '_*.svg'))
        exact = folder / (self.receipt_path.stem + '.svg')
        return exact if exact.is_file() else (matches[0] if matches else None)

    def output_path(self, folder, name):
        root = self.repo / '.handoff'
        path = root / folder / name
        if root.resolve() != root or path.resolve() != path:
            raise ValueError('cache path must not be a symlink')
        return path

    def jobs(self):
        rows = []
        for entry in self.receipt['jobs']:
            job = self.job_dir(entry['job_id'])
            meta = read_meta(job)
            end = job / 'exit_code'
            state = 'running' if entry['running'] else job_state(job).lower()
            try:
                start = timestamp(meta.get('submitted_at', ''))
            except (ValueError, AttributeError, OverflowError):
                start = None
            finish = end.stat().st_mtime if end.is_file() and state != 'running' else None
            rows.append({**entry, **{key: meta.get(key, 'not recorded') for key in ('role', 'model', 'effort')},
                         'state': state, 't0': start, 't1': finish,
                         'start': meta.get('submitted_at', 'not recorded'),
                         'end': datetime.fromtimestamp(finish, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ') if finish is not None else 'not recorded',
                         'duration': elapsed(start, finish),
                         'exit_code': end.read_text().strip() if end.is_file() else 'not recorded'})
        return rows

    def payload(self, token):
        jobs = self.jobs()
        diagram = self.diagram()
        lane_map = parse_map(diagram.read_bytes()) if diagram else None
        overview = (reconcile(lane_map, jobs) if lane_map else
                    {'hotspots': [], 'banner': 'The diagram declares no lane map' if diagram else 'No diagram for this receipt'})
        fields = self.receipt['fields']
        facts = {key: fields.get(key, 'not recorded') for key in
                 ('phase', 'duration', 'checks', 'anomalies', 'scope', 'config_source', 'receipt_schema_version')}
        facts['roles_used'] = roles_used(fields.get('roles_used'))
        denial_rows, anomalies, warnings = [], [], []
        for row in jobs:
            job = self.job_dir(row['job_id'])
            denial_rows.append({'job': row['job_id'], **denials(job, row['backend'])})
            if row['state'] in ('failed', 'cancelled'):
                anomalies.append(f"{row['job_id']}: {row['state']}")
            if row['exit_code'] not in ('0', 'not recorded'):
                anomalies.append(f"{row['job_id']}: exit code {row['exit_code']}")
            stderr = job / 'stderr.log'
            if stderr.is_file() and stderr.stat().st_size:
                anomalies.append(f"{row['job_id']}: non-empty stderr.log")
            output = self.output_path('transcripts', row['job_id'] + '.html')
            if not transcript._is_ignored(output):
                warnings.append(f'{row["job_id"]}: transcript output is not ignored by Git; do not commit captured command output')
        url = lambda path: path + '?token=' + quote(token, safe='')
        for row in jobs:
            row['url'] = url('/transcript/' + quote(row['job_id'], safe=''))
        return {'jobs': jobs, 'facts': facts, 'goal': read_goal(self.repo / '.handoff' / 'goal.md', self.indexed),
                'denials': denial_rows, 'anomalies': anomalies, 'warnings': warnings,
                'overview': {**overview, 'mapped': lane_map is not None,
                             'image': url('/diagram.svg') if diagram else None},
                'cost_url': url('/cost-receipt'), 'prompt': self.diagram_prompt()}

    def diagram_target(self):
        return (self.repo / '.handoff/receipts/diagram'
                / (self.receipt_path.stem + '_handoff-session-timeline.svg'))

    def diagram_prompt(self):
        return (f'Use baoyu-diagram to read the receipt at {self.receipt_path} and the matching goal, '
                f'if any, at {self.repo / ".handoff/goal.md"}. Produce a hand-authored narrative session '
                f'timeline at {self.diagram_target()}. '
                'Band the run into its five phases. Give every identity its own sub-timeline, forked '
                'from the driver lane at the point the driver submitted its job and joining back where '
                'the driver read the result. Use a light theme and a minimum font size of 12px. Render '
                'every clock label in the local timezone and name that zone in the subtitle. '
                'Label the SVG as narrative illustration, with measured records in the tabs. '
                'On the SVG root declare a positive-width/height viewBox, data-axis as a JSON list of '
                '{"t0":"offset-aware ISO8601","t1":"offset-aware ISO8601","x0":270,"x1":1230} '
                'segments using tick positions (not stroke endpoints), ordered and non-overlapping in time and x. '
                'Declare data-lanes as a JSON list of {"job":"indexed jobId","y0":300,"y1":362,'
                '"t0":"offset-aware ISO8601","t1":"offset-aware ISO8601"}. Include every indexed job once, '
                'no unknown jobs or overlapping bands, all coordinates finite. Declared windows must match '
                'meta.submitted_at and exit_code mtime within 60 seconds. Do not invent a running job end. '
                'An optional {"lane":"driver","y0":180,"y1":250} carries no window. Draw bars at the '
                'declared coordinates; keep gaps in the time axis. The page checks lane claims, not narrative prose.')

    def cached(self, output, inputs, render):
        with self.lock_guard:
            lock = self.locks.setdefault(output, threading.Lock())
        with lock:
            paths = inputs()
            signature = tuple((str(p), p.stat().st_mtime_ns if p.is_file() else None) for p in paths)
            newest = max((mtime for _, mtime in signature if mtime is not None), default=-1)
            if (not output.is_file() or output.stat().st_mtime_ns <= newest
                    or self.cache.get(output) != signature):
                if render() != 0:
                    raise ValueError('renderer failed')
                self.cache[output] = signature
            return output.read_bytes()

    def render_transcript(self, job_id):
        job = self.job_dir(job_id)
        output = self.output_path('transcripts', job.name + '.html')
        return self.cached(output, lambda: [job / name for name in JOB_INPUTS] + [transcript.DEFAULT_TEMPLATE],
                           lambda: transcript.main([str(job), '--repo', str(self.repo), '--no-open']))

    def cost_inputs(self):
        inputs = [self.receipt_path, cost.DEFAULT_TEMPLATE]
        # Model inheritance can read parent meta outside the receipt's job set.
        inputs.extend(sorted((self.repo / '.handoff/jobs').glob('*/meta')))
        for job_id in sorted(self.indexed):
            job = self.job_dir(job_id)
            inputs.extend(job / name for name in (*JOB_INPUTS, 'usage.json'))
        session = self.receipt['fields'].get('claude_session', 'none')
        if session != 'none':
            inputs.extend(Path(p) for p in sorted(glob.glob(str(cost.DEFAULT_PROJECTS / '*' / (glob.escape(session) + '.jsonl')))))
        return inputs

    def render_cost(self):
        # Revalidate changed receipts before handing the absolute path to main.
        current = cost.load_receipt(self.receipt_path.read_text(encoding='utf-8'), self.repo)
        if current['jobs'] != self.receipt['jobs']:
            raise ValueError('receipt job index changed; restart the session viewer')
        self.receipt = current
        match = cost.RECEIPT_STAMP.match(self.receipt_path.name)
        stem = match.group(1) if match else self.receipt_path.stem
        output = self.output_path('cost-receipts', f'cost-receipt-{stem}.html')
        self.output_path('cost-receipts', f'cost-receipt-{stem}.md')
        return self.cached(output, self.cost_inputs,
                           lambda: cost.main([str(self.receipt_path), '--repo', str(self.repo), '--no-open']))


def make_handler(session, token):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            request = urlsplit(self.path)
            # Answered before the token check on purpose: a browser requests its
            # icon with no token, and a 204 carrying no body discloses nothing the
            # open port has not already disclosed. Without it every page load
            # plants a 403 in the console of a tool built to surface real faults.
            if unquote(request.path) == '/favicon.ico':
                self.respond(204, b'', 'image/x-icon', 'shell')
                return
            supplied = parse_qs(request.query).get('token', [''])[0]
            hosts = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
            if self.headers.get('Host') not in hosts or not hmac.compare_digest(supplied.encode(), token.encode()):
                self.respond(403, b'Invalid local access token', 'text/plain', 'shell')
                return
            path = unquote(request.path)
            try:
                if path == '/':
                    body = inject(TEMPLATE.read_text(encoding='utf-8'), session.payload(token)).encode()
                    self.respond(200, body, 'text/html; charset=utf-8', 'shell')
                elif path == '/diagram.svg' and session.diagram():
                    self.respond(200, session.diagram().read_bytes(), 'image/svg+xml', 'svg')
                elif path.startswith('/transcript/'):
                    self.respond(200, session.render_transcript(path[len('/transcript/'):]), 'text/html; charset=utf-8', 'frame')
                elif path == '/cost-receipt':
                    self.respond(200, session.render_cost(), 'text/html; charset=utf-8', 'frame')
                else:
                    self.respond(404, b'Not found', 'text/plain', 'shell')
            except (OSError, ValueError, cost.ReceiptError) as error:
                self.respond(400, str(error).encode(), 'text/plain; charset=utf-8', 'shell')

        def respond(self, status, body, content_type, policy):
            self.send_response(status)
            self.send_header('Content-Type', content_type)
            self.send_header('Content-Length', str(len(body)))
            self.send_header('Content-Security-Policy', CSP[policy])
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.send_header('Cache-Control', 'no-store')
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            pass
    return Handler


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('receipt', nargs='?')
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--port', type=int, default=0)
    parser.add_argument('--no-open', action='store_true')
    parser.add_argument('--allow-missing-diagram', action='store_true',
                        help='Open without a timeline diagram, on the job-table rung.')
    args = parser.parse_args(argv)
    try:
        session = Session(args.repo, args.receipt)
    except (OSError, ValueError, cost.ReceiptError) as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2

    # Both assets are on disk before a browser opens. This page is a review
    # surface; a reviewer who lands on an empty overview and an empty cost tab
    # learns nothing about the run and cannot tell a missing asset from a bug.
    #
    # Only one of the two can be made here. The cost receipt is Python, so it is
    # rendered eagerly. A diagram needs a model, and a renderer that spawns one
    # would bill a vendor as a side effect of opening a page, so this exits 3 and
    # hands the caller the prompt instead. Exit 3 means "an asset is missing and
    # only a model can make it", which is a different instruction to the caller
    # than exit 2's "your input is wrong".
    diagram = session.diagram()
    if diagram is None and not args.allow_missing_diagram:
        print(f'ERROR: no timeline diagram for {session.receipt_path.name}.', file=sys.stderr)
        print(f'Generate it at {session.diagram_target()} with this prompt, then run again:',
              file=sys.stderr)
        print('', file=sys.stderr)
        print(session.diagram_prompt(), file=sys.stderr)
        print('', file=sys.stderr)
        print('Or pass --allow-missing-diagram to open on the job-table rung.', file=sys.stderr)
        return 3
    # A diagram that exists but declares no lane map is not a refusal: the page
    # opens and is useful. But the driver cannot see the page's banner, so the
    # state and the prompt go to stdout, and the driver asks before spending a
    # generation on a page that already works.
    if diagram is None:
        print('timeline diagram: none (--allow-missing-diagram)', flush=True)
    elif parse_map(diagram.read_bytes()) is None:
        print(f'timeline diagram: {diagram} (declares no lane map: no hotspots)', flush=True)
        print('Ask the user before spending anything. Cheapest fix: annotate that file '
              'in place, reading its tick x positions and lane band y bounds and adding '
              'viewBox, data-axis and data-lanes to the SVG root. Redraw only if its '
              'geometry is unreadable, with this prompt, then run again:', flush=True)
        print('', flush=True)
        print(session.diagram_prompt(), flush=True)
        print('', flush=True)
    else:
        print(f'timeline diagram: {diagram}', flush=True)
    try:
        session.render_cost()
    except (OSError, ValueError, cost.ReceiptError) as error:
        print(f'ERROR: cannot render the cost receipt: {error}', file=sys.stderr)
        return 2
    print('cost receipt: rendered', flush=True)

    try:
        token = secrets.token_urlsafe(24)
        server = ThreadingHTTPServer(('127.0.0.1', args.port), make_handler(session, token))
    except OSError as error:
        print(f'ERROR: {error}', file=sys.stderr)
        return 2
    url = f'http://127.0.0.1:{server.server_port}/?token={token}'
    print(f'Handoff session: {url}', flush=True)
    print('Only localhost can connect. Press Ctrl-C to stop.', flush=True)
    if not args.no_open:
        webbrowser.open(url)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
