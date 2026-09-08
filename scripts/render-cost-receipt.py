#!/usr/bin/env python3
"""Render a Handoff Session Receipt and its job state as a cost receipt.

Numbers come only from measurement: codex jobs yield token counters, claude
jobs yield the CLI's own cost figure, and the driver row is scoped to the
run interval. Nothing is estimated, and no saving is computed.
"""

from __future__ import annotations

import argparse
import glob
import json
import re
import sys
from datetime import datetime, timedelta, timezone
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


DURATION = re.compile(r"^(\d+)min ([0-5]\d)sec$")
RECEIPT_STAMP = re.compile(r"^receipt-(\d{8}T\d{6}Z)\.md$")
DEFAULT_PROJECTS = Path.home() / ".claude" / "projects"


def parse_duration(value: str) -> int:
    match = DURATION.match(value.strip())
    if not match:
        raise ReceiptError(f"duration must look like '74min 05sec', got {value!r}")
    return int(match.group(1)) * 60 + int(match.group(2))


def run_interval(receipt_path: Path | None, duration: str):
    """A receipt saved by make-receipt.py is named for its generation time, so
    the stamp is the run's end and `duration` is its span. Any other input has
    no derivable end, and the driver row is reported unscoped instead."""
    if receipt_path is None:
        return None
    match = RECEIPT_STAMP.match(receipt_path.name)
    if not match:
        return None
    end = datetime.strptime(match.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    return end - timedelta(seconds=parse_duration(duration)), end


def _parse_ts(value: str):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def driver_row(session_id: str, interval, projects_root: Path = DEFAULT_PROJECTS) -> dict:
    blank = {"state": "unavailable", "usage": _blank_usage(),
             "models": [], "cost_usd": None}
    # The session id is data, not a pattern: escape it before it reaches glob.
    pattern = str(projects_root / "*" / (glob.escape(session_id) + ".jsonl"))
    matches = sorted(glob.glob(pattern))
    if not matches:
        return blank

    usage = _blank_usage()
    models: set[str] = set()
    for line in Path(matches[0]).read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        message = event.get("message") or {}
        raw = message.get("usage")
        if not raw:
            continue
        if interval is not None:
            stamp = _parse_ts(event.get("timestamp", ""))
            if stamp is None or not (interval[0] <= stamp <= interval[1]):
                continue
        for counter, field in CLAUDE_FIELDS.items():
            _add(usage, counter, raw.get(field))
        details = raw.get("output_tokens_details") or {}
        _add(usage, "reasoning", details.get("thinking_tokens"))
        if message.get("model"):
            models.add(message["model"])

    return {"state": "measured" if interval else "unscoped",
            "usage": usage, "models": sorted(models), "cost_usd": None}


def _figure(rows: list[dict], with_cost: bool) -> dict:
    usage = {}
    for counter in COUNTERS:
        values = [r["usage"].get(counter) for r in rows]
        measured = [v for v in values if v is not None]
        usage[counter] = {
            "value": sum(measured) if measured else None,
            "complete": len(measured) == len(values) and bool(values),
            "measured": len(measured),
            "total": len(values),
        }
    costs = [r["cost_usd"] for r in rows if r["backend"] == "claude"]
    known = [c for c in costs if c is not None]
    return {
        "usage": usage,
        "cost_usd": sum(known) if (with_cost and known) else None,
        "cost_complete": bool(costs) and len(known) == len(costs),
        "jobs": len(rows),
    }


def summarize(rows: list[dict]) -> dict:
    """Two figures whose populations overlap by design: codex jobs are in both.
    They are never added together, and neither is a saving."""
    codex_rows = [r for r in rows if r["backend"] == "codex"]
    denials = [r["denials"] for r in rows if r["denials"] is not None]
    return {
        "codex_subscription": _figure(codex_rows, with_cost=False),
        "outside_driver": _figure(rows, with_cost=True),
        "denials": sum(denials) if denials else None,
    }


COUNTER_LABELS = {"input": "Input", "cache_read": "Cache read",
                  "cache_write": "Cache write", "output": "Output",
                  "reasoning": "Reasoning"}

DRIVER_NOTE = {
    "measured": "scoped to this run's interval",
    "unscoped": "unscoped: whole-session total, may include work outside this run",
    "unavailable": "transcript not found",
}


def md_cell(value) -> str:
    """Table cell text. None is an absent measurement, never a zero."""
    if value is None:
        return "unknown"
    if isinstance(value, int):
        return f"{value:,}"
    text = str(value).replace("|", "\\|").replace("\n", " ")
    return text or "-"


def _figure_cell(column: dict) -> str:
    if column["value"] is None:
        return "unknown"
    body = f"{column['value']:,}"
    if column["complete"]:
        return body
    return f"≥ {body} ({column['measured']} of {column['total']} jobs measured)"


def build_payload(receipt: dict, rows: list[dict], driver: dict,
                  interval, sources: list[str]) -> dict:
    fields = receipt["fields"]
    return {
        "claude_session": fields.get("claude_session", "none"),
        "duration": fields.get("duration", "unknown"),
        "interval": ([interval[0].isoformat(), interval[1].isoformat()]
                     if interval else None),
        "generated_at": datetime.now(timezone.utc).isoformat(),
        # Enumerated export: no denial command strings, no prompt text.
        "jobs": [{"job_id": r["job_id"], "label": r["label"], "role": r["role"],
                  "backend": r["backend"], "model": r["model"], "state": r["state"],
                  "usage": r["usage"], "cost_usd": r["cost_usd"],
                  "denials": r["denials"], "repeated": r["repeated"]}
                 for r in rows],
        "driver": driver,
        "summary": summarize(rows),
        "sources": sources,
        "counters": list(COUNTERS),
    }


def render_markdown(payload: dict) -> str:
    summary = payload["summary"]
    out = ["# Handoff Cost Receipt", ""]
    out.append(f"- Session: `{payload['claude_session']}`")
    out.append(f"- Run duration: {payload['duration']}")
    out.append("- Interval: " + (
        f"{payload['interval'][0]} to {payload['interval'][1]}"
        if payload["interval"] else "not derivable from this input"))
    out.append(f"- Generated: {payload['generated_at']}")
    out += ["", "## Summary", ""]

    codex, outside = summary["codex_subscription"], summary["outside_driver"]
    out.append(f"**Ran on a Codex subscription** ({codex['jobs']} jobs). "
               "No cost figure: the Codex CLI emits none.")
    out.append("")
    out.append("| " + " | ".join(COUNTER_LABELS[c] for c in COUNTERS) + " |")
    out.append("|" + "---|" * len(COUNTERS))
    out.append("| " + " | ".join(_figure_cell(codex["usage"][c]) for c in COUNTERS) + " |")
    out.append("")
    cost = ("unknown" if outside["cost_usd"] is None
            else repr(outside["cost_usd"]) + ("" if outside["cost_complete"] else " (partial)"))
    out.append(f"**Ran outside the driver session** ({outside['jobs']} jobs). "
               f"CLI-reported cost of the claude-backed jobs: {cost}.")
    out.append("")
    out.append("| " + " | ".join(COUNTER_LABELS[c] for c in COUNTERS) + " |")
    out.append("|" + "---|" * len(COUNTERS))
    out.append("| " + " | ".join(_figure_cell(outside["usage"][c]) for c in COUNTERS) + " |")
    out += ["",
            "Codex jobs are counted in both figures. They are **not addends**, and "
            "no difference between them is a saving.",
            ""]
    if summary["denials"] is not None:
        out += [f"Permission denials across claude-backed jobs: **{summary['denials']}**. "
                "A denied tool call does not move a job's exit code.", ""]

    out += ["## Delegated jobs", "",
            "| Job | Role | Backend | Model | State | "
            + " | ".join(COUNTER_LABELS[c] for c in COUNTERS) + " | CLI-reported cost |",
            "|---|---|---|---|---|" + "---|" * (len(COUNTERS) + 1)]
    for job in payload["jobs"]:
        counters = " | ".join(md_cell(job["usage"][c]) for c in COUNTERS)
        cost_cell = "n/a - subscription" if job["backend"] == "codex" else (
            repr(job["cost_usd"]) if job["cost_usd"] is not None else "unknown")
        out.append(f"| `{md_cell(job['job_id'])}` | {md_cell(job['role'])} | "
                   f"{job['backend']} | {md_cell(job['model'])} | "
                   f"{job['state'].lower()} | {counters} | {cost_cell} |")

    driver = payload["driver"]
    out += ["", "## Driver session", "",
            f"State: {driver['state']} - {DRIVER_NOTE[driver['state']]}.",
            f"Models: {', '.join(driver['models']) or 'unknown'}. "
            "CLI-reported cost: unknown, the driver transcript carries no cost field.",
            "",
            "| " + " | ".join(COUNTER_LABELS[c] for c in COUNTERS) + " |",
            "|" + "---|" * len(COUNTERS),
            "| " + " | ".join(md_cell(driver["usage"][c]) for c in COUNTERS) + " |",
            "", "## Method", "",
            "Every number above was read from these files. Nothing is estimated, "
            "and no price table is applied.", ""]
    out += [f"- `{source}`" for source in payload["sources"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("receipt", nargs="?", default=None)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
