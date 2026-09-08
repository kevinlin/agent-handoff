# Cost Receipt Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `/agent-handoff cost-receipt [<receipt-file>]` renders a markdown and an HTML cost receipt from a written Handoff Session Receipt plus the delegated job state it indexes, using measured telemetry only.

**Architecture:** One Python script parses and computes; two renderers consume the same payload dict. Shared job-state and payload-injection helpers move into `scripts/handoff_runtime.py` so the new script and `render-transcript.py` share one implementation. No receipt schema change, no edits to `make-receipt.py`, `validate-receipt.py`, or `delegate-codex.sh`.

**Tech Stack:** Python 3 standard library only. `unittest`. Self-contained HTML with inline CSS/JS, no CDN.

**Spec:** [docs/specs/design_cost-receipt.md](design_cost-receipt.md)

## Global Constraints

- **Standard library only.** No third-party imports in any script or test.
- **English only.** `scripts/english-only-scan.py` fails on CJK in any tracked file, UI strings included.
- **`from __future__ import annotations`** at the top of every new Python module, matching every existing script.
- **Version is `3.6.1`** in `SKILL.md` frontmatter, the README badge, `CHANGELOG.md`, and `docs/releases/v3.6.1.md`. Bump them together.
- **`receipt_schema_version` stays `5`.** This feature reads receipts and does not extend them.
- **Risky command text** (`git reset --hard`, `rm -rf`, `--force`) in docs is scanned by `check-skill-repo.sh`; genuine detection patterns need a `# risk-ok:` marker.
- **Never emit a savings, avoided-cost, or context-saved figure.** No price table. No cost figure for codex jobs or for the driver.
- **`total_cost_usd` is quoted verbatim and unrounded** everywhere it appears, including the shipped example.
- **Never zero a missing value.** Use the named states in the spec's degraded-states table. A measured zero prints `0`.

---

## File Structure

| File | Responsibility |
|---|---|
| `scripts/handoff_runtime.py` (modify) | Gains `read_meta`, `_pid_alive`, `job_state`, `PAYLOAD_PATTERN`, `inject` — moved verbatim from `render-transcript.py` |
| `scripts/render-transcript.py` (modify) | Imports those five back; no behavior change |
| `scripts/render-cost-receipt.py` (create) | Receipt loading and validation, usage folding, interval, summary, markdown, CLI |
| `assets/cost-receipt.html` (create) | Self-contained page with a `handoff-payload` slot |
| `tests/test_cost_receipt.py` (create) | Every behavior in the spec's Verification section |
| `examples/v3.6.1-conversation-cost-receipt.{md,html}` (create) | Generated from real jobs, asserted against their logs |

---

### Task 1: Move the shared helpers into `handoff_runtime.py`

Pure move. `render-transcript.py` keeps working and its tests stay green **unmodified** — that is the gate proving the move was faithful.

**Files:**
- Modify: `scripts/handoff_runtime.py`
- Modify: `scripts/render-transcript.py:1-76`, `scripts/render-transcript.py:156-167`
- Test: `tests/test_render_transcript.py` (run only, do not edit)

**Interfaces:**
- Consumes: nothing.
- Produces: `handoff_runtime.read_meta(job_dir: Path) -> dict[str, str]`, `handoff_runtime.job_state(job_dir: Path) -> str` returning one of `"CANCELLED" | "DONE" | "FAILED" | "RUNNING"`, `handoff_runtime.inject(template: str, payload: dict) -> str`, `handoff_runtime.PAYLOAD_PATTERN`.

- [ ] **Step 1: Run the existing suite to capture the green baseline**

Run: `python3 -m unittest tests.test_render_transcript -v`
Expected: PASS. Note the test count; it must be identical at the end of this task.

- [ ] **Step 2: Append the five members to `scripts/handoff_runtime.py`**

Add these imports at the top of the file, after `from __future__ import annotations`:

```python
import json
import os
import re
from pathlib import Path
from typing import Dict, Mapping
```

Append to the end of the file:

```python
PAYLOAD_PATTERN = re.compile(
    r'(<script id="handoff-payload" type="application/json">)(.*?)(</script>)', re.S)


def read_meta(job_dir: Path) -> Dict[str, str]:
    """Parse a job's `key=value` meta file. A missing file is not an error:
    resumed jobs legitimately omit backend and role."""
    meta: Dict[str, str] = {}
    path = job_dir / "meta"
    if not path.is_file():
        return meta
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            meta[key.strip()] = value.strip()
    return meta


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError) as error:
        return isinstance(error, PermissionError)
    return True


def job_state(job_dir: Path) -> str:
    """Mirror delegate-codex.sh job_state(). Absence of exit_code is never
    RUNNING on its own: a worker that died without writing one is FAILED."""
    if (job_dir / "cancelled").is_file():
        return "CANCELLED"
    exit_code = job_dir / "exit_code"
    if exit_code.is_file():
        return "DONE" if exit_code.read_text(encoding="utf-8").strip() == "0" else "FAILED"
    pid_file = job_dir / "pid"
    if pid_file.is_file():
        raw = pid_file.read_text(encoding="utf-8").strip()
        if raw.isdigit() and _pid_alive(int(raw)):
            return "RUNNING"
    return "FAILED"


def inject(template: str, payload: dict) -> str:
    """Replace the payload slot. Escaping `<` as \\u003c keeps any closing-script
    sequence inside captured output from ending the element early. This guards
    the element boundary only; DOM insertion is the page's own boundary."""
    if not PAYLOAD_PATTERN.search(template):
        raise ValueError("template has no handoff-payload slot")
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    # A lambda, not a replacement string: re.sub interprets backslashes in a
    # replacement, and the encoded payload is full of them.
    return PAYLOAD_PATTERN.sub(
        lambda m: m.group(1) + encoded + m.group(3), template, count=1)
```

- [ ] **Step 3: Delete the five originals from `render-transcript.py` and import them back**

Delete the `read_meta`, `_pid_alive`, `job_state` definitions (lines 44-76) and the `inject` definition plus `PAYLOAD_PATTERN` assignment. Then, after the existing `from pathlib import Path` line, add:

```python
SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
from handoff_runtime import PAYLOAD_PATTERN, inject, job_state, read_meta  # noqa: E402
```

Remove any now-unused imports from `render-transcript.py` (`os` and `re` are likely orphaned; keep `re` only if still referenced). Do not remove anything else.

- [ ] **Step 4: Run the transcript tests unmodified**

Run: `python3 -m unittest tests.test_render_transcript -v`
Expected: PASS, with the same test count as Step 1. `tests/test_render_transcript.py` loads the script by file path via `spec_from_file_location`; the `SCRIPT_DIR` idiom is what keeps that working.

- [ ] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/handoff_runtime.py scripts/render-transcript.py
git commit -m "refactor: move job-state and payload-injection helpers into handoff_runtime"
```

---

### Task 2: Receipt loading and fail-closed validation

**Files:**
- Create: `scripts/render-cost-receipt.py`
- Create: `tests/test_cost_receipt.py`

**Interfaces:**
- Consumes: `handoff_runtime.read_meta`.
- Produces: `ReceiptError(Exception)`; `load_receipt(text: str, repo: Path) -> dict` returning `{"fields": dict[str, str], "jobs": [{"job_id": str, "backend": str, "running": bool}]}`. Jobs are ordered codex entries first, then cc entries, each in receipt order.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_cost_receipt.py`:

