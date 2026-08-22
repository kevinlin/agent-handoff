#!/usr/bin/env python3
"""Generate a valid Partner Session Receipt.

Receipts written by hand drift in format and invite optimistic guesses.
This tool builds the receipt from arguments, validates the result with
scripts/validate-receipt.py logic before printing, and can persist it under
the target repo's .partner/receipts/.

Usage:
    python3 make-receipt.py --phase "final fix" --claude-session abc123 \
        --checks "npm test; bash lint.sh" --codex-jobs 2 \
        [--scope project] [--config-source project] [--roles-used '[]'] \
        [--anomalies none] [--save] [--repo PATH]

Tip: get --codex-jobs from the job directories under <repo>/.partner/jobs/
instead of recalling how many were submitted.
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent


def load_validator():
    spec = importlib.util.spec_from_file_location("validate_receipt", SCRIPT_DIR / "validate-receipt.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description="Generate a valid Partner Session Receipt.")
    parser.add_argument("--phase", required=True)
    parser.add_argument("--claude-session", required=True, help="Session id, or 'none'.")
    parser.add_argument("--checks", required=True)
    parser.add_argument("--anomalies", default="none")
    parser.add_argument("--codex-jobs", default="0", help="Number of delegate-codex.sh jobs including fix rounds.")
    parser.add_argument("--scope", default="n/a", help="project | global | n/a (default: n/a, when no configured role was touched).")
    parser.add_argument("--config-source", default="n/a", help="session | project | global | default | n/a.")
    parser.add_argument("--roles-used", default="none", help="'none' or a JSON array of {role, host, model, effort, verified}; host is the executing CLI.")
    parser.add_argument("--save", action="store_true", help="Also write to <repo>/.partner/receipts/.")
    parser.add_argument("--repo", default=".", help="Target repo for --save (default: current directory).")
    args = parser.parse_args()

    fields = {
        "phase": args.phase,
        "claude_session": args.claude_session,
        "checks": args.checks,
        "anomalies": args.anomalies,
        "codex_jobs": args.codex_jobs,
        "scope": args.scope,
        "config_source": args.config_source,
        "roles_used": args.roles_used,
        "receipt_schema_version": "3",
    }

    validator = load_validator()
    failures = validator.validate(dict(fields))
    if failures:
        for failure in failures:
            print(f"FAIL {failure}", file=sys.stderr)
        return 1

    lines = ["[Partner session receipt]"]
    lines.extend(f"{key}: {value}" for key, value in fields.items())
    receipt = "\n".join(lines)
    print(receipt)

    if args.save:
        save_dir = Path(args.repo).resolve() / ".partner" / "receipts"
        save_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        save_path = save_dir / f"receipt-{stamp}.md"
        save_path.write_text(receipt + "\n", encoding="utf-8")
        print(f"saved: {save_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
