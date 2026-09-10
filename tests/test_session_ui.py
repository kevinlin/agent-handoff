from __future__ import annotations

import concurrent.futures
import contextlib
import copy
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'tests/fixtures/session-view'
SPEC = importlib.util.spec_from_file_location('session_ui', ROOT / 'scripts/handoff-session-ui.py')
ui = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ui)


class SessionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        receipts = self.repo / '.handoff/receipts'
        receipts.mkdir(parents=True)
        self.receipt = receipts / 'receipt-20260910T003000Z.md'
        shutil.copyfile(FIXTURES / self.receipt.name, self.receipt)
        for name, backend, start, finish in (
                ('job-a', 'codex', '2026-09-10T00:05:00Z', '2026-09-10T00:25:00Z'),
                ('job-b', 'claude', '2026-09-10T00:01:00Z', '2026-09-10T00:03:00Z')):
            job = self.repo / '.handoff/jobs' / name
            job.mkdir(parents=True)
            (job / 'meta').write_text(f'backend={backend}\nsubmitted_at={start}\nmodel=fixture\nrole=fast_worker\neffort=high\n')
            (job / 'exit_code').write_text('0')
            os.utime(job / 'exit_code', (ui.timestamp(finish), ui.timestamp(finish)))
            (job / 'log.jsonl').write_text('')
            (job / 'prompt.md').write_text('fixture prompt')
        shutil.copyfile(FIXTURES / 'goal.md', self.repo / '.handoff/goal.md')
        self.session = ui.Session(self.repo, str(self.receipt))
        self.svg = (FIXTURES / 'annotated-timeline.svg').read_text()
        self.map = ui.parse_map(self.svg)

    def diagram(self, svg=None):
        folder = self.receipt.parent / 'diagram'
        folder.mkdir(exist_ok=True)
        path = folder / (self.receipt.stem + '_handoff-session-timeline.svg')
        path.write_text(self.svg if svg is None else svg)
        return path

    def payload(self):
        with patch.object(ui.transcript, '_is_ignored', return_value=True):
            return self.session.payload('fixture-token')

    def test_piecewise_boundary_gap_and_nonzero_origin(self):
        rects = ui.rectangles(self.map['lanes'][1], self.map)
        self.assertEqual(rects, [dict(left=30, top=25, width=20, height=12.5),
                                 dict(left=60, top=25, width=20, height=12.5)])
        lane = dict(self.map['lanes'][1], t0=ui.timestamp('2026-09-10T00:11:00Z'),
                    t1=ui.timestamp('2026-09-10T00:19:00Z'))
        self.assertEqual(ui.rectangles(lane, self.map), [])
        contiguous = copy.deepcopy(self.map)
        contiguous['axis'][1]['t0'] = contiguous['axis'][0]['t1']
        rects = ui.rectangles(self.map['lanes'][1], contiguous)
        self.assertEqual(len(rects), 2)
        self.assertEqual(rects[0]['width'], 20)
        self.assertEqual(rects[1]['width'], 30)
        lane['t0'], lane['t1'] = self.map['axis'][0]['t1'], self.map['axis'][1]['t0']
        self.assertEqual(ui.rectangles(lane, self.map), [])

    def test_reconciliation_tolerance_and_per_lane_failure(self):
        jobs = self.session.jobs()
        result = ui.reconcile(self.map, jobs)
        self.assertEqual(result['banner'], 'Lane map matches the 2 indexed jobs')
        self.assertEqual(len(result['hotspots']), 5)
        for key in ('t0', 't1'):
            for delta in (-60, 60):
                changed = copy.deepcopy(self.map)
                changed['lanes'][1][key] += delta
                self.assertNotIn('differs', ui.reconcile(changed, jobs)['banner'])
            for delta in (-61, 61):
                changed = copy.deepcopy(self.map)
                changed['lanes'][1][key] += delta
                result = ui.reconcile(changed, jobs)
                self.assertIn('job-a: declared window differs', result['banner'])
                self.assertEqual({r['job'] for r in result['hotspots']}, {None, 'job-b'})

    def test_unknown_missing_running_and_unmeasured_lanes(self):
        changed = copy.deepcopy(self.map)
        changed['lanes'][1]['job'] = 'unknown'
        result = ui.reconcile(changed, self.session.jobs())
        self.assertIn('unknown: unknown lane', result['banner'])
        self.assertIn('job-a: missing lane', result['banner'])
        self.session.receipt['jobs'][0]['running'] = True
        jobs = self.session.jobs()
        self.assertEqual(jobs[0]['state'], 'running')
        self.assertIsNone(jobs[0]['t1'])
        self.assertEqual(jobs[0]['duration'], 'not recorded')
        result = ui.reconcile(self.map, jobs)
        self.assertIn('job-a: unverifiable (job running)', result['banner'])
        self.assertEqual({r['job'] for r in result['hotspots']}, {None, 'job-b'})
        jobs[0]['state'], jobs[0]['t0'] = 'failed', None
        self.assertIn('window not recorded', ui.reconcile(self.map, jobs)['banner'])

    def test_reconciled_window_in_gap_is_named_not_clamped(self):
        lane = dict(self.map['lanes'][1], t0=ui.timestamp('2026-09-10T00:11:00Z'),
                    t1=ui.timestamp('2026-09-10T00:19:00Z'))
        self.map['lanes'][1] = lane
        jobs = self.session.jobs()
        jobs[0].update(t0=lane['t0'], t1=lane['t1'])
        result = ui.reconcile(self.map, jobs)
        self.assertIn('job-a: outside the drawn axis', result['banner'])
        self.assertNotIn('job-a', {rect['job'] for rect in result['hotspots']})

    def test_all_map_validity_rules_drop_whole_map_to_second_rung(self):
        root = ET.fromstring(self.svg)
        axis = json.loads(root.attrib['data-axis'])
        lanes = json.loads(root.attrib['data-lanes'])
        mutations = [
            ('unordered time', 'axis', lambda a: a.reverse()),
            ('overlapping time', 'axis', lambda a: a[1].update(t0='2026-09-10T00:09:00Z')),
            ('unordered x', 'axis', lambda a: a[1].update(x0=0, x1=100)),
            ('overlapping x', 'axis', lambda a: a[1].update(x0=599)),
            ('zero x', 'axis', lambda a: a[0].update(x1=200)),
            ('negative x', 'axis', lambda a: a[0].update(x1=199)),
            ('zero time', 'axis', lambda a: a[0].update(t1=a[0]['t0'])),
            ('reversed time', 'axis', lambda a: a[0].update(t1='2026-09-09T23:00:00Z')),
            ('infinite x', 'axis', lambda a: a[0].update(x0=float('inf'))),
            ('nan x', 'axis', lambda a: a[0].update(x1=float('nan'))),
            ('naive axis', 'axis', lambda a: a[0].update(t0='2026-09-10T00:00:00')),
            ('duplicate job', 'lanes', lambda a: a[2].update(job='job-a')),
            ('overlapping bands', 'lanes', lambda a: a[2].update(y0=199)),
            ('zero band', 'lanes', lambda a: a[2].update(y1=250)),
            ('nonfinite y', 'lanes', lambda a: a[2].update(y1=float('inf'))),
            ('naive lane', 'lanes', lambda a: a[1].update(t1='2026-09-10T00:25:00')),
            ('missing window', 'lanes', lambda a: a[1].pop('t0')),
            ('reversed lane window', 'lanes', lambda a: a[1].update(t1=a[1]['t0'])),
            ('driver start window', 'lanes', lambda a: a[0].update(t0='2026-09-10T00:00:00Z')),
            ('driver end window', 'lanes', lambda a: a[0].update(t1='2026-09-10T00:00:00Z')),
            ('duplicate driver', 'lanes', lambda a: a.append(dict(lane='driver', y0=350, y1=400))),
            ('driver job', 'lanes', lambda a: a[0].update(job='job-c')),
            ('unknown lane kind', 'lanes', lambda a: a[0].update(lane='other')),
        ]
        for name, target, mutate in mutations:
            with self.subTest(name=name):
                current = copy.deepcopy(root)
                value = copy.deepcopy(axis if target == 'axis' else lanes)
                mutate(value)
                current.set('data-' + target, json.dumps(value))
                self.diagram(ET.tostring(current, encoding='unicode'))
                self.assertIsNone(ui.parse_map(ET.tostring(current)))
                payload = self.payload()
                self.assertFalse(payload['overview']['mapped'])
                self.assertEqual(payload['overview']['hotspots'], [])
                self.assertIn('declares no lane map', payload['overview']['banner'])
                self.assertIsNotNone(payload['overview']['image'])
        for box in ('0 0 0 100', '0 0 100 -1', '0 0 inf 100', '0 0 100', 'nan 0 100 100'):
            with self.subTest(box=box):
                root.set('viewBox', box)
                self.assertIsNone(ui.parse_map(ET.tostring(root)))
                self.diagram(ET.tostring(root, encoding='unicode'))
                self.assertFalse(self.payload()['overview']['mapped'])
                self.assertIn('declares no lane map', self.payload()['overview']['banner'])

    def test_svg_parsing_and_all_fallback_rungs(self):
        self.assertIsNotNone(self.map)
        for svg in ('<svg/>', '<svg', '<svg data-axis="oops"/>'):
            self.assertIsNone(ui.parse_map(svg))
        payload = self.payload()
        self.assertIsNone(payload['overview']['image'])
        for expected in (str(self.receipt), 'baoyu-diagram', 'data-axis', 'data-lanes', 'viewBox', 'exit_code', '60 seconds'):
            self.assertIn(expected, payload['prompt'])
        self.diagram('<svg xmlns="http://www.w3.org/2000/svg"/>')
        self.assertFalse(self.payload()['overview']['mapped'])
        self.diagram()
        self.assertTrue(self.payload()['overview']['mapped'])

    def test_job_guard_and_same_named_cwd_never_wins(self):
        for job_id in ('../job-a', '.', '..', '/job-a', 'job-a/x', 'job-a\\x', 'last', 'a', 'unknown'):
            with self.subTest(job_id=job_id), self.assertRaises(ValueError):
                self.session.job_dir(job_id)
        outside = self.repo / 'outside'
        outside.mkdir()
        sibling = outside / 'job-a'
        sibling.mkdir()
        (sibling / 'prompt.md').write_text('WRONG CWD JOB')
        old_cwd = Path.cwd()
        try:
            os.chdir(outside)
            with patch.object(ui.transcript, 'main', wraps=ui.transcript.main) as render, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                body = self.session.render_transcript('job-a')
            argument = Path(render.call_args.args[0][0])
            self.assertTrue(argument.is_absolute())
            self.assertEqual(argument, self.repo / '.handoff/jobs/job-a')
            self.assertIn(b'fixture prompt', body)
            self.assertNotIn(b'WRONG CWD JOB', body)
        finally:
            os.chdir(old_cwd)
        job = self.repo / '.handoff/jobs/job-a'
        job.rename(job.with_name('original'))
        job.symlink_to(sibling, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.session.render_transcript('job-a')

    def test_cost_renderer_receives_absolute_validated_receipt(self):
        with patch.object(ui.cost, 'main', wraps=ui.cost.main) as render, contextlib.redirect_stdout(io.StringIO()):
            self.assertIn(b'Handoff', self.session.render_cost())
        self.assertEqual(render.call_args.args[0][0], str(self.receipt.resolve()))

    def serve(self):
        server = ui.ThreadingHTTPServer(('127.0.0.1', 0), ui.make_handler(self.session, 'fixture-token'))
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def close():
            server.shutdown()
            server.server_close()
            thread.join()
        self.addCleanup(close)
        return server

    def request(self, server, path, host=None):
        connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
        connection.request('GET', path, headers={'Host': host} if host else {})
        response = connection.getresponse()
        result = response.status, dict(response.getheaders()), response.read()
        connection.close()
        return result

    def test_actual_route_headers_and_authentication(self):
        self.diagram()
        server = self.serve()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for path, policy in (('/', 'shell'), ('/diagram.svg', 'svg'), ('/transcript/job-a', 'frame'), ('/cost-receipt', 'frame')):
                with self.subTest(path=path):
                    status, headers, _ = self.request(server, path + '?token=fixture-token')
                    self.assertEqual(status, 200)
                    self.assertEqual(headers['Content-Security-Policy'], ui.CSP[policy])
                    self.assertEqual(headers['Cache-Control'], 'no-store')
                    self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
                    directives = headers['Content-Security-Policy'].split('; ')
                    if policy == 'svg':
                        self.assertFalse(any(d.startswith('script-src') for d in directives))
                    elif policy == 'frame':
                        self.assertIn("img-src 'self' data:", directives)
                        self.assertIn("frame-ancestors 'self'", directives)
                        self.assertNotEqual(headers.get('X-Frame-Options'), 'DENY')
                    else:
                        self.assertIn("frame-src 'self'", directives)
                        self.assertIn("img-src 'self'", directives)
            for path in ('/', '/diagram.svg', '/transcript/job-a', '/cost-receipt'):
                self.assertEqual(self.request(server, path)[0], 403)
                self.assertEqual(self.request(server, path + '?token=wrong')[0], 403)
                for host in ('evil.example', '127.0.0.1:1', 'localhost:123.evil'):
                    self.assertEqual(self.request(server, path + '?token=fixture-token', host)[0], 403)
            for path in ('/transcript/unknown', '/transcript/%2E%2E', '/transcript/job-a%2Fx'):
                self.assertEqual(self.request(server, path + '?token=fixture-token')[0], 400)

    def _main(self, *extra):
        """Run main() without ever letting it bind a port or open a browser."""
        out, err = io.StringIO(), io.StringIO()
        argv = [str(self.receipt), '--repo', str(self.repo), '--no-open', *extra]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            with patch.object(ui, 'ThreadingHTTPServer', side_effect=OSError('bind blocked')):
                with patch.object(ui.transcript, '_is_ignored', return_value=True):
                    code = ui.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_missing_diagram_refuses_to_open_and_hands_over_the_prompt(self):
        # No diagram written: the gate must stop before any server or browser.
        code, out, err = self._main()
        self.assertEqual(code, 3)
        self.assertIn('no timeline diagram', err)
        self.assertIn(str(self.session.diagram_target()), err)
        self.assertIn('--allow-missing-diagram', err)
        # The prompt is handed over whole, carrying the requirements a generated
        # diagram has to satisfy for the lane map to reconcile.
        for requirement in ('data-axis', 'data-lanes', 'light theme', 'minimum font size of 12px',
                            'local timezone', 'sub-timeline', 'within 60 seconds'):
            self.assertIn(requirement, err)
        self.assertNotIn('Handoff session: http', out)
        # Nothing was opened, so nothing was rendered either.
        self.assertFalse((self.repo / '.handoff/cost-receipts').exists())

    def test_allow_missing_diagram_opens_on_the_job_table_rung(self):
        code, out, err = self._main('--allow-missing-diagram')
        # Reaches the bind, which the patch refuses: the gate let it through.
        self.assertEqual(code, 2)
        self.assertIn('bind blocked', err)
        self.assertIn('timeline diagram: none', out)
        self.assertIn('cost receipt: rendered', out)

    def test_cost_receipt_is_rendered_before_the_page_is_served(self):
        self.diagram()
        code, out, err = self._main()
        self.assertEqual(code, 2)
        self.assertIn('bind blocked', err)
        self.assertIn('timeline diagram:', out)
        self.assertIn('cost receipt: rendered', out)
        # On disk before any request, not on first click of the tab.
        written = sorted(p.name for p in (self.repo / '.handoff/cost-receipts').iterdir())
        self.assertEqual(written, ['cost-receipt-20260910T003000Z.html',
                                   'cost-receipt-20260910T003000Z.md'])
        # Ordering matters: the assets are reported before the URL would be.
        self.assertLess(out.index('cost receipt: rendered'), len(out))

    def test_a_cost_receipt_that_cannot_render_blocks_the_page(self):
        self.diagram()
        with patch.object(ui.Session, 'render_cost', side_effect=OSError('log unreadable')):
            code, out, err = self._main()
        self.assertEqual(code, 2)
        self.assertIn('cannot render the cost receipt', err)
        self.assertIn('log unreadable', err)
        self.assertNotIn('cost receipt: rendered', out)

    def test_favicon_is_a_no_content_route_that_needs_no_token(self):
        self.diagram()
        server = self.serve()
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            for path in ('/favicon.ico', '/favicon.ico?token=wrong'):
                status, headers, body = self.request(server, path)
                self.assertEqual(status, 204)
                self.assertEqual(body, b'')
                self.assertEqual(headers['Content-Security-Policy'], ui.CSP['shell'])
            # The exemption is this one path and nothing near it.
            for path in ('/favicon.ico/../', '/favicon.icox', '/FAVICON.ICO'):
                self.assertEqual(self.request(server, path)[0], 403)

    def test_every_emitted_subresource_has_token_and_payload_is_safe(self):
        self.diagram()
        (self.repo / '.handoff/goal.md').write_text((FIXTURES / 'goal.md').read_text() + '\n</script><img onerror=bad>')
        payload = self.payload()
        urls = [payload['cost_url'], payload['overview']['image'], *(j['url'] for j in payload['jobs'])]
        self.assertTrue(all('?token=fixture-token' in url for url in urls))
        html = ui.inject(ui.TEMPLATE.read_text(), payload)
        self.assertNotIn('</script><img onerror=bad>', html)
        self.assertIn('\\u003c/script>', html)
        # All network sinks in the shell use these authenticated payload fields.
        script = ui.TEMPLATE.read_text()
        sources = [line.strip() for line in script.splitlines() if '.src =' in line]
        self.assertEqual(len(sources), 3)
        self.assertTrue(any('data.cost_url' in line for line in sources))
        self.assertTrue(any('job.url' in line for line in sources))
        self.assertTrue(any('data.overview.image' in line for line in sources))
        for forbidden in ('innerHTML', 'outerHTML', 'insertAdjacentHTML', '<object', '<embed', 'src="/'):
            self.assertNotIn(forbidden, script)

    def test_shipped_shell_behavior_with_real_payloads(self):
        self.session.receipt['fields']['checks'] = '<img onerror=bad>'
        for rung in ('missing', 'unmapped', 'mapped'):
            if rung == 'unmapped':
                self.diagram('<svg/>')
            elif rung == 'mapped':
                self.diagram()
            for matched in (True, False):
                with self.subTest(rung=rung, matched=matched):
                    payload = self.payload()
                    payload['goal']['matched'] = matched
                    result = subprocess.run(['node', str(FIXTURES / 'check-shell.cjs')],
                                            input=json.dumps({'html': ui.TEMPLATE.read_text(), 'payload': payload}),
                                            text=True, capture_output=True)
                    self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
                    self.assertIn('text-only evidence: OK', result.stdout)

    def test_goal_matched_unmatched_and_unparseable(self):
        goal = self.repo / '.handoff/goal.md'
        result = ui.read_goal(goal, {'job-a'})
        self.assertTrue(result['matched'])
        self.assertEqual(result['tasks']['rows'][0][-1], 'job-a')
        result = ui.read_goal(goal, {'old-job'})
        self.assertFalse(result['matched'])
        self.assertIn('Review the fixture run.', result['sections']['Goal'])
        goal.write_text('## Goal\njob-a mentioned in prose only\n## Tasks\nnot a table with job-a')
        result = ui.read_goal(goal, {'job-a'})
        self.assertFalse(result['matched'])
        self.assertIsNone(result['tasks'])
        self.assertEqual(result['sections']['Tasks'], 'not a table with job-a')
        template = ui.TEMPLATE.read_text()
        self.assertIn('no goal file matches this receipt', template)
        self.assertIn('Current working-tree goal', template)
        self.assertIn("add($('goal'), 'details')", template)

    def test_denials_all_backends_join_missing_start_and_terminal_result(self):
        job = self.session.job_dir('job-a')
        shutil.copyfile(FIXTURES / 'copilot.jsonl', job / 'log.jsonl')
        result = ui.denials(job, 'copilot')
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(result['items'][0]['tool'], 'shell')
        self.assertEqual(result['items'][0]['command'], {'command': 'echo fixture'})
        self.assertEqual(result['items'][1]['command'], 'details unavailable')
        self.assertEqual(result['items'][1]['toolCallId'], 'missing')
        self.assertEqual(ui.denials(job, 'claude')['state'], 'not yet available')
        self.assertEqual(ui.denials(job, 'codex'), {'state': 'not reported (codex)', 'items': []})
        events = [{'type': 'result', 'permission_denials': [{'tool_name': 'wrong'}]},
                  {'type': 'result', 'permission_denials': [{'tool_name': 'Bash', 'tool_input': {'command': 'echo fixture'}}]}]
        (job / 'log.jsonl').write_text('\n'.join(json.dumps(e) for e in events))
        self.assertEqual(ui.denials(job, 'claude')['items'], [{'tool': 'Bash', 'command': {'command': 'echo fixture'}}])
        (job / 'log.jsonl').write_text('{"type":"result","permission_denials":[]}\n')
        self.assertEqual(ui.denials(job, 'claude'), {'state': 'reported', 'items': []})

    def test_malformed_roles_and_derived_anomalies(self):
        for raw in (None, 'oops', '{}', 'null'):
            self.assertEqual(ui.roles_used(raw), 'not recorded')
        valid = {'role': 'r', 'host': 'codex', 'model': 'm', 'effort': 'high', 'verified': False}
        self.assertEqual(ui.roles_used(json.dumps([valid, {'verified': 'yes'}])), [valid, 'not recorded'])
        self.session.receipt['fields']['roles_used'] = 'oops'
        job = self.session.job_dir('job-a')
        (job / 'exit_code').write_text('9')
        (job / 'stderr.log').write_text('fixture error')
        (job / 'cancelled').touch()
        payload = self.payload()
        self.assertEqual(payload['facts']['roles_used'], 'not recorded')
        self.assertEqual(payload['anomalies'], ['job-a: cancelled', 'job-a: exit code 9', 'job-a: non-empty stderr.log'])
        with patch.object(ui.transcript, '_is_ignored', return_value=False):
            self.assertEqual(len(self.session.payload('token')['warnings']), 2)

    def test_transcript_cache_invalidates_every_input_creation_deletion_and_mtime(self):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()), patch.object(ui.transcript, 'main', wraps=ui.transcript.main) as render:
            self.session.render_transcript('job-a')
            self.session.render_transcript('job-a')
            self.assertEqual(render.call_count, 1)
            job = self.session.job_dir('job-a')
            for name in ui.JOB_INPUTS:
                path = job / name
                if not path.exists():
                    path.write_text('0' if name == 'pid' else '')
                before = render.call_count
                os.utime(path, ns=(time.time_ns(), time.time_ns()))
                self.session.render_transcript('job-a')
                self.assertEqual(render.call_count, before + 1, name)
                self.session.render_transcript('job-a')
                self.assertEqual(render.call_count, before + 1, name)
            (job / 'prompt.md').unlink()
            before = render.call_count
            self.session.render_transcript('job-a')
            self.assertEqual(render.call_count, before + 1)

    def test_cost_cache_invalidates_all_inputs_including_driver_and_usage(self):
        projects = self.repo / 'projects'
        driver = projects / 'project' / 'session.jsonl'
        driver.parent.mkdir(parents=True)
        driver.write_text('')
        self.receipt.write_text(self.receipt.read_text().replace('claude_session: none', 'claude_session: session'))
        self.session = ui.Session(self.repo, str(self.receipt))
        output = self.session.output_path('cost-receipts', 'cost-receipt-20260910T003000Z.html')
        def render(_argv):
            output.parent.mkdir(exist_ok=True)
            output.write_text('complete page')
            return 0
        with patch.object(ui.cost, 'DEFAULT_PROJECTS', projects), patch.object(ui.cost, 'main', side_effect=render) as renderer:
            self.session.render_cost()
            self.session.render_cost()
            self.assertEqual(renderer.call_count, 1)
            paths = self.session.cost_inputs()
            self.assertIn(driver, paths)
            self.assertIn(self.receipt, paths)
            for path in paths:
                if path == ui.cost.DEFAULT_TEMPLATE:
                    continue
                if not path.is_file():
                    path.write_text('')
                before = renderer.call_count
                os.utime(path, ns=(time.time_ns(), time.time_ns()))
                self.session.render_cost()
                self.assertEqual(renderer.call_count, before + 1, path.name)
            usage = self.session.job_dir('job-a') / 'usage.json'
            usage.unlink()
            before = renderer.call_count
            self.session.render_cost()
            self.assertEqual(renderer.call_count, before + 1)

    def test_template_mtime_and_output_age_invalidate_cache(self):
        template = self.repo / 'template.html'
        template.write_text(ui.transcript.DEFAULT_TEMPLATE.read_text())
        with patch.object(ui.transcript, 'DEFAULT_TEMPLATE', template), patch.object(ui.transcript, 'main', wraps=ui.transcript.main) as render, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.session.render_transcript('job-a')
            template.write_text(template.read_text() + '\n<!-- updated -->')
            self.assertIn(b'<!-- updated -->', self.session.render_transcript('job-a'))
            self.assertEqual(render.call_count, 2)
            output = self.session.output_path('transcripts', 'job-a.html')
            os.utime(output, (1, 1))
            self.session.render_transcript('job-a')
            self.assertEqual(render.call_count, 3)
        template.write_text(ui.cost.DEFAULT_TEMPLATE.read_text())
        with patch.object(ui.cost, 'DEFAULT_TEMPLATE', template), patch.object(ui.cost, 'main', wraps=ui.cost.main) as render, contextlib.redirect_stdout(io.StringIO()):
            self.session.render_cost()
            template.write_text(template.read_text() + '\n<!-- updated -->')
            self.assertIn(b'<!-- updated -->', self.session.render_cost())
            self.assertEqual(render.call_count, 2)

    def test_one_lock_per_output_held_through_render_and_read(self):
        output = self.repo / 'cache.html'
        other = self.repo / 'other.html'
        entered = threading.Event()
        release = threading.Event()
        def render():
            output.write_text('half')
            entered.set()
            self.assertTrue(release.wait(5))
            output.write_text('whole')
            return 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(self.session.cached, output, lambda: [], render)
            self.assertTrue(entered.wait(5))
            second = pool.submit(self.session.cached, output, lambda: [], render)
            other_result = pool.submit(self.session.cached, other, lambda: [], lambda: (other.write_text('other') and 0))
            self.assertEqual(other_result.result(5), b'other')
            self.assertFalse(second.done())
            release.set()
            self.assertEqual(first.result(5), b'whole')
            self.assertEqual(second.result(5), b'whole')
        # A read must still own the same lock, not merely follow a locked render.
        original = Path.read_bytes
        def read(path):
            if path == output:
                self.assertFalse(self.session.locks[output].acquire(blocking=False))
            return original(path)
        with patch.object(Path, 'read_bytes', read):
            self.assertEqual(self.session.cached(output, lambda: [], render), b'whole')


if __name__ == '__main__':
    unittest.main()
