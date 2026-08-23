#!/usr/bin/env python3
"""Statically validate the Handoff behavior regression prompts.

    python3 scripts/run-test-prompts.py

Checks every entry in test-prompts.json structurally: unique ids, non-empty
prompt, expected_behavior and must_not lists, risky command text confined to
must_not, and receipt-contract coverage.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PROMPTS = ROOT / "test-prompts.json"

RISKY = re.compile(r"git reset --hard|rm -rf|force push|--force")  # risk-ok: detection pattern, not a command
REQUIRED_KEYS = {"id", "prompt", "expected_behavior", "must_not"}


def static_check(entries: list[dict]) -> list[str]:
    failures: list[str] = []

    if len(entries) < 4:
        failures.append(f"expected at least 4 cases, found {len(entries)}")

    ids = [entry.get("id") for entry in entries]
    duplicated = {case_id for case_id in ids if ids.count(case_id) > 1}
    if duplicated:
        failures.append(f"duplicate ids: {sorted(duplicated)}")

    for entry in entries:
        case_id = entry.get("id", "<missing id>")
        missing = REQUIRED_KEYS - set(entry)
        if missing:
            failures.append(f"{case_id}: missing keys {sorted(missing)}")
            continue
        if not str(entry["prompt"]).strip():
            failures.append(f"{case_id}: empty prompt")
        for list_key in ("expected_behavior", "must_not"):
            value = entry[list_key]
            if not isinstance(value, list) or not value:
                failures.append(f"{case_id}: {list_key} must be a non-empty list")
                continue
            if any(not str(item).strip() for item in value):
                failures.append(f"{case_id}: {list_key} contains an empty item")
        for text in [entry["prompt"], *entry.get("expected_behavior", [])]:
            if RISKY.search(str(text)):
                failures.append(f"{case_id}: risky command text outside must_not: {text!r}")
        if entry.get("should_trigger") not in (True, False):
            failures.append(f"{case_id}: should_trigger must be true or false")

    if not any("receipt" in str(entry.get("id", "")) for entry in entries):
        failures.append("no receipt-contract case found (expected an id containing 'receipt')")

    return failures


def main() -> int:
    argparse.ArgumentParser(description="Validate Handoff test prompts.").parse_args()

    entries = json.loads(PROMPTS.read_text(encoding="utf-8"))
    if not isinstance(entries, list):
        print("FAIL test-prompts.json must be a JSON array")
        return 1

    failures = static_check(entries)
    if not failures:
        print(f"PASS static checks ({len(entries)} cases)")

    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
