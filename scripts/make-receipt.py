#!/usr/bin/env python3
"""Generate a valid Handoff Session Receipt.

Receipts written by hand drift in format and invite optimistic guesses.
This tool builds the receipt from arguments, validates the result with
scripts/validate-receipt.py logic before printing, and persists it under
the target repo's .handoff/sessions/<date>_<claude_session>/ unless --no-save
says otherwise. The folder is looked up by session id: a session that already
has one keeps it, and a new one takes the date of the receipt's own stamp.

Usage:
    python3 make-receipt.py --start --repo PATH        # Phase 0: stamp the start
    python3 make-receipt.py --phase "final fix" [--claude-session ID] \
        --checks "npm test; bash lint.sh" --codex-jobs 2 --cc-jobs 1 \
        --copilot-jobs 1 --cursor-jobs 1 --antigravity-jobs 1 \
        [--scope project] [--config-source project] [--roles-used '[]'] \
        [--anomalies none] [--started-at ISO8601] [--ended-at ISO8601] \
        [--no-save] [--repo PATH]

The session id is read, not recalled: Claude Code sets CLAUDE_CODE_SESSION_ID in
every Bash tool subprocess, so with --claude-session omitted the receipt records
that value. An explicit id wins, which is how a past run's receipt is
regenerated from another session. `--claude-session none` is for a run no Claude
Code session drove, and is refused while the variable is set; an omitted flag
with the variable unset is refused too. The id is part of the session folder's
name, so it is checked as a path segment before anything is printed.

Tip: get --codex-jobs, --cc-jobs, --copilot-jobs, --cursor-jobs and
--antigravity-jobs from the job directories under <repo>/.handoff/jobs/ instead
of recalling how many were submitted; each job's meta names the backend that
executed it.

Duration is wall clock: --start writes <repo>/.handoff/session-start, and the
receipt run measures against it, so time blocked on a human approval counts.
--ended-at pins the far end instead of the current clock, which is what lets a
past run's receipt be regenerated without the elapsed time growing to today.
Per-job durations come from each job's meta submitted_at and the mtime of its
exit_code, partitioned by that job's backend; only jobs submitted inside the
[start, end] window count, so an earlier or later run is left out.

The declared --codex-jobs, --cc-jobs, --copilot-jobs, --cursor-jobs and
--antigravity-jobs counts are checked against those measured durations and a
mismatch refuses to emit. A count that disagrees with the job directories means
the window is wrong -- most often a session-start marker stamped after the run's
first job -- and a receipt that indexes fewer jobs than it counts is the one
failure the downstream cost renderer cannot work around.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from handoff_runtime import (  # noqa: E402
    SESSION_ENV, EvidencePathError, legacy_layout_note, session_path)


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_receipt", SCRIPT_DIR / "validate-receipt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def marker_path(repo: str) -> Path:
    return Path(repo).resolve() / ".handoff" / "session-start"


def parse_iso(text: str) -> datetime:
    """Parse an ISO 8601 UTC stamp; 3.9's fromisoformat rejects a literal Z."""

    parsed = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def format_duration(seconds: float) -> str:
    whole = int(max(0.0, seconds))
    return f"{whole // 60}min {whole % 60:02d}sec"


BACKENDS = ("codex", "claude", "copilot", "cursor", "antigravity")


def job_durations(repo: str, started: datetime, ended: datetime) -> dict[str, str]:
    """Per-job wall clock from job state, oldest first, split by backend.

    Keyed by BACKENDS. A job directory written before backend dispatch carries
    no `backend=` line and is codex by construction, as is one naming a backend
    this version does not know.
    """

    measured: dict[str, list[tuple[datetime, str]]] = {b: [] for b in BACKENDS}
    for meta in Path(repo).resolve().glob(".handoff/jobs/job-*/meta"):
        text = meta.read_text(encoding="utf-8")
        found = re.search(r"^submitted_at=(.+)$", text, re.M)
        if not found:
            continue
        submitted = parse_iso(found.group(1))
        if not started <= submitted <= ended:
            continue
        exit_code = meta.with_name("exit_code")
        value = (
            format_duration(exit_code.stat().st_mtime - submitted.timestamp())
            if exit_code.is_file()
            else "running"
        )
        backend = re.search(r"^backend=(.+)$", text, re.M)
        named = backend.group(1).strip() if backend else ""
        bucket = named if named in BACKENDS else "codex"
        measured[bucket].append((submitted, f"{meta.parent.name}={value}"))

    return {
        backend: "; ".join(entry for _, entry in sorted(entries)) or "none"
        for backend, entries in measured.items()
    }


