# Cost Receipt Implementation Plan

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

| File | Role |
|---|---|
| `scripts/render-cost-receipt.py` | Receipt loading, usage folding, interval derivation, summary, markdown renderer, CLI |
| `assets/cost-receipt.html` | Self-contained page with a `handoff-payload` slot, built on transcript viewer's tokens |
| `scripts/handoff_runtime.py` | Gained shared helpers: `read_meta`, `job_state`, `inject`, `PAYLOAD_PATTERN` |
| `scripts/render-transcript.py` | Imports shared helpers back from `handoff_runtime.py`; no behavior change |
| `tests/test_cost_receipt.py` | All spec-verification behaviors (74 tests) |
| `examples/v3.6.1-conversation-cost-receipt.{md,html}` | Generated from real job telemetry, pinned by tests |

---

### Task 1: Move the shared helpers into `handoff_runtime.py`

Moved `read_meta`, `_pid_alive`, `job_state`, `inject`, and `PAYLOAD_PATTERN` from `render-transcript.py` into `handoff_runtime.py` and imported them back. Existing transcript tests stayed green unmodified, proving the move was faithful.

---

### Task 2: Receipt loading and fail-closed validation

Created `scripts/render-cost-receipt.py` with `ReceiptError` and `load_receipt()`, implementing fail-closed receipt parsing: exactly one receipt block, no duplicate keys, schema version 5 only, safe path-segment job IDs, cross-list deduplication, count/entry reconciliation, and backend agreement with job meta.

---

### Task 3: Fold per-job usage from both backends

Added `fold_usage()` and `job_row()`. Codex usage sums across `turn.completed` and `turn.failed`; claude takes the last `result` record for cost, models, and denial count. Missing counters stay `None`, never zeroed. `model=inherit` chains resolve through `parent=` links.

---

### Task 4: The measurement interval and the driver row

Added `parse_duration()`, `run_interval()`, and `driver_row()`. The interval derives from the receipt filename stamp minus duration. Driver transcript located by escaped glob over `~/.claude/projects/*/`, filtered to the interval window. Underivable interval produces an `unscoped` row; missing transcript produces `unavailable`.

---

### Task 5: The two summary figures

Added `summarize()` producing two overlapping figures: `codex_subscription` (codex jobs, no cost) and `outside_driver` (all jobs, claude CLI cost). Incomplete columns carry a floor marker. The two figures are not addends and neither computes a saving.

---

### Task 6: The markdown renderer

Added `build_payload()`, `render_markdown()`, and `md_cell()`. The payload is an enumerated export: denial command strings are counted but never serialized. Both summary figures, the overlap statement, the delegated-jobs table, the driver row, and a method section naming every source file.

---

### Task 7: The HTML template

Created `assets/cost-receipt.html` with the transcript viewer's `light-dark()` tokens, a `handoff-payload` JSON slot, and `textContent`-only DOM insertion. Self-contained, no CDN, no `innerHTML`, closing-script escaping via `inject()`.

---

### Task 8: CLI wiring

Added `resolve_receipt()` and completed `main()`. Stamp-ordered selection (greatest filename, not mtime), outputs named for the receipt stamp under `.handoff/cost-receipts/`, `--no-open` flag, and exit code 2 for validation/resolution errors.

---

### Task 9: Docs, gates, and the version bump

Version bump to 3.6.1 across `SKILL.md`, `README.md`, `CHANGELOG.md`, `docs/releases/v3.6.1.md`. Added `check_file` entries, `py_compile` entries, and a test-prompt case for `/agent-handoff cost-receipt`.

---

### Task 10: Generate the shipped example and pin it to its sources

Generated `examples/v3.6.1-conversation-cost-receipt.{md,html}` from real job telemetry via the CLI. Added `ShippedExampleTests` pinning the example to its source job logs and asserting the export boundary. Added archive pointers to the v2.0.x files.

---

## Self-Review

**Pre-execution validation.** Every code block in this plan was extracted and run before the plan was committed: 74 tests, 68 passing, 6 failing only on artifacts the scratch tree lacked (shipped example files and the real template). Two defects surfaced and were fixed in the plan.

**One deviation from the spec, deliberate.** The spec puts only `read_meta()` into `handoff_runtime.py`. Task 1 also moves `job_state`, `_pid_alive`, `inject`, and `PAYLOAD_PATTERN`. Moving `inject` is what gives the new template the closing-script escaping the spec requires without a second implementation, and moving `job_state` is what makes "job state is not re-invented" literally true rather than a convention.

## Changelog

- 2026-09-09 — **Compacted post-implementation.** Removed step-by-step tasks, file-by-file diffs, code snippets, and verification commands now that the feature has shipped. Preserved Goal, Global Constraints, File Structure, task intent summaries, and the spec deviation note. Original plan recoverable via git history.
- 2026-09-09 — **`/interface-kit` pass on `assets/cost-receipt.html`.** Reworked the page and regenerated `examples/v3.6.1-conversation-cost-receipt.{md,html}` from `.handoff/receipts/receipt-20260908T173625Z.md`; every measured number unchanged. Palette and type now follow the v2.0.1 receipt's editorial language (serif masthead, cobalt figures, coral caveat rule, uppercase labels) carried onto `light-dark()` tokens, so the design doc's "the v2.0.1 palette is not revived" line is stale, on the repo owner's instruction. One behavioral fix: `DRIVER_NOTE[state]` travels in the payload from `build_payload()`, so `unscoped` is labelled in the HTML as well as the markdown instead of appearing as a bare word. Summary counters became label-over-figure blocks rather than a one-row table that scrolled. `main` uses `grid-template-columns: minmax(0, 1fr)` — an implicit `auto` column sizes to the jobs table's min-content and pushed the page sideways, 1482px of scroll width in a 430px viewport before the fix. Absent measurements (`unknown`, `n/a - subscription`, a partial `>= n (2 of 3)`) render muted italic, never in the figure face. Accessibility: `scope="col"` on every header, scroll containers as labelled keyboard-reachable regions with a focus ring, `tabular-nums` on numeric cells, job state as a tinted pill with the word inside, hover gated behind `(hover: hover)`, `prefers-reduced-motion` honored.
- 2026-09-09 — **Timestamps display in the system timezone.** The masthead's interval and generated stamps render through `toLocaleString(undefined, ...)`, each in a `<time>` whose `datetime` and `title` keep the payload's UTC original. Named component options, not `dateStyle`/`timeStyle`: combining those with `timeZoneName` throws `Invalid option` and blanks the page. The markdown output stays UTC ISO — it has no viewer to localize for, and a machine-dependent timestamp in a committed example would diff on every regeneration.
