# Add session duration to the Handoff Session Receipt

## Context

The receipt (`[Handoff session receipt]`, schema v3) records what a handoff run did — phase, jobs, checks, roles — but not how long it took. Wall-clock duration is the one number a reader can't reconstruct from the receipt, and it's the number that captures the cost the model doesn't see: time spent blocked on human permission prompts.

Outcome: every receipt carries `duration` (overall, wall-clock, human waits included) and `codex_job_durations` (per delegated job), both computed from repo evidence — never recalled or estimated. Format is LLM-parseable: `74min 12sec`.

Decisions already made:
- Start anchor: a `.handoff/session-start` marker written at Phase 0 preflight. Measures the handoff run, not the whole CLI session.
- Schema: `duration` and `codex_job_durations` are **required**; `receipt_schema_version` 3 → 4. A v3 receipt fails validation, which is the upgrade signal — the same pattern the repo used for v2 → v3.
- Per-job durations included, auto-derived. No changes to `delegate-codex.sh`.

## Design

**Start marker.** `scripts/make-receipt.py --start --repo <path>` writes `<repo>/.handoff/session-start` containing a UTC ISO timestamp, overwriting any previous value (a fresh Phase 0 means a fresh run). `/agent-handoff resume` re-enters mid-flow and never runs Phase 0, so a resumed run keeps the original start.

**Overall duration.** At receipt time, `duration = now − marker`. `--started-at <ISO>` overrides the marker (needed where no marker exists). Neither present → refuse to emit, same as any other invalid field. Start in the future → refuse (broken clock, not a 0-second run).

**Per-job durations.** Derived from state `delegate-codex.sh` already writes: `submitted_at=` in `.handoff/jobs/<jobId>/meta`, and the mtime of that job's `exit_code` as the end. Jobs whose `submitted_at` predates the session start are skipped, so previous runs' job dirs don't leak in. A job with no `exit_code` yet renders as `running`.

**Format.** One shared formatter: `f"{m}min {s:02d}sec"`. Validator regex `^\d+min [0-5]\dsec$` — exact, since only the generator writes these.

```text
[Handoff session receipt]
phase: review
claude_session: c5d355c5-...
duration: 74min 12sec
checks: ...
anomalies: none
codex_jobs: 4
codex_job_durations: job-2026-08-23T09-12-00-1234-t1=12min 04sec; job-2026-08-23T09-12-00-1234-t1-r2=3min 41sec
scope: project
config_source: project
roles_used: [...]
receipt_schema_version: 4
```

## Changes

**Engine**

- [scripts/make-receipt.py](scripts/make-receipt.py) — add `--start` mode (relax the `required=True` args and validate them manually when not starting); read the marker or `--started-at`; add the duration formatter; scan job dirs for `codex_job_durations`; emit both fields in the order above.
- [scripts/validate-receipt.py](scripts/validate-receipt.py) — `REQUIRED_FIELDS` gains both fields; duration regex; `codex_job_durations` accepts `none` or `key=value` pairs split on `"; "`; version const 3 → 4.
- [docs/receipt-schema.json](docs/receipt-schema.json) — `$id` `handoff.receipt.v4`, both properties with descriptions, `required` list, `receipt_schema_version` const 4, and a top-level description saying what v4 adds and that a v3 receipt fails as the upgrade signal.

**Flow prose** (product surface — CI gates it)

- [references/claude-driven.md](references/claude-driven.md) — Phase 0 gains the marker step; Phase 5 notes both duration fields come from `make-receipt.py --repo`, not recall.
- [SKILL.md](SKILL.md) — receipt block and the paragraph after it; state that duration is wall-clock and therefore includes time blocked on human approvals; frontmatter `version: 3.0.0` → `3.1.0`.
- [README.md](README.md) — version badge, the receipt example block, and the File Map line for `receipt-schema.json` (also says "schema v2 archive" for `examples/session-receipt.md` today, which is already stale — correct it to v4 while editing that line).
- [CHANGELOG.md](CHANGELOG.md) — `## v3.1.0 (2026-08-23)` entry noting the breaking receipt bump.
- [examples/session-receipt.md](examples/session-receipt.md) — add both lines and bump to v4. This is a real past session with no recorded timing, so the two values are backfilled; say so in one line of the prose, and let the next real run replace the example.

**Gates**

- [.github/workflows/checks.yml](.github/workflows/checks.yml) — the receipt roundtrip runs `make-receipt.py --start --repo .` first (also exercises the new mode). `.handoff/` is gitignored, so the checkout stays clean.
- [CLAUDE.md](CLAUDE.md) — same two-line roundtrip in the documented commands.
- [scripts/check-skill-repo.sh](scripts/check-skill-repo.sh) — "v3 schema" message → v4; add `duration` to the SKILL.md receipt-contract grep so the field can't silently drop out of the docs.
- [test-prompts.json](test-prompts.json) — `session-receipt-required` gains one `expected_behavior` ("take the duration from `.handoff/session-start` through make-receipt.py") and one `must_not` ("estimate or round the duration from memory").

**Tests** — [tests/test_receipt.py](tests/test_receipt.py)

- Base `fields()` gains both new fields; existing version test flips to "must be 4".
- Duration format: reject `74 min`, `74min 5sec` (unpadded), `74min 99sec`, empty.
- `--start` writes the marker; a following receipt run emits a duration that validates.
- No marker and no `--started-at` → exit 1, no receipt on stdout (mirrors `test_invalid_arguments_refuse_to_emit_a_receipt`).
- `codex_job_durations` from fake job dirs in a temp repo: a finished job, a job with no `exit_code` (`running`), and a job submitted before the marker (excluded).

Prose edits (SKILL.md, README.md, CHANGELOG.md, examples/, references/) get a `declawed` pass before the change is called done.

## Verification

```bash
python3 -m unittest discover -s tests
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py

# end-to-end in a scratch repo
R=$(mktemp -d)
python3 scripts/make-receipt.py --start --repo "$R"
python3 scripts/make-receipt.py --repo "$R" --phase review --claude-session x \
  --checks ci --codex-jobs 0 --scope project --config-source project --roles-used '[]' \
  | python3 scripts/validate-receipt.py -

python3 scripts/validate-receipt.py examples/session-receipt.md   # v4 example still passes
```

Manual check that the number is real: run `--start`, wait a measurable interval (including one permission prompt), then generate — the emitted `duration` should match the wall clock, prompt wait included.

## Skipped

- No changes to `delegate-codex.sh` — `submitted_at` plus `exit_code` mtime already carry per-job timing. Add an explicit `finished_at` only if mtime turns out to be unreliable.
- No wall-clock breakdown per phase, and no separate "time blocked on human" field. Both need instrumentation that doesn't exist; the ask was overall duration.
