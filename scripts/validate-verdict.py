#!/usr/bin/env python3
"""Validate an e2e verdict artifact written by the e2e_verifier role.

The verdict is the input to a merge decision, so its fields are checked
against each other rather than trusted: a PASS must agree with the exit
code, the scenario counts, and the hash of the scenarios the driver
reviewed. Field semantics are documented in docs/verdict-schema.json.

Usage:
    python3 scripts/validate-verdict.py <verdict.json>
    python3 scripts/validate-verdict.py - --expect-scenarios-sha256 <sha>

Exit 0 when valid, 1 with FAIL lines on stderr otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, List, Mapping, Optional

RESULTS = ("PASS", "FAIL", "BLOCKED")
REQUIRED_FIELDS = (
    "result",
    "base_commit",
    "tested_commit",
    "scenarios_sha256",
    "target",
    "command",
    "exit_code",
    "scenarios",
    "harness_edits",
    "findings",
    "evidence",
)
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate(verdict: Mapping[str, Any], expected_sha: Optional[str]) -> List[str]:
    failures: List[str] = []
    for field in REQUIRED_FIELDS:
        if field not in verdict:
            failures.append(f"missing field: {field}")
    if failures:
        return failures

    result = verdict["result"]
    if result not in RESULTS:
        failures.append(f"result must be one of {', '.join(RESULTS)}; got {result!r}")

    for field in ("base_commit", "tested_commit"):
        if not isinstance(verdict[field], str) or not SHA1.match(verdict[field]):
            failures.append(f"{field} must be a 40-character commit sha")

    sha = verdict["scenarios_sha256"]
    if not isinstance(sha, str) or not SHA256.match(sha):
        failures.append("scenarios_sha256 must be a 64-character sha256")
    elif expected_sha is not None and sha != expected_sha:
        failures.append(
            "scenarios_sha256 does not match the reviewed scenarios; "
            "the acceptance contract changed after review, so this run is void"
        )

    if not isinstance(verdict["target"], str) or not verdict["target"].strip():
        failures.append("target must name the system under test")

    scenarios = verdict["scenarios"]
    if not isinstance(scenarios, Mapping):
        failures.append("scenarios must be an object with total, passed, and ids")
        return failures
    for key in ("total", "passed"):
        value = scenarios.get(key)
        # bool is an int subclass; a boolean count is a malformed verdict.
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            failures.append(f"scenarios.{key} must be a non-negative integer")
    ids = scenarios.get("ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        failures.append("scenarios.ids must be a list of scenario id strings")
    if failures:
        return failures

    if scenarios["total"] != len(ids):
        failures.append("scenarios.total must equal the number of ids")
    if scenarios["passed"] > scenarios["total"]:
        failures.append("scenarios.passed must not exceed scenarios.total")

    for field in ("harness_edits", "findings", "evidence"):
        if not isinstance(verdict[field], list):
            failures.append(f"{field} must be a list")
    if failures:
        return failures

    exit_code = verdict["exit_code"]
    if exit_code is not None and (
        not isinstance(exit_code, int) or isinstance(exit_code, bool)
    ):
        failures.append("exit_code must be an integer or null")

    if result == "PASS":
        if exit_code != 0:
            failures.append("PASS requires exit_code 0")
        if scenarios["passed"] != scenarios["total"]:
            failures.append("PASS requires every scenario to pass")
        if scenarios["total"] == 0:
            failures.append("PASS requires at least one executed scenario")
        if verdict["findings"]:
            failures.append("PASS requires an empty findings list")
    elif result == "FAIL":
        if not verdict["findings"]:
            failures.append("FAIL requires at least one finding")
        if exit_code == 0 and scenarios["passed"] == scenarios["total"]:
            failures.append("FAIL requires a non-zero exit_code or an unpassed scenario")
    else:  # BLOCKED
        if scenarios["passed"] != 0:
            failures.append(
                "BLOCKED requires scenarios.passed to be 0; no valid execution happened"
            )
        if not verdict["findings"]:
            failures.append("BLOCKED requires a finding naming the blocker")

    return failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an e2e verdict artifact.")
    parser.add_argument("path", help="Path to verdict.json, or - for stdin.")
    parser.add_argument(
        "--expect-scenarios-sha256",
        help="Hash of the reviewed .feature files, as recorded by the driver at review time.",
    )
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError as error:
        print(f"FAIL: verdict is not valid JSON: {error}", file=sys.stderr)
        return 1
    if not isinstance(verdict, dict):
        print("FAIL: verdict must be a JSON object", file=sys.stderr)
        return 1

    failures = validate(verdict, args.expect_scenarios_sha256)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"OK: {verdict['result']} verdict for {verdict['tested_commit'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
