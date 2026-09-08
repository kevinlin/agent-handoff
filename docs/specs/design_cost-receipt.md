# Cost receipt for a handoff session

## Context

The Handoff Session Receipt records what a run *did*: phase, jobs, durations, checks, roles. It does not record what the run *consumed*. That question currently has two bad answers in the repo: the workload model in `docs/showcase-cost-model.md`, which is explicitly illustrative, and `examples/v2.0.*-conversation-cost-receipt.md`, hand-written narratives whose central figure is a counterfactual (`$64.195302` "avoided Fable spend") derived by repricing observed Codex tokens at Anthropic list rates. `CLAUDE.md` forbids exactly that move. Both archives carry a schema-v2 banner and are kept as history, not as templates.

The premise has since changed. Delegated jobs now retain real usage telemetry, so the consumption question can be answered by measurement instead of by argument.

Outcome: `/agent-handoff cost-receipt [<receipt-file>]` reads a written session receipt plus the job state it indexes, and renders a markdown and an HTML cost receipt containing only measured numbers, each labelled with what it does and does not establish.

### Three findings that shaped the design

**Both backends retain usage. Only one retains a cost figure.** Verified against the two real jobs in this repo's `.handoff/jobs/`:

| Backend | Event | Fields | Cost |
| --- | --- | --- | --- |
| codex | `turn.completed.usage` | `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`, `reasoning_output_tokens` | none. The work runs on a subscription and no per-run price is emitted |
| claude | terminal `type: result` | `total_cost_usd`, `usage`, per-model `modelUsage` with `costUSD` and `costBasis`, plus `permission_denials` | yes, reported by the CLI at `costBasis: list` |

Measured values from those two jobs, quoted exactly as the logs carry them:

| Job | Backend | Model | Input | Cache read | Output | CLI cost |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `job-…-spec-review` | codex | `gpt-6-astra` | 779,279 | 673,664 | 9,530 | none |
| `job-…-transcript-impl` | claude | `claude-opus-5` | 160 | 8,222,548 | 47,924 | 6.633623999999999 |

The second job also recorded 11 `permission_denials` while exiting 0. That is the v3.5.1 failure mode, still legible in the log.

**The session receipt indexes the run, but does not by itself bound it.** `claude_session` names the driver transcript and the jobId keys in `codex_job_durations` / `cc_job_durations` name every delegated job, fix rounds included. That settles which jobs belong to the run. It does not settle *when* the run started and stopped, which the driver row needs, because a session can carry work from before and after this receipt. See "The measurement interval".

**Session ids are globally unique, so the driver transcript needs no path derivation.** `~/.claude/projects/*/<claude_session>.jsonl` resolves in one glob, with the session id escaped as a literal rather than passed through as glob syntax. Deriving Claude Code's directory slug from the repo path would encode an undocumented rule this repo cannot test.

### Decisions already made

- **Generated from measurement, never composed.** A script emits the whole artifact. There is no prose section a driver fills in by hand, because a hand-typed cost figure is the failure the session receipt's generator-only rule already exists to prevent.
- **Driver side included, tokens only, scoped to the run.** The driver transcript carries `usage` and `model` but no cost field. Its cost column reads `unknown` rather than being computed from a checked-in price table.
- **Validate before reading, and fail closed.** The input is a receipt written by a generator, but the reader treats it as untrusted text. See "Input validation".
- **`cost receipt`, never bare `receipt`.** `SKILL.md` routes the bare word to the session receipt.
- **Two reported figures, never one blended number,** and neither of them called a saving. See "The summary, and what it must not claim".
- **No receipt schema change.** `receipt_schema_version` stays 5. The cost receipt reads receipts; it does not extend them.
- **The example is versioned like the archives.** `examples/v3.6.1-conversation-cost-receipt.{md,html}`, matching the `v2.0.0` / `v2.0.1` convention of naming the file for the skill version that produced it.

## Design

### Artifacts

- `scripts/render-cost-receipt.py` — parses, computes, writes both outputs.
- `assets/cost-receipt.html` — the page template with a `handoff-payload` JSON slot.
- `examples/v3.6.1-conversation-cost-receipt.{md,html}` — generated from real jobs, never hand-edited.

One Python parser feeds two renderers. The transcript viewer puts all parsing in JavaScript so a dropped log needs no second parser; that reasoning does not carry over here, because a markdown output requires the parsing to exist in Python regardless, and duplicating it in JavaScript would be two implementations of the same arithmetic.