```python
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render-cost-receipt.py"
SPEC = importlib.util.spec_from_file_location("render_cost_receipt", SCRIPT)
rcr = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = rcr
SPEC.loader.exec_module(rcr)


def receipt_text(**overrides) -> str:
    base = {
        "phase": "review",
        "claude_session": "abc123",
        "duration": "74min 05sec",
        "checks": "python3 -m unittest discover -s tests",
        "anomalies": "none",
        "codex_jobs": "1",
        "codex_job_durations": "job-a=1min 00sec",
        "cc_jobs": "1",
        "cc_job_durations": "job-b=2min 00sec",
        "scope": "project",
        "config_source": "project",
        "roles_used": "none",
        "receipt_schema_version": "5",
    }
    base.update(overrides)
    body = "\n".join(f"{k}: {v}" for k, v in base.items())
    return f"[Handoff session receipt]\n{body}\n"


def make_repo(tmp: Path, jobs: dict[str, str]) -> Path:
    """jobs maps jobId -> backend. Creates minimal job dirs."""
    for job_id, backend in jobs.items():
        job = tmp / ".handoff" / "jobs" / job_id
        job.mkdir(parents=True)
        (job / "meta").write_text(f"backend={backend}\nmodel=m\nrole=r\nlabel=l\n")
        (job / "exit_code").write_text("0\n")
    return tmp


class LoadReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        make_repo(self.tmp, {"job-a": "codex", "job-b": "claude"})

    def assert_refused(self, text, needle, repo=None):
        with self.assertRaises(rcr.ReceiptError) as caught:
            rcr.load_receipt(text, repo or self.tmp)
        self.assertIn(needle, str(caught.exception))

    def test_valid_receipt_loads_jobs_in_order(self):
        loaded = rcr.load_receipt(receipt_text(), self.tmp)
        self.assertEqual([j["job_id"] for j in loaded["jobs"]], ["job-a", "job-b"])
        self.assertEqual([j["backend"] for j in loaded["jobs"]], ["codex", "claude"])
        self.assertEqual([j["running"] for j in loaded["jobs"]], [False, False])

    def test_two_receipt_blocks_are_refused(self):
        self.assert_refused(receipt_text() + "\n" + receipt_text(), "exactly one")

    def test_duplicate_field_key_is_refused(self):
        self.assert_refused(receipt_text() + "phase: review\n", "duplicate field")

    def test_schema_version_four_is_refused(self):
        self.assert_refused(receipt_text(receipt_schema_version="4"), "schema_version")

    def test_job_id_with_path_separator_is_refused(self):
        self.assert_refused(
            receipt_text(codex_job_durations="../../etc/passwd=1min 00sec"), "path")

    def test_job_id_duplicated_across_lists_is_refused(self):
        self.assert_refused(
            receipt_text(cc_job_durations="job-a=2min 00sec"), "duplicate job")

    def test_count_disagreeing_with_entries_is_refused(self):
        self.assert_refused(receipt_text(codex_jobs="2"), "codex_jobs")

    def test_backend_disagreeing_with_meta_is_refused(self):
        self.assert_refused(
            receipt_text(codex_job_durations="job-b=1min 00sec",
                         cc_job_durations="job-a=2min 00sec"), "backend")

    def test_missing_job_directory_is_refused(self):
        self.assert_refused(
            receipt_text(codex_job_durations="job-gone=1min 00sec"), "job-gone")

    def test_running_entry_is_carried_not_dropped(self):
        loaded = rcr.load_receipt(receipt_text(cc_job_durations="job-b=running"), self.tmp)
        self.assertEqual([j["running"] for j in loaded["jobs"]], [False, True])

    def test_none_contributes_no_jobs(self):
        loaded = rcr.load_receipt(
            receipt_text(codex_jobs="0", codex_job_durations="none",
                         cc_jobs="0", cc_job_durations="none"), self.tmp)
        self.assertEqual(loaded["jobs"], [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: FAIL — the script does not exist yet.

- [ ] **Step 3: Write the minimal implementation**

Create `scripts/render-cost-receipt.py`:

```python
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("receipt", nargs="?", default=None)
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    args = parser.parse_args(argv)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: PASS, 11 tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/render-cost-receipt.py tests/test_cost_receipt.py
git commit -m "feat: fail-closed receipt loading for the cost receipt reader"
```

---

### Task 3: Fold per-job usage from both backends

**Files:**
- Modify: `scripts/render-cost-receipt.py`
- Modify: `tests/test_cost_receipt.py`

**Interfaces:**
- Consumes: `load_receipt`, `handoff_runtime.job_state`, `handoff_runtime.read_meta`.
- Produces: `COUNTERS: tuple[str, ...]` = `("input", "cache_read", "cache_write", "output", "reasoning")`; `fold_usage(events: list[dict], backend: str) -> dict` returning `{"usage": {counter: int | None}, "cost_usd": float | None, "denials": int | None, "models": list[str], "repeated": bool}`; `job_row(repo: Path, job: dict) -> dict` adding `job_id`, `label`, `role`, `backend`, `model`, `state`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cost_receipt.py`:

```python
CODEX_TURN = {"type": "turn.completed", "usage": {
    "input_tokens": 100, "cached_input_tokens": 60, "cache_write_input_tokens": 0,
    "output_tokens": 10, "reasoning_output_tokens": 4}}

CLAUDE_RESULT = {"type": "result", "total_cost_usd": 6.633623999999999,
                 "usage": {"input_tokens": 160, "cache_creation_input_tokens": 132345,
                           "cache_read_input_tokens": 8222548, "output_tokens": 47924,
                           "output_tokens_details": {"thinking_tokens": 6602}},
                 "modelUsage": {"claude-opus-5": {"costUSD": 6.633623999999999}},
                 "permission_denials": [{"tool_name": "Bash"}] * 11}


class FoldUsageTests(unittest.TestCase):
    def test_codex_usage_sums_across_turns(self):
        folded = rcr.fold_usage([CODEX_TURN, CODEX_TURN], "codex")
        self.assertEqual(folded["usage"]["input"], 200)
        self.assertEqual(folded["usage"]["reasoning"], 8)
        self.assertIsNone(folded["cost_usd"])

    def test_codex_sums_completed_and_failed_turns(self):
        failed = {"type": "turn.failed", "usage": {"input_tokens": 5, "output_tokens": 1}}
        folded = rcr.fold_usage([CODEX_TURN, failed], "codex")
        self.assertEqual(folded["usage"]["input"], 105)
        self.assertEqual(folded["usage"]["output"], 11)

    def test_claude_result_yields_cost_models_and_denials(self):
        folded = rcr.fold_usage([CLAUDE_RESULT], "claude")
        self.assertEqual(folded["cost_usd"], 6.633623999999999)
        self.assertEqual(folded["models"], ["claude-opus-5"])
        self.assertEqual(folded["denials"], 11)
        self.assertEqual(folded["usage"]["cache_read"], 8222548)
        self.assertEqual(folded["usage"]["reasoning"], 6602)

    def test_model_usage_is_never_added_to_usage(self):
        folded = rcr.fold_usage([CLAUDE_RESULT], "claude")
        self.assertEqual(folded["usage"]["input"], 160)

    def test_repeated_claude_result_takes_the_last_and_flags_it(self):
        second = dict(CLAUDE_RESULT, total_cost_usd=1.5)
        folded = rcr.fold_usage([CLAUDE_RESULT, second], "claude")
        self.assertEqual(folded["cost_usd"], 1.5)
        self.assertTrue(folded["repeated"])

    def test_no_terminal_record_yields_unknown_counters(self):
        folded = rcr.fold_usage([], "claude")
        self.assertEqual(set(folded["usage"].values()), {None})

    def test_missing_individual_counter_is_unknown_not_zero(self):
        partial = {"type": "turn.completed", "usage": {"input_tokens": 7}}
        folded = rcr.fold_usage([partial], "codex")
        self.assertEqual(folded["usage"]["input"], 7)
        self.assertIsNone(folded["usage"]["output"])

    def test_measured_zero_is_kept_as_zero(self):
        zeroed = {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}
        folded = rcr.fold_usage([zeroed], "codex")
        self.assertEqual(folded["usage"]["input"], 0)


class JobRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.jobs = self.tmp / ".handoff" / "jobs"

    def write_job(self, job_id, meta_lines, events=(), exit_code="0"):
        job = self.jobs / job_id
        job.mkdir(parents=True)
        (job / "meta").write_text("\n".join(meta_lines) + "\n")
        if events:
            (job / "log.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n")
        if exit_code is not None:
            (job / "exit_code").write_text(exit_code + "\n")
        return job

    def test_done_codex_job_row(self):
        self.write_job("job-a", ["backend=codex", "model=gpt-6-astra",
                                 "role=deep_reasoner", "label=spec"], [CODEX_TURN])
        row = rcr.job_row(self.tmp, {"job_id": "job-a", "backend": "codex", "running": False})
        self.assertEqual(row["state"], "DONE")
        self.assertEqual(row["model"], "gpt-6-astra")
        self.assertEqual(row["usage"]["input"], 100)

    def test_no_exit_code_and_no_pid_is_failed_not_running(self):
        self.write_job("job-x", ["backend=codex", "model=m"], exit_code=None)
        row = rcr.job_row(self.tmp, {"job_id": "job-x", "backend": "codex", "running": False})
        self.assertEqual(row["state"], "FAILED")

    def test_cancelled_job(self):
        job = self.write_job("job-c", ["backend=codex", "model=m"], exit_code=None)
        (job / "cancelled").write_text("")
        row = rcr.job_row(self.tmp, {"job_id": "job-c", "backend": "codex", "running": False})
        self.assertEqual(row["state"], "CANCELLED")

    def test_truncated_jsonl_yields_unknown_not_a_crash(self):
        job = self.write_job("job-t", ["backend=codex", "model=m"])
        (job / "log.jsonl").write_text('{"type": "turn.compl')
        row = rcr.job_row(self.tmp, {"job_id": "job-t", "backend": "codex", "running": False})
        self.assertIsNone(row["usage"]["input"])

    def test_model_inherit_resolves_through_parent(self):
        self.write_job("job-p", ["backend=claude", "model=opus"])
        self.write_job("job-p-r2", ["backend=claude", "model=inherit", "parent=job-p"])
        row = rcr.job_row(self.tmp, {"job_id": "job-p-r2", "backend": "claude", "running": False})
        self.assertEqual(row["model"], "opus")

    def test_model_inherit_without_parent_is_named_unresolved(self):
        self.write_job("job-o", ["backend=claude", "model=inherit", "parent=gone"])
        row = rcr.job_row(self.tmp, {"job_id": "job-o", "backend": "claude", "running": False})
        self.assertEqual(row["model"], "inherit (unresolved)")

    def test_job_without_backend_line_is_codex_by_construction(self):
        self.write_job("job-legacy", ["model=m"], [CODEX_TURN])
        row = rcr.job_row(self.tmp, {"job_id": "job-legacy", "backend": "codex", "running": False})
        self.assertEqual(row["usage"]["input"], 100)
```

Add `import json` to the test file's imports.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'fold_usage'`.

- [ ] **Step 3: Write the implementation**

Insert into `scripts/render-cost-receipt.py`, after `load_receipt`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/render-cost-receipt.py tests/test_cost_receipt.py
git commit -m "feat: fold per-job usage from codex and claude job logs"
```

---

### Task 4: The measurement interval and the driver row

**Files:**
- Modify: `scripts/render-cost-receipt.py`
- Modify: `tests/test_cost_receipt.py`

**Interfaces:**
- Consumes: `COUNTERS`, `_blank_usage`, `_add`.
- Produces: `parse_duration(value: str) -> int` (seconds); `run_interval(receipt_path: Path | None, duration: str) -> tuple[datetime, datetime] | None`; `driver_row(session_id: str, interval, projects_root: Path) -> dict` returning `{"state": "measured" | "unscoped" | "unavailable", "usage": dict, "models": list[str], "cost_usd": None}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cost_receipt.py`:

```python
from datetime import datetime, timedelta, timezone


def transcript_line(ts: str, tokens: int) -> str:
    return json.dumps({
        "type": "assistant", "timestamp": ts,
        "message": {"model": "claude-opus-5",
                    "usage": {"input_tokens": tokens, "output_tokens": 1}}})


