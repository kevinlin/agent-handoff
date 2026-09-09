# Agent Handoff — session evidence, transcripts, and receipts

## Context

`docs/specs/design_agent-handoff.md` is the design of record for the four phases where a run is decided and done. This document covers what those phases leave on disk, and the three artifacts that read it back: the transcript of one delegated job, the Handoff Session Receipt for the whole run, and the cost receipt derived from both.

Why any of this is a design and not a rendering detail: the flow's headline claim is that delegated work executed on another meter and stayed out of the driver's context. A driver can assert that in one sentence at the end of a run, and a summary is exactly what an unverified claim looks like. Everything here exists so the claim can be checked against files nobody typed.

This document replaces two earlier ones: `design_transcript-viewer.md` (shipped 3.6.0) and `design_cost-receipt.md` (shipped 3.6.1). Each described half of one flow. Both predate the copilot backend and receipt schema v6.

### Scope

Covered: the `.handoff/` evidence store, transcript rendering, session receipt generation and re-validation, cost receipt generation.

| Out of scope | Where it lives |
| --- | --- |
| Plan, split, delegate, monitor, review | `docs/specs/design_agent-handoff.md` |
| The job primitive itself (`submit`/`status`/`result`/`resume`/`cancel`/`cleanup`) | `scripts/delegate-codex.sh` header comment |
| Identity configuration and the setup wizard | `docs/config-schema.md` |
| E2E verdicts | `docs/verdict-schema.json`, `references/e2e-gauntlet.md` |
| The illustrative workload model and its ledger | `docs/showcase-cost-model.md` |

The prose contract agents load is `references/claude-driven.md`, Phase 0 and Phase 5; the same flow drawn for a human reader is `docs/user-guide/diagrams/phase5-wrap-up.svg`.

### The rule all four flows obey

Every field in every artifact traces back to a file read during the run. Three corollaries, each with a script behind it:

- **Generated, never typed.** `make-receipt.py` refuses to print an invalid receipt, `validate-receipt.py` re-checks a written one, and neither renderer accepts a prose section a driver fills in by hand.
- **Measured, never repriced.** Tokens metered by one vendor are never converted into another vendor's list price. The `$64.195302` "avoided spend" figure in `examples/v2.0.0-conversation-cost-receipt.md` is the specific move this rules out, and `CLAUDE.md` forbids it independently.
- **A gap is named, never zeroed.** `unknown`, `running`, `cancelled`, `failed`, `unscoped`, `unavailable`, `inherit (unresolved)` and `not recorded` are all real output values. A measured zero prints `0`, and the two facts stay distinguishable.

### Producers and readers

| Artifact | Written by | Read by | Contract |
| --- | --- | --- | --- |
| `.handoff/jobs/<jobId>/` | `delegate-codex.sh`, while the job runs | monitor loop, all three renderers | `delegate-codex.sh` header |
| `.handoff/session-start` | `make-receipt.py --start`, Phase 0 | `make-receipt.py` | one ISO stamp |
| `.handoff/goal.md` | driver and monitor loop through `goal-sync.py` | driver, a resumed session | `references/goal-template.md` |
| `.handoff/receipts/receipt-<stamp>.md` | `make-receipt.py --save` | `validate-receipt.py`, `render-cost-receipt.py` | `docs/receipt-schema.json` |
| `.handoff/transcripts/<jobId>.html` | `render-transcript.py` | a human | none; it is a page |
| `.handoff/cost-receipts/cost-receipt-<stamp>.{md,html}` | `render-cost-receipt.py` | a human, and the README showcase | none; it is a page |

---

## Flow 1 — Gather and persist session evidence

### Evidence accumulates during the run, not at the end

Nothing in Phase 5 collects anything the run did not already write. Each job's own directory is the unit:

| File | Written | What later reads it |
| --- | --- | --- |
| `prompt.md` | at submit | the transcript's first row |
| `run.sh` | at submit | audit: the command actually executed |
| `meta` | at submit, `key=value` | role, backend, model, effort, each value's source, `submitted_at`, `parent`, `mode` |
| `log.jsonl` | streamed by the worker | transcript rows, usage folding, denial counts |
| `stderr.log` | streamed by the worker | the anomaly rule in Phase 3 |
| `pid`, `exit_code`, `cancelled` | at submit, at exit, on cancel | `job_state()` |
| `session_id` | extracted at the end, or assigned at submit on copilot | `resume` |
| `usage.json` | copilot only, at exit | the copilot token and credit columns |

`job_state()` lives once, in `scripts/handoff_runtime.py`, mirroring `delegate-codex.sh`. Absence of `exit_code` is never `RUNNING` on its own: a worker that died without writing one is `FAILED`. Both renderers import that single implementation rather than re-deriving it.

### Two writes that are ordered, and one that is contended

Phase 0 stamps `.handoff/session-start` **before** any job is submitted. `make-receipt.py` excludes any job whose `submitted_at` predates the marker, on the grounds that it belongs to an earlier run, so a marker stamped after the fact produces a structurally valid receipt indexing nothing. With no marker and no explicit `--started-at`, the tool refuses to emit rather than accept a remembered start time.

`.handoff/goal.md` is written by both the driver and the Phase 3 monitor loop, so `goal-sync.py` reads and writes it under a `--expect-sha256` compare-and-set. A lost status update is a silent evidence loss, which is why this is not a plain file edit.

### Phase 5 probes; it does not recall

Every receipt field has a source the driver reads at wrap-up:

- job counts and per-job durations: each job's `meta` (`backend=`, `submitted_at`) plus the mtime of its `exit_code`
- `duration`: the start marker to now, wall clock, including time blocked on a human approval
- `scope` and `config_source`: `handoff-config.py resolve`
- `roles_used`: each job's `meta` for role, model and effort; `handoff-config.py resolve` for `verified`
- `anomalies` and `checks`: what the run actually did, including a denial count that a zero exit code hides

A role with `verified: false` is listed with the flag as it stands. Dropping it, or promoting it to look cleaner, is the failure mode the field exists to catch.

### Three telemetry shapes under one layout

All three backends produce the same job directory. What differs is the shape inside `log.jsonl`, and that difference is contained in the readers:

| Backend | Terminal record | Tokens | Cost or credits | Denials |
| --- | --- | --- | --- | --- |
| codex | `turn.completed` / `turn.failed`, `usage` | five counters, summed across turns | none emitted; the work runs on a subscription | not reported |
| claude | `type: result` | `usage`, plus `output_tokens_details.thinking_tokens` | `total_cost_usd`, `costBasis: list` | `permission_denials[]` |
| copilot | `type: result` in the log; `usage.json` beside it | `tokenDetails` and `modelMetrics` in `usage.json` | premium requests and nano-AIU: AI credits, no currency | typed `tool.execution_complete` with `error.code == "denied"` |

Copilot's file carries two meters with two different scopes. `tokenDetails` and `modelMetrics` are per invocation, so a parent job and its fix round add up. `totalPremiumRequestCost` and `totalNanoAiu` at the top level are cumulative for the whole Copilot session, so summing those across a resume double-counts the parent. The per-invocation figures under `modelMetrics` are the ones read. An absent `usage.json` is `unknown`; a file of zeros is a measured zero.

### There is no per-event clock, and only two formats pretend otherwise

Codex `exec --json` emits no timestamp on any event; across 280 events in the original four sample jobs the complete key set was `type`, `thread_id`, `item`, `message`, `error`, `usage`. Claude and copilot events do carry one. So the transcript reads a per-event time where the format supplies it and falls back to sequence position where it does not, and the job-level window comes from `submitted_at` plus the `exit_code` mtime — the same evidence the receipt's durations use. Nothing infers a clock from a neighbouring event.

### The store is plaintext, local, and quotable