def multiline_fields(fields: dict[str, str]) -> list[str]:
    """Values carrying a newline, which serialize into an unreadable receipt.

    The receipt is one field per line, so a value containing a newline splits
    into a line extract_block() cannot parse and every field after it is lost.
    Field validation passes -- the value itself is fine -- so this is checked
    here, before anything is printed or written.
    """

    return [
        f"{key} contains a newline; the receipt is one line per field, so this "
        "value would truncate the block. Join it onto a single line."
        for key, value in fields.items()
        if "\n" in value or "\r" in value
    ]


def count_mismatches(fields: dict[str, str]) -> list[str]:
    """Declared job counts that disagree with the measured durations."""

    failures = []
    for backend in BACKENDS:
        count_field = "cc_jobs" if backend == "claude" else f"{backend}_jobs"
        durations_field = count_field.replace("_jobs", "_job_durations")
        declared = fields[count_field].strip()
        if not declared.isdigit():
            continue  # validate_receipt already reports a non-integer count
        value = fields[durations_field].strip()
        measured = 0 if value == "none" else len([e for e in value.split(";") if e.strip()])
        if int(declared) != measured:
            failures.append(
                f"{count_field} is {declared} but {durations_field} has {measured} "
                "measured entries; the session window is excluding jobs this run "
                "submitted -- check .handoff/session-start against the job "
                "directories, or pass --started-at/--ended-at"
            )
    return failures