### Command contract

```bash
python3 "$HANDOFF_DIR/scripts/render-cost-receipt.py" [<receipt-file>] --repo "$REPO" [--no-open]
```

- The selector is optional. Omitted, or the literal `last`, means the newest file matching `.handoff/receipts/receipt-*.md`, where **newest is the greatest filename stamp**, not mtime — the stamp is the receipt's own generation time and is stable across file copies.
- An explicit path to any file containing a `[Handoff session receipt]` block also resolves.
- Two receipt files carrying the same stamp is an error listing both, the same refuse-rather-than-guess rule `render-transcript.py` applies to ambiguous job selectors.
- No saved receipt and no selector is an error telling the user to run `make-receipt.py --save`.
- **Output naming.** `<repo>/.handoff/cost-receipts/<stamp>.{md,html}` where `<stamp>` is the receipt's filename stamp. An input outside `.handoff/receipts/` has no stamp, so it is named `<input-basename>.{md,html}`. Existing files are overwritten, because the output is a pure function of inputs that do not change; a hand-edited output is not something to protect.

### Input validation

The reader runs `validate-receipt.py`'s own validation first, then adds the checks a consumer needs that a writer does not. A receipt that fails any of these is an error naming the failure, and nothing is written.

Reused from the validator: field presence, `duration` grammar, integer job counts, the `JOB_DURATIONS` grammar, and `receipt_schema_version`. The version must be exactly `5`; a v4 receipt is refused rather than partially read.

Added here, because `extract_block` at [`validate-receipt.py:60`](../../scripts/validate-receipt.py) takes the first header it finds and lets a later duplicate field silently overwrite an earlier one:

- **Exactly one receipt block.** More than one `[Handoff session receipt]` header in the file is an error; the first-wins behavior is fine for a writer checking its own output and wrong for a reader deciding what a document means.
- **No duplicate field keys** inside the block.
- **`jobId=running` entries are valid input.** The validator's `_JOB_ENTRY` accepts `running` alongside a duration, and `tests/test_receipt.py` asserts it. A running job contributes a row with no usage, never an omission and never a zero.
- **Path containment.** The validator's jobId grammar is `[^=;]+`, which admits `/` and `..`. A jobId must be a single path segment that resolves inside `<repo>/.handoff/jobs/`; anything else is refused before any file is opened.
- **No duplicate jobIds**, within either list or across the two.
- **Counts reconcile.** `codex_jobs` must equal the number of entries in `codex_job_durations`, and likewise for `cc_*`. The validator does not cross-check these; a disagreement means the receipt is describing something other than what it indexes.
- **Backend agreement.** A jobId listed under `codex_job_durations` whose `meta` says `backend=claude` is an error, not a silent reclassification.
- **Every indexed job directory exists.** Missing ones are named. There is no fallback scan of `.handoff/jobs/`: two sources of truth about run membership would eventually disagree.
- **`claude_session` is a literal.** It is escaped before use in the transcript glob.

### The measurement interval

The driver row needs a window, and the receipt carries `duration` but no absolute timestamps. Two cases:

- **A receipt saved by `make-receipt.py --save`** is named `receipt-<YYYYMMDDTHHMMSSZ>.md`, and [`make-receipt.py:173`](../../scripts/make-receipt.py) builds that stamp from the same `now` the receipt is generated at. So the stamp is the run's end, `duration` is its span, and the interval is `[stamp − duration, stamp]`. Driver entries are filtered by their ISO `timestamp` field, which every usage-bearing message carries.
- **Any other input** has no derivable end. The driver row is then reported as `unscoped` and labelled in both outputs as a whole-session total that may include work outside this run. It is never silently presented as the run's driver cost.

This is the reason the driver row can be trusted at all: a session id locates a transcript, it does not bound a measurement. Two runs sharing one session produce two different driver rows, and re-rendering an old receipt after the session grew produces the same row it produced the first time.

### Reading the run

1. Resolve and validate the receipt per "Input validation".
2. Collect jobIds from both duration fields. `none` contributes nothing; a `=running` entry contributes a job with no usage.
3. For each job, read `meta` for backend, model, role, and label, and derive its state.
4. Fold `log.jsonl` for usage per "What each backend yields".
5. Resolve the driver transcript and its interval, when `claude_session` is not `none`.