A job's `log.jsonl` holds whatever the worker read: the largest single event in the sample set was 110 KB of file contents. `.handoff/` is gitignored in this repo and in the target repos observed so far, but that is not a repo-wide guarantee — `handoff-setup.py` accepts `--exclude-choice self` and `track`, both of which leave `.handoff/` tracked. `render-transcript.py` therefore runs `git check-ignore -q` on its own output path and prints a one-line warning when the page is not ignored. Committing a transcript by accident is a real disclosure, and the warning is the cheapest thing that catches it.

What a resumed session inherits is this directory and nothing else: the final task statuses in `goal.md`, the job directories, the saved receipts, and the e2e verdicts. `/agent-handoff resume` reads those rather than re-deriving state from the repository.

---

## Flow 2 — Session transcript

`/agent-handoff transcript [<pid|jobId|folder>]` renders one job's `log.jsonl` as a self-contained HTML page and opens it. The same page accepts a dropped `log.jsonl` with no generation step.

### Why `result` is not enough

`delegate-codex.sh result` prints the last agent message, a command count, usage, and any denial count. That answers "what did it conclude". It does not answer "what did it do, and why did it conclude that", which is the question a driver has when a verdict looks wrong or a monitoring tick shows an anomaly. In the sample jobs the ratio was 16 agent messages to 111 command pairs, and `result` also ignored a `turn.failed` and a top-level `error` that were sitting in the log.

### One artifact, two entry points

`assets/transcript-viewer.html` is the whole viewer, around 55 KB: vendored `marked.min.js` v15.0.12 (MIT), the viewer's CSS and JS, an empty payload slot, and a dropzone. `scripts/render-transcript.py` resolves an identifier to a job directory, reads its state files, substitutes the payload, writes `.handoff/transcripts/<jobId>.html`, and opens it; `--no-open` prints the path for headless use.

All log parsing lives in the page's JavaScript. A dropped file has no Python beside it, so any parsing done in Python would need a second implementation in another language to keep the drop path working. Python only finds the job and injects `log_text` unparsed, so both entry points run the same normalizer.

### Normalization: one ordered list, correlated by id

Every row keeps the **source position** of the line that first produced it, and the list renders in that order. Correlation replaces a row's content in place rather than appending:

- codex `item.*` events correlate on `item.id`, folding 111 `started`/`completed` pairs into 111 rows and rendering an in-flight job whose last item has a start and no completion. Events with no item id — `thread.started`, `turn.*`, the top-level `error` — each keep their own position.
- claude content blocks are separate rows inside their event, joined on `tool_use_id`. A `tool_result` arrives inside a `user` event; joining fills in the row's output and keeps the call's name and input. A result never replaces a call.
- copilot folds `tool.execution_start` and `tool.execution_complete` on `toolCallId`, and drops `ephemeral: true` deltas: one small job produced 82 of them.

| Normalized kind | codex `exec --json` | claude `stream-json` | copilot JSON |
| --- | --- | --- | --- |
| `user` | — | — | `user.message` |
| `agent` | `agent_message` | `assistant` text blocks | `assistant.message` with `phase: final_answer` |
| `reasoning` | `reasoning` | `thinking` blocks | — |
| `command` | `command_execution` | `tool_use` on a shell tool | `tool.execution_start` for `bash`/`shell` |
| `tool` | — | any other `tool_use` | any other tool call |
| `file_change` | `file_change` | a *successful* edit or write tool | a *successful* `create`/`edit`/`str_replace`/`write` |
| `mcp` | `mcp_tool_call` | `mcp__*` tool names | — |
| `web_search` | `action.queries` where present, else the truncated `query` | — | — |
| `error` | `error` items and the top-level `error` | `is_error` on a result | `session.error`, a failed call, `error.code == "denied"` |
| `lifecycle` | `thread.started`, `turn.*` | `system` init, `result` | `assistant.turn_start`/`turn_end`, `result` |

Two rules in that table earned their place. A file-change row from claude or copilot is **provisional** until its result arrives: a failed Edit or a refused write is demoted to an error row, because a refused write rendering as a completed file change is the viewer lying about the repository. And copilot's denial is read from the typed `error.code`, never pattern-matched out of a message string.

