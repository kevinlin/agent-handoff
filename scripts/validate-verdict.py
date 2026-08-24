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


def _shape_failures(verdict: Mapping[str, Any]) -> List[str]:
    """Field presence and types, so the rule checks can index freely."""

    failures = [f"missing field: {field}" for field in REQUIRED_FIELDS if field not in verdict]
    if failures:
        return failures

    if verdict["result"] not in RESULTS:
        failures.append(
            f"result must be one of {', '.join(RESULTS)}; got {verdict['result']!r}"
        )
    for field in ("base_commit", "tested_commit"):
        if not isinstance(verdict[field], str) or not SHA1.match(verdict[field]):
            failures.append(f"{field} must be a 40-character commit sha")
    if not isinstance(verdict["scenarios_sha256"], str) or not SHA256.match(
        verdict["scenarios_sha256"]
    ):
        failures.append("scenarios_sha256 must be a 64-character sha256")
    if not isinstance(verdict["target"], str) or not verdict["target"].strip():
        failures.append("target must name the system under test")
    for field in ("harness_edits", "findings", "evidence"):
        if not isinstance(verdict[field], list):
            failures.append(f"{field} must be a list")
    if not _is_count(verdict["exit_code"], allow_none=True, allow_negative=True):
        failures.append("exit_code must be an integer or null")

    scenarios = verdict["scenarios"]
    if not isinstance(scenarios, Mapping):
        failures.append("scenarios must be an object with total, passed, and ids")
        return failures
    for key in ("total", "passed"):
        if not _is_count(scenarios.get(key)):
            failures.append(f"scenarios.{key} must be a non-negative integer")
    ids = scenarios.get("ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        failures.append("scenarios.ids must be a list of scenario id strings")
    return failures


def _is_count(value: Any, allow_none: bool = False, allow_negative: bool = False) -> bool:
    # bool is an int subclass; a boolean count is a malformed verdict.
    if value is None:
        return allow_none
    if not isinstance(value, int) or isinstance(value, bool):
        return False
    return allow_negative or value >= 0


def _rule_failures(verdict: Mapping[str, Any], expected_sha: Optional[str]) -> List[str]:
    """Cross-field agreement: the checks that make a verdict mergeable."""

    failures: List[str] = []
    scenarios = verdict["scenarios"]
    total, passed = scenarios["total"], scenarios["passed"]
    exit_code, findings = verdict["exit_code"], verdict["findings"]

    if expected_sha is not None and verdict["scenarios_sha256"] != expected_sha:
        failures.append(
            "scenarios_sha256 does not match the reviewed scenarios; "
            "the acceptance contract changed after review, so this run is void"
        )
    if total != len(scenarios["ids"]):
        failures.append("scenarios.total must equal the number of ids")
    if passed > total:
        failures.append("scenarios.passed must not exceed scenarios.total")

    result = verdict["result"]
    if result == "PASS":
        if exit_code != 0:
            failures.append("PASS requires exit_code 0")
        if passed != total:
            failures.append("PASS requires every scenario to pass")
        if total == 0:
            failures.append("PASS requires at least one executed scenario")
        if findings:
            failures.append("PASS requires an empty findings list")
    elif result == "FAIL":
        if not findings:
            failures.append("FAIL requires at least one finding")
        if exit_code == 0 and passed == total:
            failures.append("FAIL requires a non-zero exit_code or an unpassed scenario")
    elif result == "BLOCKED":
        if passed != 0:
            failures.append(
                "BLOCKED requires scenarios.passed to be 0; no valid execution happened"
            )
        if not findings:
            failures.append("BLOCKED requires a finding naming the blocker")
    return failures


def validate(verdict: Mapping[str, Any], expected_sha: Optional[str]) -> List[str]:
    return _shape_failures(verdict) or _rule_failures(verdict, expected_sha)


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