**Job state is not re-invented.** `render-transcript.py:60` already mirrors `delegate-codex.sh`'s `job_state()` and documents the rule this design originally got wrong: absence of `exit_code` is never `RUNNING` on its own, because a worker that died without writing one is `FAILED`. The four states are `CANCELLED`, `DONE`, `FAILED`, `RUNNING`, and `job_state()` is the single implementation.

**Metadata has real gaps to name, not to paper over.** A resumed job writes `model=inherit` ([`delegate-codex.sh:436`](../../scripts/delegate-codex.sh)), so its model is resolved by walking `parent=` to the originating job; if that fails it reads `inherit (unresolved)`, never a guessed model name. A job directory written before backend dispatch carries no `backend=` line and is codex by construction, the same rule `make-receipt.py:62` already applies. Neither case drops the job's consumption from the report.

`read_meta()` moves into `scripts/handoff_runtime.py`, imported through the `SCRIPT_DIR` / `sys.path` idiom already used at [`handoff-setup.py:22`](../../scripts/handoff-setup.py). That idiom keeps working when the importing script is itself loaded by file path, which is how `tests/test_render_transcript.py:12` loads it.

Note: `examples/session-receipt.md` carries placeholder jobIds (`job-t1`) that exist nowhere on disk, so it is not usable as input. That is the fail-closed rule working, and a standing argument for replacing that example with a real run.

### What each backend yields

**Counters are reported, never combined.** Each backend's counters go into their own columns and no column is derived by adding others. Whether codex's `cached_input_tokens` is a subset of its `input_tokens` is not established by the sample logs, and inventing a "total tokens" figure would bake that guess into every row. Claude's counters are disjoint in the observed data (`input_tokens: 160` alongside `cache_read_input_tokens: 8222548`), which is a second reason the two backends' columns are presented side by side rather than summed into one number.

| Source | Field | Column |
| --- | --- | --- |
| codex `turn.completed` / `turn.failed` | `input_tokens` | input |
| | `cached_input_tokens` | cache read |
| | `cache_write_input_tokens` | cache write |
| | `output_tokens` | output |
| | `reasoning_output_tokens` | reasoning |
| claude `result` | `usage.input_tokens` | input |
| | `usage.cache_read_input_tokens` | cache read |
| | `usage.cache_creation_input_tokens` | cache write |
| | `usage.output_tokens` | output |
| | `usage.output_tokens_details.thinking_tokens` | reasoning |
| | `total_cost_usd` | CLI cost |
| driver transcript | `message.usage.*`, same five | same five |

**`usage` and `modelUsage` are never both counted.** `usage` supplies every token figure and `total_cost_usd` supplies the cost; `modelUsage` is read only for its model names. Summing per-model entries on top of the aggregate would double every number.

**Codex usage is summed across turns**, over both `turn.completed` and `turn.failed`. The sample jobs have one turn each, so additivity across multiple turns is an assumption, not an observation; it is recorded here as such and asserted by a fixture test with two turns.

**Degraded states are named, never zeroed.** A measured zero is a measurement and is printed as `0`.

| Situation | Renders |
| --- | --- |
| job still running | `running` |
| job cancelled | `cancelled` |
| job failed | `failed`, with whatever usage the log did record |
| log truncated, unparseable, or carrying no terminal usage record | `unknown` |
| an individual counter absent from an otherwise valid record | `unknown` for that cell only |
| driver transcript not found | `unavailable` |
| driver interval not derivable | `unscoped` |

**A partial total is never presented as a total.** When any row in a column is `unknown`, `running`, `cancelled`, or `failed`, that column's summary reads `≥ <subtotal> (n of m jobs measured)`. A repeated usage record for the same job is taken once, by last occurrence, and flagged in the method note.

### The summary, and what it must not claim

Three claims were in an earlier draft of this design and are not supportable by these measurements. The design states them here so they do not reappear:

- **Not context savings.** Delegated token counts do not measure context the driver avoided. They cannot show what execution history the driver later read, and cumulative processing tokens are not context-window occupancy.
- **Not billed spend.** `modelUsage.costBasis` reads `list`. The figure is what the CLI reported, and is labelled *CLI-reported cost* everywhere it appears, in the table, the summary, and the example.
- **Not a saving.** A claude-backed delegated job bills the same vendor as the driver, so no difference between the two figures is a saving, and none is computed.

The summary therefore reports two figures whose populations deliberately overlap, and says so:

- **Ran on a Codex subscription** — token counters for codex-backed jobs, no cost figure, because none is emitted.
- **Ran outside the driver session** — token counters for all delegated jobs, either backend, plus the summed CLI-reported cost of the claude-backed ones.

Codex jobs appear in both. The outputs state that the two figures are not addends, and the tests assert it.

A third line reports `permission_denials` across claude-backed jobs. A denied tool call does not move a job's exit code, so a job can report success on checks it was refused, and the count is the cheapest way to surface that.

### Page structure and the export boundary

`assets/cost-receipt.html` inherits the transcript viewer's `light-dark()` token block, card idiom, mono stack, and documented tint-and-ink contrast pairs. One product, one visual language. The v2.0.1 page's light-only cobalt and coral palette is not revived. An `/interface-kit` pass polishes against those tokens rather than proposing a second design system.

Layout, top to bottom: header (session, receipt source, interval and how it was derived, generated time), the two summary figures with their overlap note, the delegated-jobs table, the driver row, the denials line, and a method note naming every file the numbers came from.

**What crosses into the output is enumerated, not implied.** Exported per job: jobId, label, role, backend, model, state, the five counters, CLI cost, and the denial *count*. Denial command strings are read to count them and are never written to either output. That is a narrower boundary than the transcript viewer needs, and it is what makes the committed example artifact safe to publish.

**Escaping is not optional and is already solved here.** The payload goes through the same `inject()` closing-script escaping that `tests/test_render_transcript.py:198` regression-tests, with an equivalent test for this template. The page inserts every value with `textContent`, never `innerHTML`. Labels and paths are markdown-escaped on the markdown side, since a jobId label reaches the file from a `--label` a user chose.

The gitignore warning from `render-transcript.py:216` still applies to generated outputs under `.handoff/`. It is a warning about local files and protects nothing about the deliberately committed example, which the enumerated export boundary above is what makes safe.

## Not doing

- No price table, and no cost figure for the driver or for codex. A repriced token is the fabrication the repo already rules out. A checked-in rate card would also go stale silently.
- No receipt schema change, and no edits to `make-receipt.py`, `validate-receipt.py`, or `delegate-codex.sh`.
- No changes to `showcase-cost-ledger.py` or its ledger. The workload model stays the README's illustrative comparison, separate from this measured artifact.
- No rewrite of `examples/v2.0.*`. They record a real v2.0.1 run under a schema that no longer exists; restating them in the current name and schema would require inventing a receipt block that `validate-receipt.py` is built to reject. Each gets one pointer line to the current example.
- No aggregation across runs. One receipt in, one cost receipt out.

## Changes

1. **`scripts/render-cost-receipt.py`** (new). Validation, job resolution, per-backend folding, interval derivation, driver glob, markdown writer, payload injection, browser open.
2. **`assets/cost-receipt.html`** (new). Template with the payload slot, built on the transcript viewer's tokens.
3. **`scripts/handoff_runtime.py`**: gains `read_meta()` and `job_state()`, moved from `render-transcript.py` and imported back into it through the `SCRIPT_DIR` idiom. No behavior change; `tests/test_render_transcript.py` must stay green unmodified.
4. **`SKILL.md`**: a Cost Receipt section beside Transcript; frontmatter `version: 3.6.0` → `3.6.1`; description gains the trigger phrases while bare `receipt` keeps pointing at the session receipt.
5. **`test-prompts.json`**: one case for `/agent-handoff cost-receipt`; a `must_not` on the three existing session-receipt cases; a `must_not` against reporting a savings figure. **These are prompt-file structure checks.** [`run-test-prompts.py:25`](../../scripts/run-test-prompts.py) validates the case file, it does not exercise model behavior, so these record the intended contract rather than enforcing it. Enforcement of the reporting rules lives in `tests/test_cost_receipt.py`.
6. **`scripts/check-skill-repo.sh`**: `check_file` for `scripts/render-cost-receipt.py`, `assets/cost-receipt.html`, `examples/v3.6.1-conversation-cost-receipt.md`, and `examples/v3.6.1-conversation-cost-receipt.html`.
7. **`examples/v3.6.1-conversation-cost-receipt.{md,html}`**: generated, not hand-written. Pointer lines added to both v2.0.x archives.
8. **`README.md`**: version badge; File Map; the line describing the v2.0.x receipts distinguishes archive from current. **The `assets/v2.0.1-conversation-cost-receipt.png` link must survive that edit** — [`check-skill-repo.sh:150`](../../scripts/check-skill-repo.sh) fails if README stops linking it.
9. **`CHANGELOG.md`**: `## v3.6.1`; **`docs/releases/v3.6.1.md`** per convention.
10. **`tests/test_cost_receipt.py`** (new).
11. **`.gitignore`**: confirm `.handoff/` already covers `cost-receipts/`.