### The backend is declared, or inferred from a type unique to one format

Copilot's terminal event is `type: "result"`, the same type name claude `stream-json` uses with a different payload. So the branch is chosen by the declared `meta.backend` on a generated page, and on a dropped log by scanning for a type only one format emits (`assistant.turn_start` or `tool.execution_start` for copilot, an `item.` prefix for codex). `result` never seeds the guess. A test asserts that one `result` line parses differently under each declared backend.

### The prompt row

`log.jsonl` does not carry the job's prompt on codex or claude, so the payload carries `prompt_text` from `prompt.md` and the page renders it as the first row. A copilot log carries the prompt itself as `user.message`, so the injected prompt is suppressed when it duplicates one already in the stream. Exactly one prompt row, whichever backend ran.

### Nothing is dropped, and nothing disappears without a mark

An event or block matching no rule renders as a labelled row with its JSON collapsed inside. Expanded detail for a *recognized* row also shows its full source object, so a field the mapping table does not name stays reachable. An interior line that fails to parse renders as a visible `unparsed line N` row carrying its raw text. Only a trailing unparseable line is treated as an in-flight partial write and rendered as a muted "log continues" marker, because the worker appends while the page may be reading. A transcript that quietly discards evidence is worse than no transcript.

### The header is a snapshot

The header carries what the job's own files know: label, role, backend, model, effort, `read_only`, the job window, state from `job_state()`, and usage from the terminal event. `generated_at` renders alongside them, so a page of a job that was still running is not mistaken for its final state. A resumed job legitimately has no `backend` or `role` line and carries `model=inherit`; those render as "not recorded" and "inherited from parent", never as a blank or a guess, and the parent job id links out where its directory exists.

### Escaping and the markdown boundary

Every string in a log is untrusted. The live vectors are event-handler attributes that fire on insertion (`onerror`, `onload`) and `javascript:` URLs; a `<script>` element inserted through `innerHTML` does not execute. So the boundary is not "keep script tags out", it is "never build markup from log content".

- Markdown is applied to agent and reasoning text only. Commands, shell output, tool results, MCP payloads, file paths, header values, thread ids, unknown type names, row labels and `unparsed line N` bodies all reach the DOM through `textContent`.
- Overriding a `marked` renderer inherits its escaping duties. Three overrides, against the pinned v15 token-object signatures: `html({text})` returns its text escaped, so raw HTML in a message displays as visible text and prose that legitimately mentions `Vec<T>` survives; `link({href, title, tokens})` and `image(...)` reject any scheme outside `http:`, `https:`, `mailto:` and `#`, rendering the rejected URL as plain text, and attribute-escape the surviving `href` and `title` themselves. `link` receives tokens rather than rendered text, so the override calls `this.parser.parseInline(tokens)` or the link's contents vanish.
- The payload slot is a separate boundary. `handoff_runtime.inject()` writes JSON into `<script id="handoff-payload">` with every `<` rewritten as its six-character JSON unicode escape (backslash-u-0-0-3-c), so no closing-script sequence in captured output can end the element early. That protects the element boundary only and says nothing about DOM insertion, which the two rules above govern.

`marked.min.js` was scanned against this repo's three gates before it was vendored: no CJK, no secret-pattern hits, no high-risk command text. DOMPurify was rejected as 22 KB of a second vendored blob solving what three overrides solve.

### Identifier resolution refuses rather than guesses

The argument is optional. Resolution stops at the first match: a path or existing directory; the reserved selector `last` or an omitted argument, meaning the newest job by `submitted_at` with ties broken on mtime then name; an exact directory name; an all-digit argument matched against `pid` files; a suffix or substring match on the directory name.

`last` is checked before label matching, so a job whose label is literally `last` cannot intercept it and stays reachable by its full name. Two collision rules make the silent-wrong-job cases visible: an all-digit argument matching both a pid and a label is reported as ambiguous rather than resolved by step order, and a suffix match returns **every** matching round, so `<label>` and its `-r2` resume are both printed rather than one being picked and the other hidden. Any step yielding more than one candidate lists them and exits 2.

