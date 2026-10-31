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
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from handoff_runtime import (  # noqa: E402
    PAYLOAD_PATTERN, EvidencePathError, inject, job_state, read_meta, transcript_path)

RESERVED_LAST = "last"
DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "transcript-viewer.html"


class Ambiguous(Exception):
    def __init__(self, candidates: list[Path]):
        super().__init__("more than one job matches")
        self.candidates = candidates


class NotFound(Exception):
    pass


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


def _is_ignored(path: Path) -> bool:
    try:
        done = subprocess.run(["git", "check-ignore", "-q", str(path)],
                              cwd=next(p for p in path.parents if p.is_dir()),
                              capture_output=True)
    except OSError:
        return False
    return done.returncode == 0


def render(job_dir: Path, out_path: Path, template_text: str) -> bool:
    """Write `job_dir`'s page to `out_path` and say whether Git ignores it.

    Split out of main so a caller that has already fixed the destination, as the
    session page does, writes exactly that file and not whatever a second lookup
    would pick. It creates the parent folder itself for the same reason.
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_payload(job_dir)
    payload["output_is_ignored"] = _is_ignored(out_path)
    out_path.write_text(inject(template_text, payload), encoding="utf-8")
    return payload["output_is_ignored"]


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

    # The destination is computed once, here, and handed to render(): the page
    # goes to the session of the newest receipt indexing this job (else the
    # current Claude Code session, else none), the same place the session page
    # writes. An unreadable receipt is an error too: skipping it could file the
    # page under an older session.
    try:
        out_path = transcript_path(args.repo, job_dir.name)
    except (EvidencePathError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2
    ignored = render(job_dir, out_path, template)

    if not ignored:
        print(f"WARNING: {out_path} is not ignored by Git. A transcript embeds "
              f"captured command output; do not commit it.", file=sys.stderr)

    print(out_path)
    if not args.no_open:
        webbrowser.open(out_path.resolve().as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
