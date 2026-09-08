#!/usr/bin/env python3
"""Render a Handoff Session Receipt and its job state as a cost receipt.

Numbers come only from measurement: codex jobs yield token counters, claude
jobs yield the CLI's own cost figure, and the driver row is scoped to the
run interval. Nothing is estimated, and no saving is computed.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from handoff_runtime import inject, job_state, read_meta  # noqa: E402

RECEIPT_HEADER = "[Handoff session receipt]"
SCHEMA_VERSION = "5"
FIELD_LINE = re.compile(r"^([a-z_]+):\s*(.+)$")
SAFE_JOB_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class ReceiptError(Exception):
    """The receipt cannot be read as an index of one run."""


def _parse_block(text: str) -> dict[str, str]:
    if text.count(RECEIPT_HEADER) != 1:
        raise ReceiptError(
            f"expected exactly one {RECEIPT_HEADER} block, "
            f"found {text.count(RECEIPT_HEADER)}")
    start = text.index(RECEIPT_HEADER) + len(RECEIPT_HEADER)
    fields: dict[str, str] = {}
    for line in text[start:].splitlines():
        line = line.strip()
        if not line:
            if fields:
                break
            continue
        match = FIELD_LINE.match(line)
        if not match:
            break
        key, value = match.group(1), match.group(2).strip()
        if key in fields:
            raise ReceiptError(f"duplicate field key: {key}")
        fields[key] = value
    return fields


def _entries(value: str) -> list[tuple[str, bool]]:
    """Parse a *_job_durations value into (jobId, is_running) pairs."""
    if value == "none":
        return []
    pairs = []
    for chunk in value.split("; "):
        job_id, separator, measure = chunk.partition("=")
        if not separator:
            raise ReceiptError(f"malformed job duration entry: {chunk!r}")
        pairs.append((job_id.strip(), measure.strip() == "running"))
    return pairs


def load_receipt(text: str, repo: Path) -> dict:
    fields = _parse_block(text)
    if fields.get("receipt_schema_version") != SCHEMA_VERSION:
        raise ReceiptError(
            f"receipt_schema_version must be {SCHEMA_VERSION}, got "
            f"{fields.get('receipt_schema_version')!r}; regenerate with make-receipt.py")

    jobs_root = (repo / ".handoff" / "jobs").resolve()
    jobs: list[dict] = []
    seen: set[str] = set()
    for backend, count_field, durations_field in (
            ("codex", "codex_jobs", "codex_job_durations"),
            ("claude", "cc_jobs", "cc_job_durations")):
        entries = _entries(fields.get(durations_field, "none"))
        if fields.get(count_field, "") != str(len(entries)):
            raise ReceiptError(
                f"{count_field} is {fields.get(count_field)!r} but "
                f"{durations_field} has {len(entries)} entries")
        for job_id, running in entries:
            if not SAFE_JOB_ID.match(job_id):
                raise ReceiptError(f"job id is not a safe path segment: {job_id!r}")
            if job_id in seen:
                raise ReceiptError(f"duplicate job id: {job_id}")
            seen.add(job_id)
            job_dir = (jobs_root / job_id).resolve()
            if jobs_root not in job_dir.parents:
                raise ReceiptError(f"job id escapes the jobs directory: {job_id!r}")
            if not job_dir.is_dir():
                raise ReceiptError(f"no job directory for {job_id}")
            recorded = read_meta(job_dir).get("backend", "codex")
            if recorded != backend:
                raise ReceiptError(
                    f"{job_id} is listed under {durations_field} but its meta "
                    f"records backend={recorded}")
            jobs.append({"job_id": job_id, "backend": backend, "running": running})
    return {"fields": fields, "jobs": jobs}


COUNTERS = ("input", "cache_read", "cache_write", "output", "reasoning")

CODEX_FIELDS = {
    "input": "input_tokens",
    "cache_read": "cached_input_tokens",
    "cache_write": "cache_write_input_tokens",
    "output": "output_tokens",
    "reasoning": "reasoning_output_tokens",
}
CLAUDE_FIELDS = {
    "input": "input_tokens",
    "cache_read": "cache_read_input_tokens",
    "cache_write": "cache_creation_input_tokens",
    "output": "output_tokens",
}


def read_events(job_dir: Path) -> list[dict]:
    """Every parseable JSONL line. A truncated tail is skipped, not fatal:
    a killed worker leaves a half-written line and its earlier events still count."""
    log = job_dir / "log.jsonl"
    if not log.is_file():
        return []
    events = []
    for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            events.append(json.loads(line))
        except (json.JSONDecodeError, ValueError):
            continue
    return events


def _blank_usage() -> dict:
    return {counter: None for counter in COUNTERS}


def _add(total: dict, counter: str, value) -> None:
    """Accumulate, keeping None distinct from a measured zero."""
    if not isinstance(value, int):
        return
    total[counter] = value if total[counter] is None else total[counter] + value


def fold_usage(events: list[dict], backend: str) -> dict:
    usage = _blank_usage()
    cost_usd = None
    denials = None
    models: list[str] = []
    seen_terminal = 0

    if backend == "codex":
        for event in events:
            if event.get("type") not in ("turn.completed", "turn.failed"):
                continue
            raw = event.get("usage") or {}
            seen_terminal += 1
            for counter, field in CODEX_FIELDS.items():
                _add(usage, counter, raw.get(field))
    else:
        # Only the last result is authoritative; an earlier one is a repeat.
        for event in events:
            if event.get("type") != "result":
                continue
            seen_terminal += 1
            usage = _blank_usage()
            raw = event.get("usage") or {}
            for counter, field in CLAUDE_FIELDS.items():
                _add(usage, counter, raw.get(field))
            details = raw.get("output_tokens_details") or {}
            _add(usage, "reasoning", details.get("thinking_tokens"))
            cost = event.get("total_cost_usd")
            cost_usd = cost if isinstance(cost, (int, float)) else None
            # modelUsage names the models; its numbers duplicate `usage`.
            models = sorted(event.get("modelUsage") or {})
            denials = len(event.get("permission_denials") or [])

    return {"usage": usage, "cost_usd": cost_usd, "denials": denials,
            "models": models, "repeated": seen_terminal > 1}


def resolve_model(repo: Path, job_id: str, seen: set[str] | None = None) -> str:
    """A resumed job writes model=inherit; walk `parent=` to the origin."""
    seen = seen or set()
    if job_id in seen:
        return "inherit (unresolved)"
    seen.add(job_id)
    meta = read_meta(repo / ".handoff" / "jobs" / job_id)
    model = meta.get("model", "unknown")
    if model != "inherit":
        return model
    parent = meta.get("parent")
    if not parent or not (repo / ".handoff" / "jobs" / parent).is_dir():
        return "inherit (unresolved)"
    return resolve_model(repo, parent, seen)


def job_row(repo: Path, job: dict) -> dict:
    job_dir = repo / ".handoff" / "jobs" / job["job_id"]
    meta = read_meta(job_dir)
    folded = fold_usage(read_events(job_dir), job["backend"])
    return {
        "job_id": job["job_id"],
        "label": meta.get("label", ""),
        "role": meta.get("role", ""),
        "backend": job["backend"],
        "model": resolve_model(repo, job["job_id"]),
        "state": job_state(job_dir),
        **folded,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("receipt", nargs="?", default=None)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