---

## Flow 3 — Session receipt

### It indexes the run; it does not summarize it

The Handoff Session Receipt (`docs/receipt-schema.json`, schema v6) records phase, session id, wall-clock duration, checks, anomalies, three job counts with three matching duration lists, config scope and source, and `roles_used`. Those fields settle *which* jobs belong to the run and *what* executed them. No token or cost figure appears in the receipt at all. That omission is deliberate, and Flow 4 is what fills it.

### One count per backend, read from the job's own meta

`codex_jobs`, `cc_jobs` and `copilot_jobs`, each with `*_job_durations` keyed by jobId, fix rounds included. `make-receipt.py` partitions the job directories by each `meta`'s `backend=` line: a directory with no such line predates backend dispatch and is codex by construction, and so is one naming a backend this version does not know. A copilot job is never folded into another count. In `roles_used`, `host` is the CLI that executed the role, unrelated to the runtime that loaded `SKILL.md`.

### Generated, then re-validated

`make-receipt.py` builds the fields, runs `validate-receipt.py`'s validation on them, and prints nothing when any check fails. `--save` writes `.handoff/receipts/receipt-<YYYYMMDDTHHMMSSZ>.md`, stamped from the same `now` the receipt measures to. The written file is then re-checked with `validate-receipt.py`, which is the same check CI runs, applied to the run's own output. `receipt_schema_version` must be exactly `6`; a v5 receipt fails, and that failure is the signal to regenerate rather than hand-patch.

---

## Flow 4 — Cost receipt generation

`/agent-handoff cost-receipt [<receipt-file>]` reads a written session receipt plus the job state it indexes, and writes a markdown and an HTML cost receipt to `.handoff/cost-receipts/`.

### The premise that changed

The consumption question used to have two bad answers: the workload model in `docs/showcase-cost-model.md`, which is explicitly illustrative, and the hand-written `examples/v2.0.*-conversation-cost-receipt.md`, whose central figure was a counterfactual obtained by repricing observed Codex tokens at another vendor's list rates. Delegated jobs now retain real usage telemetry, so the question can be answered by measurement. Both archives keep their schema-v2 banner and one pointer line to the current example; restating them under the current schema would mean inventing a receipt block `validate-receipt.py` is built to reject.

### One Python parser, two outputs

The transcript puts all parsing in JavaScript so a dropped log needs no second parser. That reasoning does not carry here: a markdown output requires the parsing to exist in Python regardless, so duplicating it in JavaScript would be two implementations of the same arithmetic. One parser feeds both renderers, and the HTML page inserts every value with `textContent`.

### A session id locates a transcript; it does not bound a measurement

The receipt carries `duration` but no absolute timestamp, and a Claude Code session can hold work from before and after one run. So:

- A receipt saved by `make-receipt.py --save` is named for its own generation time, which is the run's end. The interval is `[stamp − duration, stamp]`, and driver-transcript entries are filtered by their ISO `timestamp`.
- Any other input has no derivable end. The driver row is reported `unscoped` and labelled in both outputs as a whole-session total that may include work outside this run.

That is what makes the driver row trustworthy at all: two runs sharing one session produce two different rows, and re-rendering an old receipt after the session grew reproduces the row it produced the first time. The transcript itself is found by one glob over `~/.claude/projects/*/<session>.jsonl`, with the session id passed through `glob.escape` as a literal. Deriving Claude Code's directory slug from the repo path would encode an undocumented rule this repo cannot test.

### The reader is fail-closed

The input is written by a generator, and the reader still treats it as untrusted text. Reused from the validator: field presence, the duration grammar, integer counts, the job-durations grammar, and the schema version. Added, because `extract_block` takes the first header it finds and lets a later duplicate field overwrite an earlier one:

- exactly one `[Handoff session receipt]` header in the file, and no duplicate field keys inside the block
- each jobId a single safe path segment resolving inside `<repo>/.handoff/jobs/`, checked before any file is opened; the validator's own jobId grammar admits `/` and `..`
- no duplicate jobIds, within a list or across the three
- each count equal to the number of entries in its duration list
- each jobId's `meta` naming the backend it is listed under; a mismatch is an error, never a silent reclassification
- every indexed job directory present, and named when it is not

There is no fallback scan of `.handoff/jobs/`: two sources of truth about run membership would eventually disagree. A `jobId=running` entry is valid input and contributes a row with no usage. Any failure names itself, exits 2, and writes nothing. One consequence: `examples/session-receipt.md` carries placeholder jobIds that exist nowhere on disk, so it is not usable as input, which is the rule working.

### Counters are reported, never combined

Each backend's counters go into the five shared columns: input, cache read, cache write, output, reasoning. No column is derived by adding others. Whether codex's `cached_input_tokens` is a subset of its `input_tokens` is not established by any observed log, and a "total tokens" figure would bake that guess into every row. Claude's counters are disjoint in observed data (`input_tokens: 160` beside `cache_read_input_tokens: 8222548`), a second reason the backends sit side by side rather than summed.

- `usage` supplies every token figure and `total_cost_usd` supplies the cost. `modelUsage` is read only for model names; adding its per-model entries on top of the aggregate would double every number.
- Codex usage is summed across turns, over `turn.completed` and `turn.failed` alike.
- For claude, only the last `result` is authoritative; an earlier one is a repeat, taken once and flagged in the row.
- A resumed job writes `model=inherit`, so its model is resolved by walking `parent=` to the originating job, and reads `inherit (unresolved)` when that walk fails rather than naming a guessed model.

### Degraded states are named

| Situation | Renders |
| --- | --- |
| job still running, cancelled, or failed | `running`, `cancelled`, `failed`, with whatever usage the log did record |
| log truncated, unparseable, or carrying no terminal usage record | `unknown` |
| one counter absent from an otherwise valid record | `unknown` for that cell only |
| copilot job with no `usage.json` | `unknown` tokens and credits; denials still counted from the log |
| driver transcript not found | `unavailable` |
| driver interval not derivable | `unscoped` |
| codex or copilot cost column | `n/a - subscription`, `n/a - AI credits`; there is nothing there to read |

A partial total is never presented as a total: when any row in a column is unmeasured, the column reads `≥ <subtotal> (n of m jobs measured)`. A run with no claude-backed job says so, rather than reporting `unknown`, because `unknown` claims a reading was attempted and failed.

### The summary, and the three claims it must not make

Three claims are unsupportable by these measurements, and are recorded here so they do not reappear:

- **Not context savings.** Delegated token counts do not measure context the driver avoided. They cannot show what execution history the driver later read, and cumulative processing tokens are not context-window occupancy.
- **Not billed spend.** `modelUsage.costBasis` reads `list`. The figure is what the CLI reported, and is labelled *CLI-reported cost* in the table, the summary, and the example.
- **Not a saving.** A claude-backed delegated job bills the same vendor as the driver, so no difference between any two figures here is a saving, and none is computed.

What the summary reports instead is two figures whose populations overlap by design, plus a third reading that belongs to neither:

- **Ran on a Codex subscription** — token counters for codex-backed jobs, no cost figure, because the CLI emits none.
- **Ran outside the driver session** — token counters for all delegated jobs on any backend, plus the summed CLI-reported cost of the claude-backed ones.
- **Ran on the Copilot AI-credit meter** — premium requests and nano-AIU for copilot-backed jobs, stated as AI credits and never as currency.

Codex jobs appear in both of the first two. Both outputs say the figures are not addends, and tests assert that no line equals their sum. A further line reports permission denials across claude-backed and copilot-backed jobs, because a denied tool call does not move a job's exit code: a job can report success on checks it was refused.

### The export boundary is enumerated

