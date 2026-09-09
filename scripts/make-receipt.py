#!/usr/bin/env python3
"""Generate a valid Handoff Session Receipt.

Receipts written by hand drift in format and invite optimistic guesses.
This tool builds the receipt from arguments, validates the result with
scripts/validate-receipt.py logic before printing, and can persist it under
the target repo's .handoff/receipts/.

Usage:
    python3 make-receipt.py --start --repo PATH        # Phase 0: stamp the start
    python3 make-receipt.py --phase "final fix" --claude-session abc123 \
        --checks "npm test; bash lint.sh" --codex-jobs 2 --cc-jobs 1 \
        --copilot-jobs 1 \
        [--scope project] [--config-source project] [--roles-used '[]'] \
        [--anomalies none] [--started-at ISO8601] [--save] [--repo PATH]

Tip: get --codex-jobs, --cc-jobs and --copilot-jobs from the job directories under
<repo>/.handoff/jobs/ instead of recalling how many were submitted; each job's
meta names the backend that executed it.

Duration is wall clock: --start writes <repo>/.handoff/session-start, and the
receipt run measures against it, so time blocked on a human approval counts.
Per-job durations come from each job's meta submitted_at and the mtime of its
exit_code, partitioned by that job's backend; jobs submitted before the session
start belong to an earlier run and are left out.
"""

from __future__ import annotations

import argparse
import importlib.util
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


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


BACKENDS = ("codex", "claude", "copilot")


def job_durations(repo: str, started: datetime) -> dict[str, str]:
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
        if submitted < started:
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a valid Handoff Session Receipt.")
    parser.add_argument("--start", action="store_true", help="Stamp <repo>/.handoff/session-start and exit.")
    parser.add_argument("--phase")
    parser.add_argument("--claude-session", help="Session id, or 'none'.")
    parser.add_argument("--checks")
    parser.add_argument("--anomalies", default="none")
    parser.add_argument("--codex-jobs", default="0", help="Number of codex-backed delegate-codex.sh jobs including fix rounds.")
    parser.add_argument("--cc-jobs", default="0", help="Number of claude-backed delegate-codex.sh jobs including fix rounds.")
    parser.add_argument("--copilot-jobs", default="0", help="Number of copilot-backed delegate-codex.sh jobs including fix rounds.")
    parser.add_argument("--scope", default="n/a", help="project | global | n/a (default: n/a, when no configured role was touched).")
    parser.add_argument("--config-source", default="n/a", help="session | project | global | default | n/a.")
    parser.add_argument("--roles-used", default="none", help="'none' or a JSON array of {role, host, model, effort, verified}; host is the executing CLI.")
    parser.add_argument("--started-at", help="ISO 8601 session start, overriding the .handoff/session-start marker.")
    parser.add_argument("--save", action="store_true", help="Also write to <repo>/.handoff/receipts/.")
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

    if not (args.phase and args.claude_session and args.checks):
        parser.error("--phase, --claude-session and --checks are required unless --start")

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
    elapsed = (now - started).total_seconds()
    if elapsed < 0:
        print(f"FAIL session start {started.isoformat()} is in the future; check the clock", file=sys.stderr)
        return 1

    durations = job_durations(args.repo, started)
    fields = {
        "phase": args.phase,
        "claude_session": args.claude_session,
        "duration": format_duration(elapsed),
        "checks": args.checks,
        "anomalies": args.anomalies,
        "codex_jobs": args.codex_jobs,
        "codex_job_durations": durations["codex"],
        "cc_jobs": args.cc_jobs,
        "cc_job_durations": durations["claude"],
        "copilot_jobs": args.copilot_jobs,
        "copilot_job_durations": durations["copilot"],
        "scope": args.scope,
        "config_source": args.config_source,
        "roles_used": args.roles_used,
        "receipt_schema_version": "6",
    }

    validator = load_validator()
    failures = validator.validate(dict(fields))
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    lines = ["[Handoff session receipt]"]
    lines.extend(f"{key}: {value}" for key, value in fields.items())
    receipt = "\n".join(lines)
    print(receipt)

    if args.save:
        save_dir = Path(args.repo).resolve() / ".handoff" / "receipts"
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        save_path = save_dir / f"receipt-{stamp}.md"
        save_path.write_text(receipt + "\n", encoding="utf-8")
        print(f"saved: {save_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