Prose edits get a `declawed` pass before the change is called done.

## Verification

```bash
python3 -m unittest discover -s tests
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
python3 scripts/english-only-scan.py
```

**End to end, in the order that actually measures something.** The marker must be stamped *before* the jobs run: [`make-receipt.py:62`](../../scripts/make-receipt.py) excludes any job whose `submitted_at` predates the start, so stamping after the fact produces a valid receipt indexing nothing.

```bash
python3 scripts/make-receipt.py --start --repo .
# ... run at least one codex-backed and one claude-backed job ...
python3 scripts/make-receipt.py --repo . --phase review --claude-session <sid> \
  --checks ci --codex-jobs 1 --cc-jobs 1 --scope project --config-source project \
  --roles-used '[]' --save
python3 scripts/render-cost-receipt.py --repo . --no-open
```

`--codex-jobs` / `--cc-jobs` supply counts, they do not select jobs, so the generated receipt is inspected for the expected jobIds in its duration fields before it is used as input. The shipped example is produced by this same path from a saved receipt indexing real jobs, and the example's own totals are asserted against those job logs by a test, so a stale example fails CI rather than drifting quietly.

Test cases in `tests/test_cost_receipt.py`, using fake job dirs in a temp repo in the style of `tests/test_receipt.py`:

*Input validation* — two receipt blocks in one file; a duplicate field key; `receipt_schema_version: 4`; a jobId containing `../`; a jobId duplicated across the two lists; `codex_jobs: 2` against one duration entry; a jobId listed as codex whose `meta` says `backend=claude`; a missing job directory. Each exits non-zero, names the failure, and writes nothing.

*Measurement* — codex usage summed across two `turn.completed` events, and across `turn.completed` plus `turn.failed`; claude cost, model, and denial count from the terminal `result`; `modelUsage` present but never added to `usage`; a repeated usage record counted once.

*Interval* — a saved receipt scoping the driver row to `[stamp − duration, stamp]`; two receipts from one session producing different driver rows; a transcript that grew after the receipt producing an unchanged row; a non-`.handoff/receipts/` input producing an `unscoped` row labelled as such.

*Degraded states* — `=running` in the receipt; a `cancelled` file; no `exit_code` and no live pid rendering `failed`, not `running`; truncated JSONL; a missing individual counter; `model=inherit` resolved through `parent=`, and unresolved when the parent is gone; a job with no `backend=` line treated as codex. A measured zero prints `0`. Any column containing a non-measured row prints `≥ subtotal (n of m)`.

*Reporting contract* — positive assertions, in both the markdown and the injected HTML payload, that both summary figures are present, that each covers its correct job population, that they carry their stated values, and that no line equals their sum. Plus the negative assertion that no savings or avoided-cost language appears.

*Export boundary* — a `</script>` payload cannot break the slot; a job label containing markdown control characters is escaped; denial command strings appear in neither output.

Manual check: open the generated HTML in both light and dark, confirm the tables scroll rather than pushing the page sideways, and confirm every number matches the source files named in the method note.

## Risks

- **The measurement invites the claim it refuses to make.** Once a page shows a CLI-reported 6.633623999999999 next to 779,279 subscription tokens, a reader will want the difference. The two overlapping figures, the stated non-addend rule, and the refusal to price the driver are the mitigations. The positive two-figure assertions are what keep them from eroding into one blended number.
- **Claude Code's transcript layout is not a published contract.** The glob avoids the slug rule but still assumes the location, the `message.usage` shape, and the `timestamp` field the interval filter depends on. When it breaks, the driver row degrades to `unavailable`. The delegated half is unaffected.
- **Fixture tests prove arithmetic, not telemetry.** The suite reads fake logs. Whether real CLI output still carries these fields is covered by the assertion that the shipped example matches its source job logs, which fails CI when the format moves but only when someone regenerates.
- **Codex multi-turn additivity is assumed.** Every observed job has one turn. The two-turn fixture asserts the behavior this design chose; it does not prove the CLI emits cumulative rather than incremental counts. A real multi-turn job is the check, and a human has to produce one.