class IntervalTests(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(rcr.parse_duration("74min 05sec"), 74 * 60 + 5)
        self.assertEqual(rcr.parse_duration("0min 00sec"), 0)

    def test_saved_receipt_name_yields_an_interval(self):
        path = Path("/x/.handoff/receipts/receipt-20260908T155001Z.md")
        start, end = rcr.run_interval(path, "11min 01sec")
        self.assertEqual(end, datetime(2026, 9, 8, 15, 50, 1, tzinfo=timezone.utc))
        self.assertEqual((end - start).total_seconds(), 661)

    def test_unstamped_input_has_no_interval(self):
        self.assertIsNone(rcr.run_interval(Path("/x/notes.md"), "11min 01sec"))


class DriverRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.projects = self.tmp / "projects"
        (self.projects / "slug").mkdir(parents=True)
        self.path = self.projects / "slug" / "sid-1.jsonl"
        self.path.write_text("\n".join([
            transcript_line("2026-09-08T15:00:00.000Z", 10),   # before
            transcript_line("2026-09-08T15:45:00.000Z", 100),  # inside
            transcript_line("2026-09-08T16:30:00.000Z", 1000),  # after
        ]) + "\n")
        self.interval = (datetime(2026, 9, 8, 15, 39, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 8, 15, 50, 1, tzinfo=timezone.utc))

    def test_interval_scopes_the_row(self):
        row = rcr.driver_row("sid-1", self.interval, self.projects)
        self.assertEqual(row["state"], "measured")
        self.assertEqual(row["usage"]["input"], 100)
        self.assertEqual(row["models"], ["claude-opus-5"])
        self.assertIsNone(row["cost_usd"])

    def test_transcript_growth_after_the_receipt_does_not_change_the_row(self):
        before = rcr.driver_row("sid-1", self.interval, self.projects)
        with self.path.open("a") as handle:
            handle.write(transcript_line("2026-09-08T17:00:00.000Z", 9999) + "\n")
        self.assertEqual(rcr.driver_row("sid-1", self.interval, self.projects), before)

    def test_two_intervals_in_one_session_give_different_rows(self):
        later = (datetime(2026, 9, 8, 16, 0, 0, tzinfo=timezone.utc),
                 datetime(2026, 9, 8, 17, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(rcr.driver_row("sid-1", later, self.projects)["usage"]["input"], 1000)

    def test_no_interval_is_unscoped_and_totals_the_session(self):
        row = rcr.driver_row("sid-1", None, self.projects)
        self.assertEqual(row["state"], "unscoped")
        self.assertEqual(row["usage"]["input"], 1110)

    def test_missing_transcript_is_unavailable(self):
        row = rcr.driver_row("sid-absent", self.interval, self.projects)
        self.assertEqual(row["state"], "unavailable")
        self.assertEqual(set(row["usage"].values()), {None})

    def test_session_id_is_a_literal_not_a_glob(self):
        row = rcr.driver_row("sid-*", self.interval, self.projects)
        self.assertEqual(row["state"], "unavailable")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: FAIL with `AttributeError: ... 'parse_duration'`.

- [ ] **Step 3: Write the implementation**

Add `import glob` and `from datetime import datetime, timedelta, timezone` to the script's imports, then insert after `job_row`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/render-cost-receipt.py tests/test_cost_receipt.py
git commit -m "feat: scope the driver row to the run interval"
```

---

### Task 5: The two summary figures

**Files:**
- Modify: `scripts/render-cost-receipt.py`
- Modify: `tests/test_cost_receipt.py`

**Interfaces:**
- Consumes: `COUNTERS`, job rows from `job_row`.
- Produces: `summarize(rows: list[dict]) -> dict` returning `{"codex_subscription": Figure, "outside_driver": Figure, "denials": int | None}` where `Figure` is `{"usage": {counter: {"value": int | None, "complete": bool, "measured": int, "total": int}}, "cost_usd": float | None, "cost_complete": bool, "jobs": int}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cost_receipt.py`:

```python
def row(job_id, backend, input_tokens, cost=None, state="DONE", denials=None):
    usage = {c: None for c in rcr.COUNTERS}
    usage["input"] = input_tokens
    return {"job_id": job_id, "label": "", "role": "", "backend": backend,
            "model": "m", "state": state, "usage": usage, "cost_usd": cost,
            "denials": denials, "models": [], "repeated": False}


class SummaryTests(unittest.TestCase):
    def test_codex_jobs_appear_in_both_figures(self):
        summary = rcr.summarize([row("a", "codex", 100), row("b", "claude", 10, cost=2.5)])
        self.assertEqual(summary["codex_subscription"]["usage"]["input"]["value"], 100)
        self.assertEqual(summary["codex_subscription"]["jobs"], 1)
        self.assertEqual(summary["outside_driver"]["usage"]["input"]["value"], 110)
        self.assertEqual(summary["outside_driver"]["jobs"], 2)

    def test_codex_figure_never_carries_a_cost(self):
        summary = rcr.summarize([row("a", "codex", 100)])
        self.assertIsNone(summary["codex_subscription"]["cost_usd"])

    def test_cost_is_the_sum_of_claude_jobs_only_and_unrounded(self):
        summary = rcr.summarize([row("a", "codex", 1),
                                 row("b", "claude", 1, cost=6.633623999999999)])
        self.assertEqual(summary["outside_driver"]["cost_usd"], 6.633623999999999)

    def test_the_two_figures_are_not_addends(self):
        summary = rcr.summarize([row("a", "codex", 100), row("b", "claude", 10)])
        combined = (summary["codex_subscription"]["usage"]["input"]["value"]
                    + summary["outside_driver"]["usage"]["input"]["value"])
        self.assertNotEqual(summary["outside_driver"]["usage"]["input"]["value"], combined)

    def test_an_unmeasured_row_marks_the_column_incomplete(self):
        summary = rcr.summarize([row("a", "codex", 100),
                                 row("b", "codex", None, state="RUNNING")])
        column = summary["codex_subscription"]["usage"]["input"]
        self.assertEqual(column["value"], 100)
        self.assertFalse(column["complete"])
        self.assertEqual((column["measured"], column["total"]), (1, 2))

    def test_a_fully_measured_column_is_complete(self):
        column = rcr.summarize([row("a", "codex", 100)])["codex_subscription"]["usage"]["input"]
        self.assertTrue(column["complete"])

    def test_missing_cost_on_a_claude_job_marks_cost_incomplete(self):
        summary = rcr.summarize([row("b", "claude", 1, cost=None)])
        self.assertFalse(summary["outside_driver"]["cost_complete"])

    def test_denials_sum_across_claude_jobs(self):
        summary = rcr.summarize([row("b", "claude", 1, denials=11),
                                 row("c", "claude", 1, denials=2)])
        self.assertEqual(summary["denials"], 13)

    def test_denials_are_none_when_no_claude_job_reported_any(self):
        self.assertIsNone(rcr.summarize([row("a", "codex", 1)])["denials"])
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: FAIL with `AttributeError: ... 'summarize'`.

- [ ] **Step 3: Write the implementation**

Insert after `driver_row`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/render-cost-receipt.py tests/test_cost_receipt.py
git commit -m "feat: two overlapping summary figures, never blended"
```

---

### Task 6: The markdown renderer

**Files:**
- Modify: `scripts/render-cost-receipt.py`
- Modify: `tests/test_cost_receipt.py`

**Interfaces:**
- Consumes: `summarize`, job rows, `driver_row`.
- Produces: `build_payload(receipt: dict, rows: list[dict], driver: dict, interval, sources: list[str]) -> dict`; `render_markdown(payload: dict) -> str`; `md_cell(value) -> str`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cost_receipt.py`:

```python
class MarkdownTests(unittest.TestCase):
    def payload(self, rows=None, driver=None):
        rows = rows if rows is not None else [
            row("job-a", "codex", 779279), row("job-b", "claude", 160, cost=6.633623999999999)]
        driver = driver or {"state": "measured", "usage": {c: None for c in rcr.COUNTERS},
                            "models": ["claude-opus-5"], "cost_usd": None}
        return rcr.build_payload(
            {"fields": {"claude_session": "sid-1", "duration": "11min 01sec"}},
            rows, driver, None, ["/x/.handoff/jobs/job-a/log.jsonl"])

    def test_both_figures_are_present_with_their_labels(self):
        out = rcr.render_markdown(self.payload())
        self.assertIn("Ran on a Codex subscription", out)
        self.assertIn("Ran outside the driver session", out)

    def test_cost_is_unrounded(self):
        self.assertIn("6.633623999999999", rcr.render_markdown(self.payload()))

    def test_cost_is_labelled_cli_reported(self):
        self.assertIn("CLI-reported cost", rcr.render_markdown(self.payload()))

    def test_no_savings_language_anywhere(self):
        out = rcr.render_markdown(self.payload()).lower()
        for banned in ("saved", "savings", "avoided", "cheaper", "instead of"):
            self.assertNotIn(banned, out)

    def test_the_overlap_is_stated(self):
        self.assertIn("not addends", rcr.render_markdown(self.payload()))

    def test_no_line_equals_the_sum_of_the_two_figures(self):
        out = rcr.render_markdown(self.payload())
        self.assertNotIn(str(779279 + 779279 + 160), out)

    def test_incomplete_column_renders_a_floor(self):
        rows = [row("job-a", "codex", 100), row("job-b", "codex", None, state="RUNNING")]
        self.assertIn("≥ 100", rcr.render_markdown(self.payload(rows=rows)))

    def test_unscoped_driver_row_is_labelled(self):
        driver = {"state": "unscoped", "usage": {c: None for c in rcr.COUNTERS},
                  "models": [], "cost_usd": None}
        self.assertIn("unscoped", rcr.render_markdown(self.payload(driver=driver)))

    def test_pipe_in_a_label_is_escaped(self):
        self.assertEqual(rcr.md_cell("a|b"), "a\\|b")

    def test_none_renders_as_unknown(self):
        self.assertEqual(rcr.md_cell(None), "unknown")

    def test_measured_zero_renders_as_zero(self):
        self.assertEqual(rcr.md_cell(0), "0")

    def test_denial_command_strings_never_reach_the_output(self):
        rows = [row("job-b", "claude", 1, denials=11)]
        out = rcr.render_markdown(self.payload(rows=rows))
        self.assertIn("11", out)
        self.assertNotIn("curl", out)
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: FAIL with `AttributeError: ... 'build_payload'`.

- [ ] **Step 3: Write the implementation**

Insert after `summarize`:

```python
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
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/render-cost-receipt.py tests/test_cost_receipt.py
git commit -m "feat: markdown cost receipt renderer"
```

---

### Task 7: The HTML template

**Files:**
- Create: `assets/cost-receipt.html`
- Modify: `tests/test_cost_receipt.py`

**Interfaces:**
- Consumes: `handoff_runtime.inject`, the payload from `build_payload`.
- Produces: a template containing `<script id="handoff-payload" type="application/json">{}</script>`.

Copy the `:root` token block, `body`, `main`, card, and table rules from `assets/transcript-viewer.html:26-90` rather than inventing a palette. Use `light-dark()` for every colour. Wrap every table in `<div style="overflow-x:auto">`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cost_receipt.py`:

```python
TEMPLATE = ROOT / "assets" / "cost-receipt.html"


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.html = TEMPLATE.read_text(encoding="utf-8")

    def test_template_has_a_payload_slot(self):
        self.assertIn('<script id="handoff-payload" type="application/json">', self.html)

    def test_template_is_self_contained(self):
        for banned in ("http://", "https://cdn", "<link rel=\"stylesheet\""):
            self.assertNotIn(banned, self.html)

    def test_template_supports_both_themes(self):
        self.assertIn("light-dark(", self.html)

    def test_page_never_uses_inner_html(self):
        self.assertNotIn("innerHTML", self.html)

    def test_closing_script_in_a_value_cannot_break_out_of_the_slot(self):
        hostile = 'x </script><img src=x onerror=alert(1)>'
        out = rcr.inject(self.html, {"claude_session": hostile})
        self.assertNotIn("</script><img", out)
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            out, re.S)
        self.assertEqual(json.loads(match.group(1))["claude_session"], hostile)
```

Add `import re` to the test file's imports.

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_cost_receipt.TemplateTests -v`
Expected: FAIL — the template does not exist.

- [ ] **Step 3: Write the template**

Create `assets/cost-receipt.html`. Structure (fill the CSS from the transcript viewer's tokens):

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="color-scheme" content="light dark">
<title>Handoff Cost Receipt</title>
<style>
:root {
  color-scheme: light dark;
  --bg:     light-dark(#fbfbfa, #16161a);
  --fg:     light-dark(#1a1a19, #e8e8e6);
  --muted:  light-dark(#6b6b68, #9a9a96);
  --line:   light-dark(#e3e3e0, #2c2c31);
  --card:   light-dark(#ffffff, #1e1e23);
  --accent: light-dark(#4a5cd6, #8b9aff);
  --shadow-card: 0 1px 2px rgb(0 0 0 / .04), 0 6px 18px rgb(0 0 0 / .05);
  --radius-card: 16px;
  --mono: ui-monospace, SFMono-Regular, Menlo, monospace;
  --gutter: clamp(1rem, 3vw, 2.5rem);
}
body { margin:0; background:var(--bg); color:var(--fg);
  font:14px/1.6 ui-sans-serif, system-ui, -apple-system, sans-serif; }
main { max-width:72rem; margin:0 auto; padding:1.5rem var(--gutter) 6rem; }
.card { border:1px solid var(--line); border-radius:var(--radius-card);
  background:var(--card); padding:1rem 1.25rem; margin-bottom:1.25rem;
  box-shadow:var(--shadow-card); }
.scroll { overflow-x:auto; }
table { border-collapse:collapse; width:100%; font:13px/1.5 var(--mono); }
th, td { text-align:right; padding:.4rem .6rem; border-bottom:1px solid var(--line);
  white-space:nowrap; }
th:first-child, td:first-child { text-align:left; }
th { color:var(--muted); font-weight:500; }
.note { color:var(--muted); font-size:12px; }
.figure { font:600 20px/1.2 var(--mono); color:var(--accent); }
h1 { font-size:20px; margin:0 0 .25rem; }
h2 { font-size:15px; margin:0 0 .75rem; color:var(--muted); }
dt { color:var(--muted); font:11px var(--mono); }
dd { margin:0 0 .5rem; font:13px var(--mono); }
</style>
</head>
<body>
<main id="root"></main>
<script id="handoff-payload" type="application/json">{}</script>
<script>
const data = JSON.parse(document.getElementById("handoff-payload").textContent);
const LABELS = {input:"Input", cache_read:"Cache read", cache_write:"Cache write",
                output:"Output", reasoning:"Reasoning"};
const el = (tag, text, cls) => {
  const node = document.createElement(tag);
  if (text !== undefined && text !== null) node.textContent = String(text);
  if (cls) node.className = cls;
  return node;
};
const num = (v) => v === null || v === undefined ? "unknown" : v.toLocaleString();
const figureCell = (c) => c.value === null ? "unknown"
  : (c.complete ? num(c.value) : `\u2265 ${num(c.value)} (${c.measured} of ${c.total})`);

function table(headers, rows) {
  const wrap = el("div", null, "scroll");
  const t = el("table");
  const thead = el("thead"), hr = el("tr");
  headers.forEach(h => hr.appendChild(el("th", h)));
  thead.appendChild(hr); t.appendChild(thead);
  const tb = el("tbody");
  rows.forEach(r => {
    const tr = el("tr");
    r.forEach(cell => tr.appendChild(el("td", cell)));
    tb.appendChild(tr);
  });
  t.appendChild(tb); wrap.appendChild(t);
  return wrap;
}

function render() {
  const root = document.getElementById("root");
  const counters = data.counters;
  const head = el("section", null, "card");
  head.appendChild(el("h1", "Handoff Cost Receipt"));
  const dl = el("dl");
  const meta = [["session", data.claude_session], ["run duration", data.duration],
    ["interval", data.interval ? `${data.interval[0]} to ${data.interval[1]}`
                               : "not derivable from this input"],
    ["generated", data.generated_at]];
  meta.forEach(([k, v]) => { dl.appendChild(el("dt", k)); dl.appendChild(el("dd", v)); });
  head.appendChild(dl);
  root.appendChild(head);

  [["Ran on a Codex subscription", data.summary.codex_subscription, false],
   ["Ran outside the driver session", data.summary.outside_driver, true]
  ].forEach(([title, fig, withCost]) => {
    const card = el("section", null, "card");
    card.appendChild(el("h2", `${title} \u00b7 ${fig.jobs} jobs`));
    card.appendChild(el("p", withCost
      ? `CLI-reported cost of the claude-backed jobs: ${fig.cost_usd === null ? "unknown"
          : fig.cost_usd + (fig.cost_complete ? "" : " (partial)")}`
      : "No cost figure: the Codex CLI emits none.", "note"));
    card.appendChild(table(counters.map(c => LABELS[c]),
      [counters.map(c => figureCell(fig.usage[c]))]));
    root.appendChild(card);
  });

  const overlap = el("section", null, "card");
  overlap.appendChild(el("p", "Codex jobs are counted in both figures above. They are "
    + "not addends, and no difference between them is a saving.", "note"));
  if (data.summary.denials !== null) {
    overlap.appendChild(el("p", `Permission denials across claude-backed jobs: `
      + `${data.summary.denials}. A denied tool call does not move a job's exit code.`,
      "note"));
  }
  root.appendChild(overlap);

  const jobs = el("section", null, "card");
  jobs.appendChild(el("h2", "Delegated jobs"));
  jobs.appendChild(table(
    ["Job", "Role", "Backend", "Model", "State", ...counters.map(c => LABELS[c]),
     "CLI-reported cost"],
    data.jobs.map(j => [j.job_id, j.role || "-", j.backend, j.model, j.state.toLowerCase(),
      ...counters.map(c => num(j.usage[c])),
      j.backend === "codex" ? "n/a - subscription"
        : (j.cost_usd === null ? "unknown" : String(j.cost_usd))])));
  root.appendChild(jobs);

  const driver = el("section", null, "card");
  driver.appendChild(el("h2", "Driver session"));
  driver.appendChild(el("p", `State: ${data.driver.state}. Models: `
    + `${data.driver.models.join(", ") || "unknown"}. CLI-reported cost: unknown, `
    + "the driver transcript carries no cost field.", "note"));
  driver.appendChild(table(counters.map(c => LABELS[c]),
    [counters.map(c => num(data.driver.usage[c]))]));
  root.appendChild(driver);

  const method = el("section", null, "card");
  method.appendChild(el("h2", "Method"));
  method.appendChild(el("p", "Every number above was read from these files. Nothing is "
    + "estimated, and no price table is applied.", "note"));
  const ul = el("ul");
  data.sources.forEach(s => ul.appendChild(el("li", s)));
  method.appendChild(ul);
  root.appendChild(method);
}
render();
</script>
</body>
</html>
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_cost_receipt -v`
Expected: PASS.

- [ ] **Step 5: Run the interface-kit polish pass**

Invoke the `interface-kit` skill against `assets/cost-receipt.html`. Constraint: it refines spacing, hierarchy, and type scale **within the transcript viewer's existing tokens**. It must not introduce a new palette, a web font, a CDN reference, `innerHTML`, or any external request. Re-run `python3 -m unittest tests.test_cost_receipt.TemplateTests` after — those tests are the guard.

- [ ] **Step 6: Commit**

```bash
git add assets/cost-receipt.html tests/test_cost_receipt.py
git commit -m "feat: cost receipt HTML template on the transcript viewer's tokens"
```

---

### Task 8: CLI wiring

**Files:**
- Modify: `scripts/render-cost-receipt.py`
- Modify: `tests/test_cost_receipt.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `resolve_receipt(repo: Path, selector: str | None) -> Path`; `main(argv) -> int` returning `0` on success, `2` on a resolution or validation error.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_cost_receipt.py`:

```python
import contextlib
import io


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        job = self.tmp / ".handoff" / "jobs" / "job-a"
        job.mkdir(parents=True)
        (job / "meta").write_text("backend=codex\nmodel=m\nrole=r\nlabel=l\n")
        (job / "exit_code").write_text("0\n")
        (job / "log.jsonl").write_text(json.dumps(CODEX_TURN) + "\n")
        self.receipts = self.tmp / ".handoff" / "receipts"
        self.receipts.mkdir(parents=True)
        self.text = receipt_text(codex_jobs="1", codex_job_durations="job-a=1min 00sec",
                                 cc_jobs="0", cc_job_durations="none")

    def write(self, name):
        path = self.receipts / name
        path.write_text(self.text)
        return path

    def run_cli(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = rcr.main([*args, "--repo", str(self.tmp), "--no-open"])
        return code, err.getvalue()

    def test_last_picks_the_greatest_stamp_not_the_newest_mtime(self):
        old = self.write("receipt-20260908T100000Z.md")
        new = self.write("receipt-20260908T155001Z.md")
        Path(old).touch()  # older stamp, newer mtime
        self.assertEqual(rcr.resolve_receipt(self.tmp, None), new)

    def test_explicit_missing_path_exits_two(self):
        code, err = self.run_cli(str(self.tmp / "nope.md"))
        self.assertEqual(code, 2)
        self.assertIn("no such receipt file", err)

    def test_writes_both_outputs_named_for_the_stamp(self):
        self.write("receipt-20260908T155001Z.md")
        code, _ = self.run_cli()
        self.assertEqual(code, 0)
        out = self.tmp / ".handoff" / "cost-receipts"
        self.assertTrue((out / "20260908T155001Z.md").is_file())
        self.assertTrue((out / "20260908T155001Z.html").is_file())

    def test_arbitrary_input_is_named_for_its_basename(self):
        other = self.tmp / "notes.md"
        other.write_text(self.text)
        self.assertEqual(self.run_cli(str(other))[0], 0)
        self.assertTrue((self.tmp / ".handoff" / "cost-receipts" / "notes.md").is_file())

    def test_no_receipt_and_no_selector_exits_two(self):
        code, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("`make-receipt.py --save`", err)

    def test_invalid_receipt_exits_two_and_writes_nothing(self):
        self.text = receipt_text(receipt_schema_version="4")
        self.write("receipt-20260908T155001Z.md")
        code, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("schema_version", err)
        self.assertFalse((self.tmp / ".handoff" / "cost-receipts").exists())

    def test_rerun_overwrites_in_place(self):
        self.write("receipt-20260908T155001Z.md")
        self.run_cli()
        out = self.tmp / ".handoff" / "cost-receipts" / "20260908T155001Z.md"
        first = out.read_text()
        self.run_cli()
        self.assertIn("Handoff Cost Receipt", out.read_text())
        self.assertEqual(first.splitlines()[0], out.read_text().splitlines()[0])

    def test_html_output_carries_the_payload(self):
        self.write("receipt-20260908T155001Z.md")
        self.run_cli()
        html = (self.tmp / ".handoff" / "cost-receipts" / "20260908T155001Z.html").read_text()
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            html, re.S)
        self.assertEqual(json.loads(match.group(1))["jobs"][0]["job_id"], "job-a")
