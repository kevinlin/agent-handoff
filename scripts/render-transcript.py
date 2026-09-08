#!/usr/bin/env python3
"""Render a delegated job's log.jsonl as one self-contained HTML page.

The page in assets/transcript-viewer.html is the whole viewer; this script
only locates a job, reads its state files, and injects a payload. All log
parsing lives in the page's JavaScript so that dropping a log file onto the
same page needs no second parser in another language.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

RESERVED_LAST = "last"
DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "transcript-viewer.html"
PAYLOAD_PATTERN = re.compile(
    r'(<script id="handoff-payload" type="application/json">)(.*?)(</script>)', re.S)


class Ambiguous(Exception):
    def __init__(self, candidates: list[Path]):
        super().__init__("more than one job matches")
        self.candidates = candidates


class NotFound(Exception):
    pass


def read_meta(job_dir: Path) -> dict[str, str]:
    """Parse a job's `key=value` meta file. A missing file is not an error:
    resumed jobs legitimately omit backend and role."""
    meta: dict[str, str] = {}
    path = job_dir / "meta"
    if not path.is_file():
        return meta
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            meta[key.strip()] = value.strip()
    return meta


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError) as error:
        return isinstance(error, PermissionError)
    return True


def job_state(job_dir: Path) -> str:
    """Mirror delegate-codex.sh job_state(). Absence of exit_code is never
    RUNNING on its own: a worker that died without writing one is FAILED."""
    if (job_dir / "cancelled").is_file():
        return "CANCELLED"
    exit_code = job_dir / "exit_code"
    if exit_code.is_file():
        return "DONE" if exit_code.read_text(encoding="utf-8").strip() == "0" else "FAILED"
    pid_file = job_dir / "pid"
    if pid_file.is_file():
        raw = pid_file.read_text(encoding="utf-8").strip()
        if raw.isdigit() and _pid_alive(int(raw)):
            return "RUNNING"
    return "FAILED"


def list_jobs(repo: Path) -> list[Path]:
    root = repo / ".handoff" / "jobs"
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def _sort_key(job: Path) -> tuple[str, float, str]:
    # submitted_at has one-second precision, so ties break on mtime then name.
    return (read_meta(job).get("submitted_at", ""), job.stat().st_mtime, job.name)


def resolve(repo: Path, argument: str | None) -> Path:
    """Resolve an identifier to a job directory. Order matters: the reserved
    `last` selector is checked before label matching so a job labelled `last`
    cannot intercept it."""
    jobs = list_jobs(repo)

    if argument and ("/" in argument or Path(argument).is_dir()):
        candidate = Path(argument).expanduser()
        if not candidate.is_dir():
            raise NotFound(f"not a directory: {argument}")
        return candidate

    if not jobs:
        raise NotFound(f"no jobs under {repo / '.handoff' / 'jobs'}")

    if argument is None or argument == RESERVED_LAST:
        return max(jobs, key=_sort_key)

    exact = [j for j in jobs if j.name == argument]
    if exact:
        return exact[0]

    by_pid: list[Path] = []
    if argument.isdigit():
        for job in jobs:
            pid_file = job / "pid"
            if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip() == argument:
                by_pid.append(job)

    by_suffix = [j for j in jobs if j.name.endswith(argument) or argument in j.name]

    # A digit argument matching both a pid and a label is a real collision;
    # resolving it by step order is exactly the silent-wrong-job case.
    combined = list(dict.fromkeys(by_pid + by_suffix))
    if len(combined) > 1:
        raise Ambiguous(sorted(combined))
    if combined:
        return combined[0]
    raise NotFound(f"no job matches {argument!r}")


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def build_payload(job_dir: Path) -> dict:
    meta = read_meta(job_dir)
    log = job_dir / "log.jsonl"
    prompt = job_dir / "prompt.md"
    exit_code_file = job_dir / "exit_code"
    return {
        "job_id": job_dir.name,
        "meta": meta,
        # The prompt is the job's first user message; the log itself never
        # carries it, so the page cannot reconstruct it from log_text.
        "prompt_text": prompt.read_text(encoding="utf-8", errors="replace")
        if prompt.is_file() else "",
        "log_text": log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "",
        "state": job_state(job_dir),
        "exit_code": exit_code_file.read_text(encoding="utf-8").strip()
        if exit_code_file.is_file() else None,
        "exit_code_mtime": _iso(exit_code_file.stat().st_mtime)
        if exit_code_file.is_file() else None,
        "log_mtime": _iso(log.stat().st_mtime) if log.is_file() else None,
        "generated_at": _iso(datetime.now(timezone.utc).timestamp()),
        "parent_job_id": meta.get("parent"),
    }


def inject(template: str, payload: dict) -> str:
    """Replace the payload slot. Escaping `<` as \\u003c keeps any closing-script
    sequence inside captured output from ending the element early. This guards
    the element boundary only; DOM insertion is the page's own boundary."""
    if not PAYLOAD_PATTERN.search(template):
        raise ValueError("template has no handoff-payload slot")
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    # A lambda, not a replacement string: re.sub interprets backslashes in a
    # replacement, and the encoded payload is full of them.
    return PAYLOAD_PATTERN.sub(
        lambda m: m.group(1) + encoded + m.group(3), template, count=1)


def _is_ignored(path: Path) -> bool:
    try:
        done = subprocess.run(["git", "check-ignore", "-q", str(path)],
                              cwd=path.parent, capture_output=True)
    except OSError:
        return False
    return done.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job", nargs="?", default=None,
                        help="jobId, job folder path, pid, remembered label, or 'last'.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="Repository root (default: current directory).")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                        help="Viewer template (default: the skill's assets copy).")
    parser.add_argument("--no-open", action="store_true",
                        help="Write the page and print its path instead of opening it.")
    args = parser.parse_args(argv)

    try:
        job_dir = resolve(args.repo, args.job)
    except Ambiguous as error:
        print("more than one job matches; name one of these exactly:", file=sys.stderr)
        for candidate in error.candidates:
            print(f"  {candidate.name}", file=sys.stderr)
        return 2
    except NotFound as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    try:
        template = args.template.read_text(encoding="utf-8")
    except OSError as error:
        print(f"ERROR: cannot read template: {error}", file=sys.stderr)
        return 2

    out_dir = args.repo / ".handoff" / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{job_dir.name}.html"
    payload = build_payload(job_dir)
    payload["output_is_ignored"] = _is_ignored(out_path)
    out_path.write_text(inject(template, payload), encoding="utf-8")

    if not payload["output_is_ignored"]:
        print(f"WARNING: {out_path} is not ignored by Git. A transcript embeds "
              f"captured command output; do not commit it.", file=sys.stderr)

    print(out_path)
    if not args.no_open:
        webbrowser.open(out_path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