What crosses into the outputs is listed, not implied: jobId, label, role, backend, model, state, the five counters, CLI cost, premium requests, nano-AIU, and the denial *count*. Denial command strings are read to count them and reach neither output. No prompt text and no captured command output crosses at all. Source paths are relativized against the repo, and anything outside it keeps only its basename, so no home directory survives into a committed artifact. That boundary is narrower than the transcript's, and it is what makes `examples/v3.6.1-conversation-cost-receipt.{md,html}` safe to publish.

Both pages sit on one `light-dark()` token set. The cost receipt carries the v2.0.1 receipt's editorial language (serif masthead, cobalt figures, coral caveat rule, uppercase mono labels) on those tokens, so it reads in both themes and is still one design system rather than two.

---

## Where this fails closed, and where it does not

| Guard | Enforced by | What fails without it |
| --- | --- | --- |
| No receipt without a measured start | `make-receipt.py` refuses; no default start | a remembered duration |
| No invalid receipt reaches disk | `make-receipt.py` validates before printing | format drift and optimistic fields |
| A written receipt still matches the schema | `validate-receipt.py`, also in CI | a receipt nobody re-read |
| A job is counted under the backend that ran it | `meta` `backend=`, checked again by the cost reader | a copilot job folded into another count |
| A jobId cannot escape the jobs directory | path-segment check before any open | a receipt reading arbitrary files |
| Counts and duration lists agree | cost reader cross-check | a receipt describing more than it indexes |
| One log line cannot blank a transcript | per-line parse with a visible `unparsed line N` row | evidence dropped without a mark |
| Log content never becomes markup | `textContent` everywhere but agent prose, three renderer overrides, scheme allowlist | an active handler from captured output |
| Captured output cannot end the payload element | `inject()` escaping `<` | a page broken by its own evidence |
| An ambiguous selector resolves to nothing | `render-transcript.py` exits 2 with candidates | the silent wrong job |
| A page is not committed by accident | `git check-ignore` warning | a transcript of shell output in Git history |
| No price table exists | absence, plus a test asserting no savings language | a repriced token presented as a saving |

Asserted by prose rather than by a script: that the driver actually probes evidence in Phase 5 instead of recalling it, that anomalies and denial counts are recorded, and that the report does not narrate a saving. `test-prompts.json` records the intended contract but validates prompt-file structure only — `run-test-prompts.py` does not exercise model behaviour. The reporting rules are enforced where they can be: in `tests/test_cost_receipt.py`, against generated output.

## Risks and known limits

- **The shipped example is checked for existence only.** `check-skill-repo.sh` asserts that `examples/v3.6.1-conversation-cost-receipt.{md,html}` are present. The 3.6.1 design intended a further test tying the example's totals to its source job logs, and that test does not exist, so the example can drift from the logs it reports while CI stays green. The ledger precedent is the cheapest fix: regenerate in CI and diff.
- **Claude Code's transcript layout is not a published contract.** The glob avoids the slug rule but still assumes the location, the `message.usage` shape, and the `timestamp` field the interval filter depends on. When it moves, the driver row degrades to `unavailable` and the delegated half is unaffected.
- **Copilot's `usage.json` shape is likewise unpublished**, and it holds two meters at two scopes. Reading the session-cumulative pair instead of the per-invocation one would double-count a fix round with no visible symptom. One test pins the distinction.
- **Codex multi-turn additivity is assumed.** Every job observed so far has one turn. A two-turn fixture asserts the behaviour this design chose; it does not prove the CLI emits cumulative rather than incremental counts. A real multi-turn job is the check, and a human has to produce one.
- **Fixture tests prove arithmetic, not telemetry.** The suites read synthetic logs. Whether real CLI output still carries these fields is only observable by running a real job.
- **The transcript inlines a whole log.** Observed logs ran 72–656 KB, comfortable in one page. No cap exists, so a much larger log is an untested size.
- **The measurement invites the claim it refuses to make.** A page showing a CLI-reported figure beside several hundred thousand subscription tokens makes a reader want the difference. The overlapping populations, the stated non-addend rule, and the refusal to price the driver or codex are the mitigations, and the positive two-figure assertions are what keep them from eroding into one blended number.