```

- [ ] **Step 2: Run to verify it fails**

Run: `python3 -m unittest tests.test_cost_receipt.CliTests -v`
Expected: FAIL with `AttributeError: ... 'resolve_receipt'`.

- [ ] **Step 3: Write the implementation**

Add `import webbrowser` to the imports, add the template constant near the top:

```python
DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "cost-receipt.html"
```

Replace `main` with:

```python
def resolve_receipt(repo: Path, selector: str | None) -> Path:
    if selector and selector != "last":
        path = Path(selector)
        if not path.is_file():
            raise ReceiptError(f"no such receipt file: {selector}")
        return path
    # Ordered by stamp, not mtime: the stamp is the receipt's own generation
    # time and survives a copy. Names in one directory are unique, so the
    # greatest stamp is unambiguous.
    saved = sorted((repo / ".handoff" / "receipts").glob("receipt-*.md"),
                   key=lambda p: p.name)
    if not saved:
        raise ReceiptError(
            "no saved receipt found under .handoff/receipts/. "
            "Generate one with `make-receipt.py --save`.")
    return saved[-1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("receipt", nargs="?", default=None,
                        help="Path to a receipt file, or 'last' (default).")
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="Repository root (default: current directory).")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE)
    parser.add_argument("--no-open", action="store_true",
                        help="Write the pages and print their paths instead of opening.")
    args = parser.parse_args(argv)

    try:
        receipt_path = resolve_receipt(args.repo, args.receipt)
        receipt = load_receipt(receipt_path.read_text(encoding="utf-8"), args.repo)
    except (ReceiptError, OSError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    rows = [job_row(args.repo, job) for job in receipt["jobs"]]
    interval = run_interval(receipt_path, receipt["fields"].get("duration", "0min 00sec"))
    session = receipt["fields"].get("claude_session", "none")
    driver = (driver_row(session, interval) if session != "none"
              else {"state": "unavailable", "usage": _blank_usage(),
                    "models": [], "cost_usd": None})

    sources = [str(receipt_path)] + [
        str(args.repo / ".handoff" / "jobs" / r["job_id"] / "log.jsonl") for r in rows]
    payload = build_payload(receipt, rows, driver, interval, sources)

    stamp_match = RECEIPT_STAMP.match(receipt_path.name)
    stem = stamp_match.group(1) if stamp_match else receipt_path.stem
    out_dir = args.repo / ".handoff" / "cost-receipts"
    out_dir.mkdir(parents=True, exist_ok=True)
    md_path = out_dir / f"{stem}.md"
    html_path = out_dir / f"{stem}.html"
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    html_path.write_text(
        inject(args.template.read_text(encoding="utf-8"), payload), encoding="utf-8")

    print(md_path)
    print(html_path)
    if not args.no_open:
        webbrowser.open(html_path.as_uri())
    return 0
```

- [ ] **Step 4: Run the full suite**

Run: `python3 -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/render-cost-receipt.py tests/test_cost_receipt.py
git commit -m "feat: cost-receipt CLI with stamp-ordered selection"
```

---

### Task 9: Docs, gates, and the version bump

**Files:**
- Modify: `SKILL.md:2` (frontmatter `version`), `SKILL.md:4` (description), and a new section after `## Transcript`
- Modify: `README.md`, `CHANGELOG.md`, `scripts/check-skill-repo.sh`, `test-prompts.json`, `.github/workflows/checks.yml:21`
- Create: `docs/releases/v3.6.1.md`

- [ ] **Step 1: Add the SKILL.md section and bump the version**

Set `version: 3.6.1` in the frontmatter. Append to the `description`, before the "Not for ordinary code review" sentence:

```
"/agent-handoff cost-receipt" or "cost receipt" (renders a session receipt and its job state as a measured cost report),
```

Add after the `## Transcript` section:

```markdown
## Cost Receipt

On `/agent-handoff cost-receipt [<receipt-file>]`, or when the user asks what a
handoff run cost or consumed, render it and open it:

```bash
python3 "$HANDOFF_DIR/scripts/render-cost-receipt.py" [<receipt-file>] --repo "$REPO"
```

The selector is optional; omitted or `last` means the newest saved receipt under
`.handoff/receipts/`. It writes a markdown and an HTML page to
`.handoff/cost-receipts/`.

A bare request for "the receipt" is the Handoff Session Receipt above, not this.
Only "cost receipt", or an explicit question about what the run consumed, routes
here.

Every number is measured: codex jobs carry token counters and no cost figure,
because that work runs on a subscription; claude jobs carry the CLI's own
`total_cost_usd`, quoted unrounded and labelled CLI-reported. The two summary
figures overlap by design and are never added. Do not report a saving, an
avoided cost, or context kept out of the driver — no counter establishes any of
them. This renders a page; it delegates nothing and starts no job.
```

- [ ] **Step 2: Update README, CHANGELOG, and the release doc**

- README: version badge `3.6.0` → `3.6.1`; add a File Map row for `scripts/render-cost-receipt.py` and `assets/cost-receipt.html`; on the line describing the v2.0.x receipts, mark them archives and point at `examples/v3.6.1-conversation-cost-receipt.md` as current. **Do not remove the `assets/v2.0.1-conversation-cost-receipt.png` link** — [`check-skill-repo.sh:150`](../../scripts/check-skill-repo.sh) fails without it.
- `CHANGELOG.md`: a `## v3.6.1 (2026-09-09)` section above v3.6.0.
- `docs/releases/v3.6.1.md`: follow the shape of `docs/releases/v3.6.0.md` (`## What Changed`, `## Untrusted Input`, `## Release Gate`). Under Untrusted Input, name the receipt file as untrusted text and the enumerated export boundary.

- [ ] **Step 3: Add the required-file checks**

In `scripts/check-skill-repo.sh`, beside the existing `check_file` lines:

```bash
check_file "scripts/render-cost-receipt.py"
check_file "assets/cost-receipt.html"
check_file "examples/v3.6.1-conversation-cost-receipt.md"
check_file "examples/v3.6.1-conversation-cost-receipt.html"
```

- [ ] **Step 4: Add the compile check**

In `.github/workflows/checks.yml:21`, append `scripts/render-cost-receipt.py` and `scripts/render-transcript.py` to the `py_compile` list. `render-transcript.py` was already missing from it; adding it here is a one-word fix to an existing gap.

- [ ] **Step 5: Add the test prompts**

In `test-prompts.json`, add one case:

```json
{
  "id": "cost-receipt-renders-not-delegates",
  "prompt": "/agent-handoff cost-receipt",
  "expected_behavior": [
    "run scripts/render-cost-receipt.py against the newest saved receipt",
    "report both summary figures and state that codex jobs appear in both",
    "label the claude cost as CLI-reported"
  ],
  "must_not": [
    "delegate a job or start a background worker",
    "report a savings, avoided-cost, or context-saved figure",
    "estimate a cost for codex jobs or for the driver session"
  ]
}
```

Add to the `must_not` list of `session-receipt-required`, `full-review-receipt-gate`, and `receipt-splits-jobs-by-backend`:

```json
"render a cost receipt instead of the Handoff Session Receipt"
```

- [ ] **Step 6: Run every gate**

```bash
python3 -m unittest discover -s tests
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
python3 scripts/english-only-scan.py
```

Expected: all pass, except `check-skill-repo.sh` failing on the two missing `examples/v3.6.1-*` files. That is correct: Task 10 creates them.

- [ ] **Step 7: Run the declawed pass on the prose**

Invoke the `declawed` skill on the SKILL.md section, the CHANGELOG entry, and `docs/releases/v3.6.1.md`. Fix findings, re-scan, then commit.

- [ ] **Step 8: Commit**

```bash
git add SKILL.md README.md CHANGELOG.md docs/releases/v3.6.1.md \
        scripts/check-skill-repo.sh test-prompts.json .github/workflows/checks.yml
git commit -m "docs: cost receipt surface and v3.6.1 version bump"
```

---

### Task 10: Generate the shipped example and pin it to its sources

**Files:**
- Create: `examples/v3.6.1-conversation-cost-receipt.md`, `examples/v3.6.1-conversation-cost-receipt.html`
- Modify: `examples/v2.0.0-conversation-cost-receipt.md`, `examples/v2.0.1-conversation-cost-receipt.md`
- Modify: `tests/test_cost_receipt.py`

- [ ] **Step 1: Produce a real receipt indexing real jobs**

The marker must be stamped **before** the jobs run; `make-receipt.py:62` excludes any job submitted earlier.

```bash
python3 scripts/make-receipt.py --start --repo .
# run at least one codex-backed and one claude-backed job via delegate-codex.sh
python3 scripts/make-receipt.py --repo . --phase "delegated implementation" \
  --claude-session <sid> --checks "python3 -m unittest discover -s tests" \
  --anomalies none --codex-jobs <n> --cc-jobs <m> \
  --scope project --config-source project --roles-used '<json>' --save
```

Read the saved receipt and confirm its `codex_job_durations` / `cc_job_durations` name the jobs you expect. `--codex-jobs` supplies a count, it does not select jobs, so a receipt can be valid and index nothing.

- [ ] **Step 2: Render and copy into `examples/`**

```bash
python3 scripts/render-cost-receipt.py --repo . --no-open
cp .handoff/cost-receipts/<stamp>.md   examples/v3.6.1-conversation-cost-receipt.md
cp .handoff/cost-receipts/<stamp>.html examples/v3.6.1-conversation-cost-receipt.html
```

Read the markdown before committing. Confirm no denial command strings, no absolute home directory paths in the Method list beyond the repo-relative parts, and no savings language.

- [ ] **Step 3: Write the test that pins the example to its sources**

Append to `tests/test_cost_receipt.py`:

```python
EXAMPLE_MD = ROOT / "examples" / "v3.6.1-conversation-cost-receipt.md"
EXAMPLE_HTML = ROOT / "examples" / "v3.6.1-conversation-cost-receipt.html"


class ShippedExampleTests(unittest.TestCase):
    def test_both_example_files_exist(self):
        self.assertTrue(EXAMPLE_MD.is_file())
        self.assertTrue(EXAMPLE_HTML.is_file())

    def test_example_states_both_figures(self):
        text = EXAMPLE_MD.read_text(encoding="utf-8")
        self.assertIn("Ran on a Codex subscription", text)
        self.assertIn("Ran outside the driver session", text)
        self.assertIn("not addends", text)

    def test_example_claims_no_saving(self):
        text = EXAMPLE_MD.read_text(encoding="utf-8").lower()
        for banned in ("saved", "savings", "avoided", "cheaper"):
            self.assertNotIn(banned, text)

    def test_example_html_payload_matches_the_markdown_job_ids(self):
        html = EXAMPLE_HTML.read_text(encoding="utf-8")
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            html, re.S)
        payload = json.loads(match.group(1))
        text = EXAMPLE_MD.read_text(encoding="utf-8")
        for job in payload["jobs"]:
            self.assertIn(job["job_id"], text)

    def test_example_exports_no_denial_command_strings(self):
        html = EXAMPLE_HTML.read_text(encoding="utf-8")
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            html, re.S)
        for job in json.loads(match.group(1))["jobs"]:
            self.assertNotIn("tool_input", json.dumps(job))
            self.assertIsInstance(job["denials"], (int, type(None)))
```

- [ ] **Step 4: Add the archive pointers**

Add one line under the schema-v2 banner in each of `examples/v2.0.0-conversation-cost-receipt.md` and `examples/v2.0.1-conversation-cost-receipt.md`:

```markdown
> Superseded by [`v3.6.1-conversation-cost-receipt.md`](v3.6.1-conversation-cost-receipt.md),
> which is generated from measured job telemetry. This one was written by hand.
```

- [ ] **Step 5: Run every gate**

```bash
python3 -m unittest discover -s tests
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
python3 scripts/english-only-scan.py
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

Expected: all pass, including the `check-skill-repo.sh` lines added in Task 9.

- [ ] **Step 6: Open the HTML and check it by eye**

Open `examples/v3.6.1-conversation-cost-receipt.html` in a browser, in light mode and dark mode. Confirm the tables scroll inside their own container rather than pushing the page sideways, no text clips, and every number matches the files named in the Method section.

- [ ] **Step 7: Commit**

```bash
git add examples/
git commit -m "docs: shipped cost receipt example generated from real job telemetry"
```

---

## Self-Review

**Spec coverage.** Command contract → Task 8. Input validation → Task 2. Measurement interval → Task 4. Reading the run → Tasks 2-3. What each backend yields → Task 3. Summary and its prohibitions → Tasks 5-6. Page structure and export boundary → Tasks 6-7. Changes 1-2 → Tasks 2-8. Change 3 → Task 1. Changes 4-6, 8-9, 11 → Task 9. Change 7 → Task 10. Change 10 → every task. All Verification test groups are placed: input validation (2), measurement (3), interval (4), degraded states (3, 5), reporting contract (6), export boundary (7).

**Placeholders.** The only `<placeholder>` values are in Task 10, where the session id, job counts, and roles come from a live run that cannot be known in advance. Every code step carries runnable code.

**Type consistency.** `COUNTERS` is defined once in Task 3 and used verbatim in Tasks 4-8. `_blank_usage` and `_add` are defined in Task 3 and consumed in Task 4. `CLAUDE_FIELDS` is shared by `fold_usage` and `driver_row`. Job-row keys (`job_id`, `label`, `role`, `backend`, `model`, `state`, `usage`, `cost_usd`, `denials`, `models`, `repeated`) match across Tasks 3, 5, 6, and 8. `Figure` keys (`usage`, `cost_usd`, `cost_complete`, `jobs`) match between Task 5 and Task 6. `ReceiptError` is raised in Tasks 2, 4, and 8 and caught once in `main`.

**The code in this plan was executed before the plan was committed.** Every `python` block was extracted into a scratch tree and the plan's own tests were run against the plan's own implementation: 74 tests, 68 passing. The 6 that fail are exactly the artifacts the scratch tree lacks — the five `ShippedExampleTests`, which Task 10 creates, and `test_template_supports_both_themes`, which needs the real template from Task 7. Two defects surfaced and are already fixed above: `test_no_receipt_and_no_selector_exits_two` asserted a substring the implementation did not emit, and `resolve_receipt` carried a same-stamp branch that filenames in a single directory make unreachable.

**One deviation from the spec, deliberate.** The spec puts only `read_meta()` into `handoff_runtime.py`. Task 1 also moves `job_state`, `_pid_alive`, `inject`, and `PAYLOAD_PATTERN`. Moving `inject` is what gives the new template the closing-script escaping the spec requires without a second implementation, and moving `job_state` is what makes "job state is not re-invented" literally true rather than a convention.
