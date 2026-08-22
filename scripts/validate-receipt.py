#!/usr/bin/env python3
"""Validate a Handoff Session Receipt.

Accepts either the text block format emitted at the end of a Handoff run:

    [Handoff session receipt]
    phase: final fix
    claude_session: <id or none>
    ...

or a JSON object (with --json) matching docs/receipt-schema.json.

Usage:
    python3 scripts/validate-receipt.py <file-with-receipt-block>
    python3 scripts/validate-receipt.py --json <receipt.json>
    ... | python3 scripts/validate-receipt.py -

The file may be a markdown document; the first [Handoff session receipt]
block is extracted. Exit 0 when valid, 1 with FAIL lines otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

RECEIPT_HEADER = "[Handoff session receipt]"

PHASES = {"planning", "codex implementation", "review", "final fix"}
SCOPES = {"project", "global", "n/a"}
CONFIG_SOURCES = {"session", "project", "global", "default", "n/a"}
# The CLI that executed a role, not the runtime that loaded SKILL.md.
ROLE_HOSTS = {"claude_code", "codex"}
ROLES = {"deep_reasoner", "fast_worker", "arbiter"}

REQUIRED_FIELDS = [
    "phase",
    "claude_session",
    "checks",
    "anomalies",
    "codex_jobs",
    "scope",
    "config_source",
    "roles_used",
    "receipt_schema_version",
]


def extract_block(text: str) -> dict[str, str] | None:
    start = text.find(RECEIPT_HEADER)
    if start < 0:
        return None
    fields: dict[str, str] = {}
    for line in text[start + len(RECEIPT_HEADER):].splitlines():
        line = line.strip()
        if not line:
            if fields:
                break
            continue
        match = re.match(r"^([a-z_]+):\s*(.+)$", line)
        if not match:
            break
        fields[match.group(1)] = match.group(2).strip()
    return fields


def is_non_bool_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def parse_roles_used(value: object) -> list[dict]:
    if value == "none":
        return []
    parsed = value
    if isinstance(value, str):
        parsed = json.loads(value)  # may raise json.JSONDecodeError
    if not isinstance(parsed, list):
        raise ValueError("roles_used must be 'none' or a JSON array")
    for entry in parsed:
        if not isinstance(entry, dict):
            raise ValueError("roles_used entries must be objects")
        missing = {"role", "host", "model", "effort", "verified"} - set(entry)
        if missing:
            raise ValueError(f"roles_used entry missing fields: {sorted(missing)}")
        if entry["role"] not in ROLES:
            raise ValueError(f"roles_used entry has unknown role: {entry['role']!r}")
        if entry["host"] not in ROLE_HOSTS:
            raise ValueError(f"roles_used entry has unknown host: {entry['host']!r}")
        if not isinstance(entry["model"], str) or not entry["model"].strip():
            raise ValueError("roles_used entry model must be a non-empty string")
        if not isinstance(entry["effort"], str) or not entry["effort"].strip():
            raise ValueError("roles_used entry effort must be a non-empty string")
        if not isinstance(entry["verified"], bool):
            raise ValueError("roles_used entry verified must be a boolean")
    return parsed


def validate(fields: dict[str, object], *, strict_json_types: bool = False) -> list[str]:
    failures: list[str] = []

    for field in REQUIRED_FIELDS:
        if field not in fields or fields[field] in ("", None):
            failures.append(f"missing field: {field}")
    unknown = set(fields) - set(REQUIRED_FIELDS)
    if unknown:
        failures.append(f"unknown fields: {', '.join(sorted(str(f) for f in unknown))}")
    if failures:
        return failures

    def as_text(field: str) -> str:
        return str(fields[field]).strip()

    if as_text("phase") not in PHASES:
        failures.append(f"phase must be one of {sorted(PHASES)}, got {as_text('phase')!r}")

    if strict_json_types:
        value = fields["codex_jobs"]
        if not is_non_bool_int(value):
            failures.append(f"codex_jobs must be an integer, got {value!r}")
        elif value < 0:
            failures.append(f"codex_jobs must be >= 0, got {value!r}")
    elif not re.fullmatch(r"\d+", as_text("codex_jobs")):
        failures.append(f"codex_jobs must be an integer, got {as_text('codex_jobs')!r}")

    if as_text("scope") not in SCOPES:
        failures.append(f"scope must be one of {sorted(SCOPES)}, got {as_text('scope')!r}")
    if as_text("config_source") not in CONFIG_SOURCES:
        failures.append(
            f"config_source must be one of {sorted(CONFIG_SOURCES)}, got {as_text('config_source')!r}"
        )
    try:
        parse_roles_used(fields["roles_used"])
    except (ValueError, json.JSONDecodeError) as error:
        failures.append(f"roles_used is invalid: {error}")

    version = fields["receipt_schema_version"]
    version_text = as_text("receipt_schema_version")
    if strict_json_types:
        if version != 3:
            failures.append(f"receipt_schema_version must be 3, got {version!r}")
    elif version_text != "3":
        failures.append(f"receipt_schema_version must be 3, got {version_text!r}")

    for placeholder_field in ("phase", "claude_session", "checks", "anomalies"):
        value = as_text(placeholder_field)
        if value.startswith("<") and value.endswith(">"):
            failures.append(f"{placeholder_field} still contains a template placeholder: {value}")

    return failures


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Handoff Session Receipt.")
    parser.add_argument("path", help="File containing a receipt block, or - for stdin.")
    parser.add_argument("--json", action="store_true", help="Treat input as a JSON receipt object.")
    args = parser.parse_args()

    text = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")

    if args.json:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as error:
            print(f"FAIL invalid JSON: {error}")
            return 1
        if not isinstance(data, dict):
            print("FAIL JSON receipt must be an object")
            return 1
        fields: dict[str, object] = data
    else:
        extracted = extract_block(text)
        if extracted is None:
            print(f"FAIL no '{RECEIPT_HEADER}' block found in input")
            return 1
        fields = dict(extracted)

    failures = validate(fields, strict_json_types=args.json)
    if failures:
        for failure in failures:
            print(f"FAIL {failure}")
        return 1

    print("PASS Handoff session receipt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