## Not doing

- **Stamping timestamps into `log.jsonl` as it is written.** A line-stamper in `delegate-codex.sh` would give real per-event local time on every backend and survive the drop path. It also modifies a runtime primitive every job depends on, and only new jobs would benefit. Deferred; this is the way to get per-event timing if it ever becomes the point.
- **Reading Codex rollout files for timestamps.** `~/.codex/sessions/.../rollout-*.jsonl` carries ISO timestamps and joins on the session id the job already stores. It is codex-only, same-machine-only, a third format to parse, and it breaks the drop path, where the dragged file carries no timestamps at any price.
- **Diff bodies for file changes.** `file_change` events carry paths and kind, no content. A diff would make the page depend on repo state instead of the log.
- **A nested view of subagent threads.** `parent_tool_use_id` is retained and labelled; a tree UI on top of it is speculative until a real log needs one.
- **Search, export, or a two-pane outline in the viewer.** Browser find covers search at this page size.
- **A price table, or any cost figure for the driver, codex, or copilot.** A repriced token is the fabrication the repo rules out, and a checked-in rate card would go stale silently.
- **A receipt schema change for consumption.** The cost receipt reads receipts; it does not extend them. No edits to `make-receipt.py`, `validate-receipt.py`, or `delegate-codex.sh` were needed to add it.
- **Aggregation across runs.** One receipt in, one cost receipt out.
- **A `references/*.md` flow document for either command.** Each is one command with one optional argument; `SKILL.md` carries both directly.

## Verification

```bash
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
bash scripts/check-skill-repo.sh .
python3 scripts/english-only-scan.py
python3 scripts/run-test-prompts.py
```

Receipt roundtrip, and the cost receipt end to end in the order that measures something — the start marker has to be stamped before the jobs run:

```bash
python3 scripts/make-receipt.py --start --repo .
# ... run at least one codex-backed and one claude-backed job ...
python3 scripts/make-receipt.py --repo . --phase review --claude-session <sid> \
  --checks ci --codex-jobs 1 --cc-jobs 1 --copilot-jobs 0 \
  --scope project --config-source project --roles-used '[]' --save
python3 scripts/render-cost-receipt.py --repo . --no-open
python3 scripts/render-transcript.py --repo . --no-open
```

`--codex-jobs` and its siblings supply counts, not a selection, so inspect the generated receipt for the expected jobIds in its duration fields before using it as input.

The JavaScript half of the transcript needs its own observable checks, which `tests/test_transcript_viewer.mjs` runs in Node against the fixtures in `tests/fixtures/transcript/`: pair folding, distinct positions for id-less events, an in-flight last item, a truncated trailing line reported as partial, an interior malformed line kept visible, a mixed claude event carrying text plus tool_use plus an unrecognized block, a failed edit rendering as an error, one prompt row per backend, and a `result` line parsing differently under each declared backend. The security checks assert the resulting DOM against a hostile fixture: no element with an `on*` attribute, no `href` or `src` outside the allowed schemes, and the hostile payloads present as visible text. A fixture that only proves "no alert fired" gives false confidence, since an inert `<script>` proves nothing.

Manual checks no automated check covers: open a generated transcript for a job with a very large command output and confirm it stays folded; drop a second log onto a page that already carries one and confirm the previous job's header is cleared rather than merged; open the cost receipt in both themes and confirm the tables scroll inside their own container rather than pushing the page sideways, and that every number matches the files named in the method note.

## Changing any of this

`references/darwin-ratchet.md` is the gate. The dimensions here are: what evidence is written, how it is read back, what the transcript renders, what the receipt indexes, and what the cost receipt is allowed to claim. Change one at a time, validate against the fixtures and one real run, and keep the change only when the repo evidence improves. The reporting rules in Flow 4 are the ones most likely to erode under pressure to produce a single impressive number, so a change there needs a test that fails when the rule is broken, not a paragraph promising to hold it.