def resolve_session(flag: str | None, inside: str) -> tuple[str | None, str | None]:
    """The session id to record, or the reason there is none: (id, failure).

    `inside` is CLAUDE_CODE_SESSION_ID, "" when unset or empty. `none` while it
    is set is refused rather than corrected: a driver still typing it is
    following prose that has since changed, and the value would file a Claude
    Code run under `none` with no driver row. Any other explicit id wins, which
    is what lets a past run be regenerated from another session.
    """
    if flag is None:
        if inside:
            return inside, None
        return None, (
            f"no Claude Code session id: {SESSION_ENV} is not set in this shell. "
            "Pass --claude-session <id>, or --claude-session none for a run no "
            "Claude Code session drove."
        )
    if flag == "none" and inside:
        return None, (
            f"--claude-session none, but this shell runs inside Claude Code session {inside}. "
            "Omit --claude-session to record it. To regenerate a receipt for a run no "
            f"Claude Code session drove, unset {SESSION_ENV} for that one command."
        )
    return flag, None


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a valid Handoff Session Receipt.")
    parser.add_argument("--start", action="store_true", help="Stamp <repo>/.handoff/session-start and exit.")
    parser.add_argument("--phase")
    parser.add_argument(
        "--claude-session",
        help="Session id, or 'none' for a run no Claude Code session drove. Omit it to record "
             f"${SESSION_ENV}; pass an id only to regenerate a past run's receipt.")
    parser.add_argument("--checks")
    parser.add_argument("--anomalies", default="none")
    parser.add_argument("--codex-jobs", default="0", help="Number of codex-backed delegate-job.sh jobs including fix rounds.")
    parser.add_argument("--cc-jobs", default="0", help="Number of claude-backed delegate-job.sh jobs including fix rounds.")
    parser.add_argument("--copilot-jobs", default="0", help="Number of copilot-backed delegate-job.sh jobs including fix rounds.")
    parser.add_argument("--cursor-jobs", default="0", help="Number of cursor-backed delegate-job.sh jobs including fix rounds.")
    parser.add_argument("--antigravity-jobs", default="0", help="Number of antigravity-backed delegate-job.sh jobs including fix rounds.")
    parser.add_argument("--scope", default="n/a", help="project | global | n/a (default: n/a, when no configured role was touched).")
    parser.add_argument("--config-source", default="n/a", help="session | project | global | default | n/a.")
    parser.add_argument("--roles-used", default="none", help="'none' or a JSON array of {role, host, model, effort, verified}; host is the executing CLI.")
    parser.add_argument("--started-at", help="ISO 8601 session start, overriding the .handoff/session-start marker.")
    parser.add_argument("--ended-at", help="ISO 8601 receipt time, overriding the current clock; use it to regenerate a past run's receipt.")
    parser.add_argument("--no-save", action="store_true", help="Print the receipt without writing it under <repo>/.handoff/sessions/<date>_<claude_session>/.")
    parser.add_argument("--repo", default=".", help="Target repo holding .handoff/ (default: current directory).")
    args = parser.parse_args()

    now = datetime.now(timezone.utc)

    if args.start:
        marker = marker_path(args.repo)
        marker.parent.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        marker.write_text(stamp + "\n", encoding="utf-8")
        print(f"{stamp} {marker}")
        return 0

    if not (args.phase and args.checks):
        parser.error("--phase and --checks are required unless --start")

    session_id, refusal = resolve_session(args.claude_session, os.environ.get(SESSION_ENV) or "")
    if refusal:
        print(f"FAIL {refusal}", file=sys.stderr)
        return 1
    # Checked on the raw value before anything is printed: validate() strips,
    # so " abc" would pass it and still be a different folder name.
    try:
        session_path(args.repo, session_id)
    except EvidencePathError as error:
        print(f"FAIL {error}", file=sys.stderr)
        return 1

    if args.started_at:
        started = parse_iso(args.started_at)
    else:
        marker = marker_path(args.repo)
        if not marker.is_file():
            print(
                f"FAIL no session start recorded at {marker}; run "
                "'make-receipt.py --start --repo <repo>' at Phase 0, or pass --started-at <ISO8601>",
                file=sys.stderr,
            )
            return 1
        started = parse_iso(marker.read_text(encoding="utf-8"))
    ended = parse_iso(args.ended_at) if args.ended_at else now
    if ended > now:
        print(f"FAIL receipt end {ended.isoformat()} is in the future; check the clock", file=sys.stderr)
        return 1
    elapsed = (ended - started).total_seconds()
    if elapsed < 0:
        print(f"FAIL session start {started.isoformat()} is after the receipt end; check the clock", file=sys.stderr)
        return 1

    durations = job_durations(args.repo, started, ended)
    fields = {
        "phase": args.phase,
        "claude_session": session_id,
        "duration": format_duration(elapsed),
        "checks": args.checks,
        "anomalies": args.anomalies,
        "codex_jobs": args.codex_jobs,
        "codex_job_durations": durations["codex"],
        "cc_jobs": args.cc_jobs,
        "cc_job_durations": durations["claude"],
        "copilot_jobs": args.copilot_jobs,
        "copilot_job_durations": durations["copilot"],
        "cursor_jobs": args.cursor_jobs,
        "cursor_job_durations": durations["cursor"],
        "antigravity_jobs": args.antigravity_jobs,
        "antigravity_job_durations": durations["antigravity"],
        "scope": args.scope,
        "config_source": args.config_source,
        "roles_used": args.roles_used,
        "receipt_schema_version": "8",
    }

    validator = load_validator()
    failures = validator.validate(dict(fields)) + count_mismatches(fields) + multiline_fields(fields)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    lines = ["[Handoff session receipt]"]
    lines.extend(f"{key}: {value}" for key, value in fields.items())
    receipt = "\n".join(lines)

    # The output file is checked with the rest, before the receipt is printed: a
    # symlink standing where it goes would send the write somewhere else.
    save_path = None
    if not args.no_save:
        stamp = ended.strftime("%Y%m%dT%H%M%SZ")
        try:
            # The date only names a folder this session does not have yet; an
            # existing one is found by session id whatever date this run has.
            save_path = session_path(args.repo, session_id, f"receipt-{stamp}.md",
                                     date=ended.strftime("%Y%m%d"))
        except EvidencePathError as error:
            print(f"FAIL {error}", file=sys.stderr)
            return 1

    print(receipt)

    if save_path is not None:
        try:
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_text(receipt + "\n", encoding="utf-8")
        except OSError as error:
            print(f"FAIL cannot save the receipt to {save_path}: {error}", file=sys.stderr)
            return 1
        print(f"saved: {save_path}", file=sys.stderr)
        note = legacy_layout_note(args.repo)
        if note:
            print(f"note: {note}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
