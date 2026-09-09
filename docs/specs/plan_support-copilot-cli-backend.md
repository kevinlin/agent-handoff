# Support GitHub Copilot CLI as a third backend

## Context

Handoff has two execution backends. `codex` runs `codex exec --json`; `claude` runs a second Claude
Code as `claude --print --output-format stream-json`. Both are real background jobs with the same
jobId, `.handoff/jobs/` state, monitoring loop, bounded `resume` fix round, worktree lifecycle, and
receipt evidence. The identity's configured `backend` decides which CLI runs a job, always.

GitHub Copilot CLI is a third coding agent with its own subscription meter, its own model catalog,
and a non-interactive mode that covers everything the delegation primitive needs. Adding it as a
third `backend` value gives a user with a Copilot subscription the same choice the other two
already offer, and it tests whether the backend abstraction v3.5.0 introduced is actually an
abstraction or just two special cases wearing one name.

Target version: **3.7.0**.

## Grounding

`docs/research/github-copilot-cli-specification.md` is the source of fact for this plan. It was
rewritten on 2026-09-09 from live probing of GitHub Copilot CLI 1.0.83 on macOS across six real
`-p` runs, and every claim in it carries provenance: `[probed]`, `[help]`, or `[open]`. Its
section 14 lists the load-bearing details an earlier docs-only version got wrong.

This plan does not re-derive those facts. Where it depends on something the research marked
`[open]`, the dependency is named in **Assumptions to probe** rather than assumed silently.

## Approach

Fill a third slot in the seams v3.5.0 already cut, rather than adding a third special case beside
two others.

The job lifecycle is vendor-agnostic already. Job dir, `meta`, pid, `exit_code`, `status`,
`result`, `resume`, `cancel`, `cleanup`, `list`, worktrees, and config-driven role resolution all
work on any backend. Four things differ per vendor: binary discovery, the exec line, the effort
enum, and the event-stream shape. `delegate-codex.sh` already has a named function for each, so
copilot is a branch in four places rather than a parallel implementation.

Three things do not fit and need new work:

**Binary identity has to be probed, not assumed.** `copilot` is also the binary name of AWS Copilot
CLI, an unrelated ECS deployment tool, and PATH order decides which one `command -v copilot` finds.
Both were present on the probing machine. They are distinguishable only by `--version` output:
`GitHub Copilot CLI 1.0.83.` against `copilot version: v1.34.1`. Neither `codex` nor `claude` has
this problem, so this is the one piece of the integration with no existing pattern to copy.

**The terminal event type collides with Claude's.** Copilot's stream ends with `type: "result"`,
the same type name Claude's stream-json uses, carrying a different payload — `sessionId`,
`exitCode`, and a `usage` object with no token counts in it. Every parser that reads a job log must
therefore take the backend as an input rather than sniffing the event shape. `fold_usage` in
`render-cost-receipt.py` already does. `cmd_result` in `delegate-codex.sh` and the transcript
viewer's JavaScript do not, and a backend-blind parser will mis-read a Copilot log without erroring.

**There is no model catalog to read.** The setup wizard derives models from `codex model/list` and
`claude --help`. Copilot offers neither: `copilot model` and `copilot models` are not commands,
`copilot help` advertises no model values, and `availableModels` appears only inside a run's JSONL.
The catalog is also gated by account plan, org policy, and model rollout, so a hardcoded list will
be wrong for someone. On the probing account only `mai-code-1.1-flash` was available;
`claude-sonnet-4.5` and `gpt-5.4` both returned "not available".

## Decisions taken

Settled during planning. Each is a decision, not an observation.

- **Receipt gains a third flat field pair, and the Python internals become backend-keyed.**
  `copilot_jobs` / `copilot_job_durations` join the existing two pairs, schema `v6`. The flat
  receipt grammar stays scannable for a human and keeps `validate-receipt.py`'s regex simple. But
  `CODEX_FIELDS` / `CLAUDE_FIELDS` and the two-branch partitioning collapse into tables keyed by
  backend, so a fourth backend is a data row rather than a fourth `elif` in five files.

- **The session id is assigned at submit, not extracted at the end.** Codex emits `thread_id`
  early and Claude carries `session_id` on every event, so a crashed job on either backend still
  has a resumable id. Copilot's `sessionId` appears only in the terminal `result` event, so a job
  that dies mid-run would have none. `--session-id` also sets the UUID for a new session, so
  Handoff generates one at submit, writes `$JOB/session_id` before launch, and records
  `session_id_source=assigned` in `meta`. This is the choice that keeps `resume` parity real
  instead of documented with an exception. Generate it with `python3 -c 'import uuid; ...'` rather
  than `uuidgen`: the script already shells to `python3` throughout, and that removes a portability
  question for one line. **Conditional on assumption 2 as strengthened below** — an echoed id proves
  assignment, not that an interrupted session left resumable context behind.

- **Effort accepts the full CLI enum: `none|minimal|low|medium|high|xhigh|max`.** *Reversed after
  spec review.* The first draft excluded `none` because the API rejected it on the observed model
  with a 400. That reason does not survive reading the 400 itself: it says the supported values for
  that model are `minimal`, `low`, `medium`, and `high`, so `xhigh` and `max` fail there too, and
  the draft kept both. Effort validity is decided per model by the API and the CLI's enum is only a
  superset; the plan's own principle is to pass the requested effort and surface the rejection,
  never to downgrade silently. Excluding one of the three values that model rejects was arbitrary.
  `validate_effort copilot` therefore mirrors the CLI enum, and a per-model rejection surfaces as
  Copilot's own error.

- **`model = "auto"` is refused for a configured identity.** Mechanically, `auto` plus any effort is
  rejected at the CLI layer, and every identity requires a non-empty effort. Conceptually, an
  identity is a deliberate `backend + model + effort` choice and the flow's own rule is that the
  driver never re-decides routing per run; `auto` hands that choice back to the vendor. The refusal
  uses the same "change the config" wording as the existing `--backend` contradiction guard.

- **A delegated Copilot job runs with `--allow-all-tools`.** This mirrors the `bypassPermissions`
  default a delegated Claude job already gets, and the reasoning is the v3.5.1 retro. A background
  `-p` job has no approval surface, so any rule that would prompt denies instead; a denied tool call
  does not move the exit code; and the worker then reports that gates passed which it was in fact
  refused. Copilot's typed `error.code: "denied"` event is the safety net, and a job with denials
  is treated as failed whatever its exit code says — see task 2 for what "failed" means concretely.
  The worktree and the packet are **scope controls, not enforced containment**: an ordinary
  `submit` runs in the main repo unless `--worktree` is passed (`delegate-codex.sh:351`), so a
  worker under `--allow-all-tools` can reach the whole checkout. Say that plainly in the flow prose
  rather than implying a sandbox that does not exist.

- **No `HANDOFF_COPILOT_PERMISSION_MODE`.** The v3.5.1 retro concluded that naming an escape hatch
  is not mitigation when nothing tells the user to reach for it. The posture is the default, the
  submit-time warning, and typed-denial detection in `status` and `result`.

- **Copilot does not join `PRESETS`.** A preset entry carries `(backend, model, effort)`, with
  codex's model as `None` meaning "detect it". Copilot has no stable model name to hardcode and no
  catalog to detect from without spending a request, so a preset would either name a model that is
  wrong for most accounts or force a probe during `--plan`. Copilot is selected through custom mode
  and the setup UI. The guard at `handoff-setup.py:303` already requires `--role-model` when
  `--role-backend` diverges from the preset, so this needs no new code.

- **Egress defaults are overridden explicitly.** Session export to GitHub web and mobile is on by
  default in Copilot CLI. A delegated job carries repository contents and prompt text, so every
  generated `run.sh` passes `--no-remote --no-remote-export`, and never `--share`, `--share-gist`,
  `--yolo`, `--allow-all`, `--worktree`, or `--enable-memory`.

- **No credential stripping for the copilot branch**, on the narrow ground that Copilot needs no
  Claude authentication handling. `handoff_runtime.clean_claude_env()` exists
  because a nested Claude Code host injects provider URLs and credentials that would override a
  child `claude`'s own login. Copilot authenticates through `gh auth` and `~/.copilot` and reads
  none of `ANTHROPIC_*` or `CLAUDE_CODE_*`, so mirroring that logic here would be ceremony.

## Mapping audit

| Abstraction Handoff needs | Copilot mechanism | Status |
|---|---|---|
| Non-interactive submit | `-p "<text>"` | probed |
| `log.jsonl` event stream | `--output-format json` | probed |
| Session id for `resume` | assigned via `--session-id` | needs probe (assumption 2) |
| Fix round on the same session | `--resume <sid>` | probed, context intact |
| Working directory | `-C` fresh, shell cwd on resume | probed, same shape as codex |
| Effort as a routing field | `--effort` | probed, model-validated by the API |
| Read-only job | `--mode plan` | needs probe (assumption 1) |
| Final agent message | `assistant.message` where `data.phase == "final_answer"` | probed |
| Commands run | `tool.execution_start.data.arguments.command` | probed |
| Denial detection | `tool.execution_complete` where `data.error.code == "denied"` | probed, typed rather than pattern-matched |
| Token counters | `usage.json` from `--usage-output-file` | probed; key names already match `COUNTERS` |
| Cost figure | premium requests and nano-AIU; no currency | probed; a third kind of meter |
| Transcript rendering | drop `ephemeral: true`, fold by `toolCallId` | probed |
| Binary discovery | `--version` identity match | new work, no existing pattern |
| Model catalog | probe job reading `availableModels` | new work, no CLI source |

## Tasks

### 1. Live probe — close the open assumptions before writing code

Assumptions 1 through 5 in the section below decide the shape of task 2. Run them by hand against a
scratch git repo with the real CLI, then write each result back into
`docs/research/github-copilot-cli-specification.md` as a `[probed]` line with the evidence quoted,
and amend this plan where a result contradicts it.

**Task 1 is a hard stop.** Task 2 does not begin until all five results are recorded and this plan
is amended where a result contradicts it. The plan can be handed over as conditional work now, but
the capabilities behind assumptions 1, 2, and 5 cannot be promised as settled scope until probed.

Assumption 1 is the one that can change scope. If `--mode plan` is not read-only in a `-p` run, the
copilot backend refuses `--read-only` in 3.7.0. That removes **any invocation that passes
`--read-only`** — concretely the Phase 1 spec review, and the `--smoke` dry-run path in task 4.
*Corrected after spec review:* the first draft also listed `e2e_specifier`, which is wrong.
`e2e_specifier` is a writer — `references/e2e-gauntlet.md:35` submits it with `--worktree` and no
`--read-only`, and its packet at `:81` asks for executable tests and a commit. The restriction
belongs to the invocation's required capability, not to a fixed list of identities.

Probe design, per assumption:

- **1, read-only.** Do not accept the flag name. Under `--mode plan`, attempt a file write and a
  shell write, confirm both are refused, and confirm useful repository reads still succeed. A plan
  mode that also blocks reading is not a usable review channel.
- **2, assigned id.** An echoed id after a clean run proves assignment only. Add an interrupted-run
  probe: establish observable context, terminate the process before the terminal `result` event,
  then `--resume` the assigned id and check the context came back. Probe failure *before* session
  creation as a separate case.
- **3, log dir.** Confirm `--log-dir` inside the job dir does not disturb `--output-format json` on
  stdout.
- **4, usage on failure.** Distinguish three failure classes: CLI-layer argument rejection, API
  failure, and termination mid-run. Also probe whether a resumed session's `usage.json` counters are
  **per invocation or cumulative across the session** — `render-cost-receipt.py:288` sums job rows,
  so cumulative counters would double-count a parent plus its fix round.
- **5, verification under `--allow-all-tools`.** Capture a concrete repository check actually
  executing (`bash scripts/check-skill-repo.sh .`), with its output, not the worker's closing
  statement that it passed. That distinction is the whole content of the v3.5.1 retro.

Verify: for each of the five, the command run, the observed output, and for 1, 2, and 5 the
observable outcome (files unchanged; context recovered after interruption; a real check's output).
Each result written back into the research document as a `[probed]` line.

### 2. `scripts/delegate-codex.sh` — the copilot backend

- Header comment and `usage()`: three backends, not two. Document `HANDOFF_COPILOT_BIN`, the
  copilot effort enum, and the `auto` refusal.
- The backend value guard at `delegate-codex.sh:287` (`case "$BACKEND" in codex|claude)`) gains
  `copilot`. The module-level `BACKEND="codex"` initialiser at `:737` stays as it is: it is the
  default for a role-less ad-hoc job, not a guard.
- `resolve_worker_bin copilot`: read `HANDOFF_COPILOT_BIN`, else walk every PATH match with
  `type -a -p copilot` and take the first whose `--version` output contains `GitHub Copilot CLI`.
  Fail closed naming what was found instead and pointing at `HANDOFF_COPILOT_BIN`. The existing
  `CODEX_BIN` / `CODEX_BIN_SOURCE` / `CODEX_VERSION` variables keep their names so `meta` keys and
  `resume`'s parent-binary reuse stay unchanged; `CODEX_VERSION` then records the real Copilot
  version string as job evidence.
- **One resolution contract, used everywhere.** *Added after spec review.* Submit, resume, setup's
  availability check, and the UI's discovery probe must apply the same precedence and the same
  identity check, or setup can reject a valid install sitting behind AWS Copilot on PATH while the
  worker resolves a different binary. `cmd_resume` at `delegate-codex.sh:416` currently reuses the
  parent's recorded path whenever it is still executable, with no identity check — for copilot it
  must re-verify identity, since the path may now point at a different tool.
- `validate_effort copilot`: `minimal|low|medium|high|xhigh|max`.
- `cmd_submit`: refuse `model = "auto"` on a copilot job. For a fresh copilot job, set
  `NEW_SESSION_ID="$(uuidgen | tr 'A-Z' 'a-z')"`, write it to `$JOB/session_id` before launch, and
  record `session_id_source=assigned` in `meta`.
- `write_run_script`: third branch calling `write_copilot_exec_line`.
- New `write_copilot_exec_line`. Fresh job:
  `-p "$PROMPT" --output-format json -C "$WORKDIR" --model <m> --effort <e> --no-ask-user
  --no-remote --no-remote-export --no-auto-update --allow-all-tools
  --usage-output-file "$JOB/usage.json" --log-dir "$JOB/copilot-logs"`, redirected to
  `log.jsonl` and `stderr.log` with `</dev/null`. `--read-only` swaps `--allow-all-tools` for
  `--mode plan`. A fresh job passes `--session-id "$NEW_SESSION_ID"`; a resume passes
  `--resume "$session_id"`, drops `-C` and `--model`, and uses `cd "$WORKDIR"` like the codex resume
  path. `--log-dir` points inside the job dir to co-locate the evidence with the rest of the job
  state. It does **not** cause cleanup to remove those logs: `cmd_cleanup` at
  `delegate-codex.sh:698` only calls `remove_worktree` and keeps the job directory. Do not add
  job-evidence deletion to this feature; correct the claim instead.
- `warn_permission_bypass`: fires for a copilot job under `--allow-all-tools` as well, with the
  backend named in the message.
- `extract_session_id`: add `sessionId` to the key tuple. The assigned id means the cache is
  already populated for a Handoff-submitted job; this covers a job submitted by hand.
- Denial count: **count it in the parsed pass, not with grep.** *Changed after spec review.* The
  drafted `grep -cE '"subtype":"permission_denied"|"code":"denied"'` misses valid JSON with
  whitespace after the colon and matches an unrelated `code` field anywhere in the line. `cmd_status`
  already shells to `python3` for `last_event`, so count denials in that same pass against parsed
  fields — `subtype == "permission_denied"` for claude, and
  `type == "tool.execution_complete"` with `data.error.code == "denied"` for copilot. Fewer moving
  parts than two greps, and correct regardless of formatting.
- **Define what "treated as failed" means.** *Added after spec review.* `job_state` classifies exit
  0 as `DONE` (`delegate-codex.sh:217`) and `status` prints its denial warning while still
  succeeding (`:553`). This feature does **not** change that machine state: doing so would alter the
  existing claude backend's behaviour too, which is a second dimension of change, and it would need
  the Python mirror at `handoff_runtime.py:56` updated in lockstep. "Failed" here means
  driver-facing rejection: `result` reports the denials, and the flow prose says the driver must
  re-run the blocked checks itself and must not accept the diff on the worker's self-report.
- `cmd_result`: pass the backend from `meta` into the Python heredoc as an argv, and add a copilot
  branch. Skip events with `ephemeral: true`. Message from `assistant.message` where
  `data.phase == "final_answer"`; commands from `tool.execution_start.data.arguments.command`;
  denials from `tool.execution_complete` where `data.error.code == "denied"`, named from the
  matching `toolCallId`'s start event; usage from the flat terminal `result` event. Gating on
  backend is not tidiness: the `result` type name collides with Claude's.
- `session.error` must reach **both** output modes. A human-readable failure line is not enough:
  `result --json` has JSON consumers, so add the error type, message, and `statusCode` to the JSON
  payload as well. Copilot's API failures can arrive with an empty stderr, so this is the only place
  a caller can see them.

Verify: `bash -n scripts/delegate-codex.sh`, `python3 -m unittest tests.test_delegate_role`.

### 3. Config engine and schema doc

- `scripts/handoff-config.py`: `BACKENDS = ("claude", "codex", "copilot")`. `validate_config`
  already gates `backend` on that tuple, so a copilot config fails closed on a pre-3.7 engine —
  the same skew direction `docs/config-schema.md` already documents for identity names.
- `docs/config-schema.md`: three-value backend enum, the copilot effort enum in the fields table,
  the `auto` refusal, and a line stating all three are first-class execution channels.
  `schema_version` stays `2`; a new enum value is not a document-shape change.

Verify: `python3 -m unittest tests.test_handoff_config`.

### 4. Setup engine and smoke

- `scripts/handoff-setup.py`: `COPILOT_EFFORTS` into `BACKEND_EFFORTS`.
- The availability check at `handoff-setup.py:526` uses `shutil.which(backend)`, which cannot tell
  the two `copilot` binaries apart. Replace it with a `cli_available(backend)` helper: plain
  `which` for codex and claude, a `--version` identity match for copilot.
- `--smoke`: route copilot through the existing codex path,
  `delegate-codex.sh submit --read-only --dry-run` (`handoff-setup.py:866`). That exercises binary
  identity resolution, effort validation, and the `auto` refusal at zero request cost. The
  deliberate codex/claude smoke asymmetry is out of scope, as it was in v3.5.0.
- **Smoke fallback, if assumption 1 fails.** *Added after spec review.* That command passes exactly
  the flag copilot would then refuse, so smoke would fail every write-capable copilot identity — or,
  worse, be made to skip the capability check under `--dry-run` and pass misleadingly. If copilot
  cannot take `--read-only`, smoke drops the flag for copilot only and keeps `--dry-run`, and the
  printed result says which capability was checked. Decide this in task 1, not at implementation
  time.
- **Refuse `model = "auto"` at setup validation too**, not only at submit. `validate_backend_efforts`
  at `handoff-setup.py:94` checks effort alone, so a config naming `auto` currently applies cleanly
  and fails later at the first job. Also define the behaviour for a role-less ad-hoc copilot job
  that passes no `--model` at all.
- `PRESETS` unchanged, per the decision above.

Verify: `python3 -m unittest tests.test_handoff_setup`.

### 5. Setup UI — a probed model catalog

- `scripts/handoff-setup-ui.py`: new `_copilot_model_options(path, env)` beside
  `_codex_model_options` and `_claude_model_options`. It runs one throwaway `copilot -p` job and
  reads `session.auto_mode_resolved.data.availableModels` from the JSONL.
- **Discovery is on demand, not on page load.** *Changed after spec review.* `build_state` at
  `handoff-setup-ui.py:344` constructs discovery state for every supported backend when the wizard
  opens, so wiring copilot in there would spend a premium request for a user configuring only Codex
  and Claude. Discovery fires when copilot is selected for an identity, behind a control that names
  its cost before the first probe.
- **The probe's own invocation is specified, not left to implementation.** The catalog event was
  observed under `--model auto`, so the probe passes `--model auto` and **no** `--effort` (the pair
  is rejected at the CLI layer). It also gets the same controls the generated jobs get:
  `</dev/null`, `--no-ask-user`, `--no-remote --no-remote-export`, `--no-auto-update`, and a
  timeout. A temp working directory does not establish that nothing is in reach to edit, so restrict
  the tools explicitly for this answer-only probe rather than relying on the built-in baseline.
- **Cache it as a dated snapshot.** Write the catalog to
  `${XDG_CACHE_HOME:-$HOME/.cache}/handoff/copilot-models.json` with the `copilot --version` string
  and a timestamp, and read it before probing. Version alone is not a validity key: the research
  says the catalog is gated by account plan, org policy, and model rollout, all of which move
  without the CLI version moving. Label the value in the UI with its age.
- **Define the unhappy paths.** Failed discovery, an empty `availableModels`, and a failed refresh
  each need stated behaviour, and none of them may discard a model already configured. Offer manual
  model entry whenever discovery is unavailable — the selector at `handoff-setup-ui.py:1236`
  disables itself when it has no options, which would otherwise strand the user.
- **Refresh needs a server-side operation.** `/api/state` returns a copy of the startup state
  (`handoff-setup-ui.py:539`), so re-fetching it cannot re-probe. Refresh must re-run discovery on
  the controller and replace both the server-side catalog and the browser's copy.
- Report the source honestly in the per-identity source label the UI already renders: the model
  came from a probe job, whether the value was cached, and how old the cache is.
- JavaScript: third backend option in the identity matrix, a `modelCatalog` branch for copilot, a
  source-label map entry, and a rewrite of the "Codex models are read from your local account"
  subtitle, which is now wrong for two of three backends.

Verify: `python3 -m unittest tests.test_handoff_setup_ui`, plus opening the wizard and confirming
the catalog renders, that a second load hits the cache, and that Refresh busts it.

### 6. Receipt schema v6

- `docs/receipt-schema.json`: `$id` → `handoff.receipt.v6`; add `copilot_jobs` (integer, minimum 0)
  and `copilot_job_durations` (the same grammar as the existing durations fields) to `properties`
  and `required`; `receipt_schema_version` → const `6`; `roles_used[].host` enum gains `copilot`.
  Describe each count as the jobs its backend executed.
- `scripts/make-receipt.py`: `job_durations()` loops over a backend tuple instead of a hardcoded
  two-key dict. A job directory with no `backend=` line still buckets as codex, as it does today.
  New `--copilot-jobs` argument.
- `scripts/validate-receipt.py` has three hardcoded spots, not one. *Expanded after spec review:
  the first draft named only the JSON schema and would have shipped a validator that rejects its own
  new receipts.* `ROLE_HOSTS = {"claude_code", "codex"}` at `:35` gains `copilot`; the count loop
  and the durations loop at `:126` each enumerate two backends explicitly and must enumerate three;
  two fields join `REQUIRED_FIELDS` reusing `JOB_DURATIONS`; the version gate goes `5` → `6`. Add
  negative cases for an invalid copilot count, an invalid copilot duration string, and an unknown
  `roles_used[].host`.
- `SKILL.md`: the Output Contract text block gains the pair after `cc_job_durations`, and the prose
  below it explains the three-way partition and the `6`.
- Regenerate `examples/session-receipt.md`. Leave the archived
  `examples/v2.0.*` and `examples/v3.6.1-*` artifacts alone; they are historical, in older formats.
- `scripts/check-skill-repo.sh`: the "validates against the v5 schema" message becomes v6.
- `scripts/showcase-cost-ledger.py` stays as it is. Its `session_receipt_fields` list is
  illustrative and never carried `cc_jobs` either; the ledger is a workload pressure model, not a
  receipt contract. Confirm the reproducibility check still shows no diff.

Verify: `python3 -m unittest tests.test_receipt`, plus a **mixed receipt** generated from a repo
holding one job per backend and a `roles_used` entry with `host: copilot`, pushed through both
`validate-receipt.py` and `render-cost-receipt.py`. The roundtrip in `CLAUDE.md` uses
`--roles-used '[]'` and no copilot job, so on its own it cannot detect a missing host enum value or
prove the three-way partition.

### 7. Cost receipt — keyed internals and the credit meter

- **`SCHEMA_VERSION = "5"` at `render-cost-receipt.py:27` gates the whole renderer.** *Added after
  spec review; the first draft missed it entirely.* Left at `5` it rejects every v6 receipt before
  any copilot code is reached, which would make task 7 untestable end to end.
- `scripts/render-cost-receipt.py`: collapse `CODEX_FIELDS` and `CLAUDE_FIELDS` into one
  `USAGE_FIELDS` dict keyed by backend, and replace the two-branch job partitioning at `:82` with a
  loop over a backend tuple.
- `fold_usage` gains a copilot branch and a way to see `usage.json` — signature
  `fold_usage(events, backend, usage_json=None)`, with the caller reading the file so the function
  stays testable. Tokens come from `tokenDetails.<counter>.tokenCount` plus
  `modelMetrics.*.usage.reasoningTokens`; the key names already match `COUNTERS` verbatim, so no
  translation table is needed. Models from the `modelMetrics` keys. Credits from
  `totalPremiumRequestCost` and `totalNanoAiu`. Denials from the typed
  `tool.execution_complete` event.
- Job rows gain `premium_requests` and `nano_aiu`, `None` on the other two backends, rendered as
  two columns in the delegated-jobs table. The export at `render-cost-receipt.py:395` is a
  **deliberate field-by-field whitelist**, so the two new keys must be added there or they never
  reach the page.
- **`assets/cost-receipt.html` has to change too.** *Added after spec review; the first draft did
  not mention the template at all.* It renders the payload, so without an edit the HTML output drops
  the new meter and keeps attributing denials to Claude alone (`assets/cost-receipt.html:284`).
- One Summary sentence: how many jobs ran on the Copilot AI-credit meter, with the premium-request
  and nano-AIU totals, named as credits. Reporting them as a dollar figure would be a fabrication;
  they are neither codex's "subscription, no number" nor claude's `total_cost_usd`.
- `_figure`'s USD filter stays claude-only, which is already correct: Copilot reports no currency.
- The denials sentence stops saying "across claude-backed jobs" and names both backends that
  report denials, in the Markdown and in the HTML.
- **Unknown stays distinct from zero.** `_add` already keeps `None` apart from a measured zero
  (`render-cost-receipt.py:142`); missing or truncated `usage.json` must read unknown, with partial
  totals marked, never as zeros.
- **Resumed-job accounting.** Whether a resumed session's counters are per invocation or cumulative
  is probed in task 1. If cumulative, summing a parent and its fix round double-counts both tokens
  and credits, and the fold needs to take the last reading rather than add.

Verify: `python3 -m unittest tests.test_cost_receipt`, covering the credit columns in the payload
and in the HTML, unknown and partial telemetry, and parent-plus-resume accounting.

### 8. Transcript viewer

- `assets/transcript-viewer.html`: a copilot branch in the log parser, **gated on the payload's
  backend** rather than the event shape, for the `result` collision reason.
- Drop every event with `ephemeral: true`; one small job produced 82 ephemeral
  `assistant.message_delta` events, so this is the transcript filter, not an optimisation.
- Fold `tool.execution_start` and `tool.execution_complete` by `toolCallId` — the same folding the
  viewer already does by `item.id` and `tool_use_id`. Map `assistant.message` with
  `phase == "final_answer"` to an agent row, `session.error` to an error row, and the turn events to
  lifecycle rows.
- `scripts/render-transcript.py` needs no parsing change; a generated payload already carries
  `meta.backend` (`render-transcript.py:96`).
- **The drag-and-drop path has no metadata.** *Added after spec review.* Dropping a raw log mounts
  it with `meta: {}` (`assets/transcript-viewer.html:748`), so a parser that requires
  `meta.backend` breaks Copilot raw-log viewing and risks regressing older logs. Give that path a
  backend selector, or infer from an unambiguous event — `assistant.turn_start` or
  `tool.execution_start` for copilot, `item.completed` for codex. Never infer from `result`, which
  is the colliding type.

Verify: `node --test tests/test_transcript_viewer.mjs`,
`python3 -m unittest tests.test_render_transcript`, with copilot fixtures covering the
metadata-free drop path, a failed tool call, ephemeral events, and a truncated log.

### 9. Prose, diagrams, and regression prompts

- `SKILL.md`: description, Overview, Routing Rules, Configuration, Output Contract. Version
  `3.6.1` → `3.7.0`. The description currently names Codex heavily; it should name all three
  backends without turning into a list of vendors.
- `references/fable5-principles.md`: the delegation-channel table now has three job backends and
  the same two in-process subagent escape hatches. The rule that an identity is never swapped to
  move a job to another vendor extends to three backends unchanged, and should say three.
- `references/claude-driven.md`, `references/goal-template.md`,
  `references/handoff-template.md`, `references/e2e-gauntlet.md`, `references/tryout.md`: sweep for
  two-backend enumerations. Most of the v3.5.0 wording is already backend-neutral; the failure mode
  is a stray "codex or claude" pair.
- `README.md` badge and identity table, **and its live receipt example at `README.md:135`**, which
  carries the field list.
- `CHANGELOG.md` `## v3.7.0`, a `docs/releases/` entry.
- `references/setup.md:14` — its discovery, effort-selection, and automatic-smoke contract all
  change.
- `CLAUDE.md` — *added after spec review.* It already says "schema v4" and "Codex executes
  delegated work on its own subscription" (`CLAUDE.md:53`), both stale since v3.5.0. This plan moves
  the schema again, so correct the implementation-facing guidance rather than leaving it two
  versions behind.
- `references/claude-driven.md:98` — the monitor is told to read stderr on a failure. Copilot's API
  errors can arrive with stderr empty and the detail only in the JSONL `session.error` event, so
  that instruction has to name the log.
- `docs/user-guide/agent-handoff.html`: version and reviewed date, backend enumerations, the job
  primitive table, the receipt v6 field list, and the cost-receipt section's credit meter.
- Diagrams, text-only edits with no coordinate shifts: `phase1-plan-split.svg`,
  `phase2-delegate.svg`, `flow-overview.svg`, `phase5-wrap-up.svg`.
- `test-prompts.json`: a copilot identity is delegated on its own backend and gets a jobId; the
  driver never substitutes a different identity to change vendor or meter across any of the three;
  a fix round on a copilot job resumes the same session on the same backend; a job with typed
  denials is treated as failed whatever its exit code says; the receipt splits three counts rather
  than folding copilot into either existing one.

Verify: `bash scripts/check-skill-repo.sh .`, `python3 scripts/english-only-scan.py`,
`python3 scripts/run-test-prompts.py`, and the SVG well-formedness parse
(`python3 -c "import xml.dom.minidom,glob;[xml.dom.minidom.parse(f) for f in glob.glob('docs/user-guide/diagrams/*.svg')]"`).
Two caveats on what those establish: `run-test-prompts.py` validates the file's structure, not agent
behaviour, and the XML parse proves well-formedness, not that a longer label still fits its card. The
diagrams need an eyeball pass for clipping after the text edits.

### 10. Tests — three-backend parity, asserted

The v3.5.1 lesson drives this task. That suite asserted the argv it was told to expect and never
asked whether that argv let the worker work: `--permission-mode` appeared in no assertion at all,
so the defect was invisible to a green suite. A parity claim needs a test for the capability, not
only for the plumbing.

- `tests/test_delegate_role.py`: `make_env` gains a fake `copilot` whose `--version` prints
  `GitHub Copilot CLI 1.0.83.`, **and a second fake printing `copilot version: v1.34.1`** so the
  binary-identity guard is tested rather than asserted in prose. Cover both orderings on PATH.
- A copilot subclass of the backend-agnostic mixin v3.5.0 introduced. *Corrected after spec
  review:* it is `BackendLifecycle` at `tests/test_delegate_role.py:277`, inherited by
  `CodexWorktreeTests` and `ClaudeWorktreeTests`, and it holds 12 shared tests, not 8.
- **A third subclass alone will not cover the assigned id.** The lifecycle fixture deletes
  `session_id` before parsing its synthetic log (`tests/test_delegate_role.py:393`), so add a case
  that preserves the assigned cache and omits the terminal event — that is exactly the crashed-job
  shape the decision exists for.
- **Binary discovery tests:** an explicit `HANDOFF_COPILOT_BIN`, an AWS-only install (must fail
  closed with an actionable message), both PATH orderings with the two fakes present, and a resume
  whose recorded parent path now resolves to a different tool.
- Argv assertions must include `--allow-all-tools`, `--no-remote`, `--no-remote-export`,
  `--usage-output-file`, `--session-id` on a fresh job and `--resume` on a resume, and the
  **absence** of `--share`, `--share-gist`, `--yolo`, `--allow-all`, and `--worktree`. The egress
  and permission flags are the capability assertions; omitting them is how v3.5.0 shipped a green
  suite over a broken default.
- A `--read-only` job asserts `--mode plan` and the absence of `--allow-all-tools`.
- Monitoring parity: `status` and `result` return the same shape for a copilot job, including the
  typed denial count.
- `tests/test_receipt.py`: v6 round-trip, all three counts, a mixed three-backend repo partitioning
  correctly, and a v5 receipt now failing validation.
- `tests/test_cost_receipt.py`: copilot `usage.json` folding, the credit columns, denial counting,
  and a copilot job contributing no USD to the cost figure.
- `tests/test_handoff_config.py`: copilot accepted, an unknown backend still refused.
- `tests/test_handoff_setup.py`: the copilot effort enum, the `auto` refusal, the `cli_available`
  identity check, and the smoke path.
- `tests/test_handoff_setup_ui.py`: the catalog probe, the cache hit, and the refresh.

Verify: `python3 -m unittest discover -s tests` fully green.

## Verification

Everything `.github/workflows/checks.yml` runs, which the first draft under-listed:

```bash
bash -n install.sh
bash -n scripts/check-skill-repo.sh
bash -n scripts/delegate-codex.sh
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
python3 scripts/english-only-scan.py
bash install.sh --dry-run

SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

Plus the receipt roundtrip from `CLAUDE.md`, run against a mixed-backend repo per task 6.

Live end-to-end, needing a real `copilot` CLI and a scratch repo with a copilot-backed identity:

```bash
bash scripts/delegate-codex.sh submit --repo <tmp> --prompt-file <packet> \
  --label smoke --role fast_worker
bash scripts/delegate-codex.sh status <jobId> --repo <tmp> --wait --timeout 300
bash scripts/delegate-codex.sh result <jobId> --repo <tmp>
```

Name a concrete packet with one expected edit and one repository check the worker must run, then
confirm: the expected file changed; the named check actually executed, with its output in the log;
`meta` records `backend=copilot`, `session_id_source=assigned`, and an id matching
`$JOB/session_id`; `usage.json` carries token counts; `result --json` reports zero denials; a
`resume` round lands on the same session and its accounting does not double-count the parent; a
`--worktree` job pins its base SHA and cleans up; and a generated receipt counts the job under
`copilot_jobs` rather than either existing field. The first draft only submitted, polled, and read
one job, which established none of the resume, worktree, or receipt claims.

## Assumptions to probe

Planning chose spec-first, so these are stated rather than verified. Task 1 closes them and writes
each result back into the research document. Three of the five are **gates**: they decide what the
feature can claim, and the plan is conditional until they resolve.

1. **Gate. `--mode plan` is genuinely read-only in a `-p` run**, and composes sensibly with the fact
   that a read-only job does not pass `--allow-all-tools`. Highest risk of the five. The research
   marked this `[open]` and explicitly warned against trusting the flag name. Resolving it fixes
   both the affected-invocation list in task 1 and the smoke fallback in task 4.
2. **Gate. `--session-id <uuid>` sets the id for a new `-p` session**, the terminal `result` echoes
   it back, **and an interrupted session is genuinely resumable from it.** This is `[help]` text,
   not probed behaviour, and the entire justification for assigning rather than extracting is
   crash-resumability — which a clean run cannot demonstrate.
3. **`--log-dir` inside the job dir works** without fighting `--output-format json` on stdout. Fits
   inside task 1; the cleanup expectation is already corrected above.
4. **`--usage-output-file` on failure, and the scope of a resumed session's counters.** Written on a
   non-zero exit, across all three failure classes? And are a resumed session's counters per
   invocation or cumulative? Fits inside task 1 with an explicit unknown-or-partial fallback in the
   cost receipt, but the cumulative case changes how the fold works.
5. **Gate. A repository's own verification command runs under `--allow-all-tools`** in a background
   `-p` job with no tty. This is the capability that made v3.5.1 necessary on the claude backend,
   and without it a delegated Copilot job cannot be promised as usable. Evidence is the check's
   own output, not the worker's closing claim.

## Not doing

- No `HANDOFF_COPILOT_PERMISSION_MODE`, per the decision above.
- No receipt field generalisation to a backend-keyed map. The flat pair was chosen; the internals
  are what get generalised.
- No Copilot entry in `PRESETS`.
- No `--worktree`, `--share`, `--share-gist`, `--yolo`, `--allow-all`, `--enable-memory`,
  `--secret-env-vars`, `--max-ai-credits`, `--agent`, `--add-dir`, or MCP wiring. Handoff owns the
  worktree protocol, pinned to an immutable base SHA; handing that lifecycle to the CLI would give
  up the pinning and the cleanup.
- No scavenging of `~/.copilot/logs` or `session-store.db` for a free model catalog. It would work
  today, but it depends on an undocumented on-disk layout that GitHub can change without notice.
- No credential stripping in the copilot branch of `run.sh`.
- No rename of `delegate-codex.sh`, no new delegation script, no config `schema_version` bump.
- No change to the Sub Agent Routing three-level lookup; it stays for in-session subagents.
- No rewrite of `CHANGELOG.md` or `docs/specs/` history.

## Risks

- **Assumption 1 can shrink the feature.** If `--mode plan` is not read-only under `-p`, a copilot
  identity cannot serve spec review, arbiter blind-solve, or `e2e_specifier` in 3.7.0. The
  mitigation is to find out in task 1, before the exec line is written, and to say so in the flow
  prose rather than shipping a `--read-only` that quietly writes.
- **Model availability is per account and cannot be validated cheaply.** A model name that works on
  one machine returns "not available" on another, decided by plan, org policy, and rollout. The
  wizard's probe reflects the account that ran it. A config shared between machines will fail at
  submit with Copilot's own plain-text stderr line, which is the right place for it, but it is a
  failure mode neither existing backend has.
- **The catalog probe spends a premium request.** Caching against `copilot --version` bounds it to
  once per CLI version, but a user who refreshes repeatedly pays for it. The Refresh control should
  say so.
- **Effort validity is decided per model by the API, and the CLI enum is only a superset.** Handoff
  passes the configured effort and surfaces the rejection; it does not silently downgrade. A user
  can therefore hold a configuration that validates locally and 400s on their account.
- **Three backends is where the parser branching starts to cost.** `cmd_result`, `fold_usage`, and
  the viewer's JavaScript each carry three shapes now. The keyed-internals decision addresses the
  field tables; the event-shape branches stay per-vendor. If a fourth backend
  arrives, revisit whether the event normalisation belongs in one place.
- **Fake-CLI tests prove wiring, not behaviour.** The parity suite asserts argv, job state, and
  worktree lifecycle against stub binaries. That a real Copilot worker completes a real task and
  runs the repo's own checks is covered only by the live smoke run and assumption 5, both of which
  need a human to trigger them.

## Spec Review

Reviewed once by `deep_reasoner` (codex / gpt-6-astra / xhigh, read-only) as job
`job-2026-09-09T18-08-31-75493-spec-review`. Zero permission denials, 18 commands run. Findings
were checked against the repository before folding, and every claim below verified.

**Accepted and folded in.** Nine findings, the three worst being gaps that would have broken the
implementation:

- `validate-receipt.py` carries `ROLE_HOSTS` and two explicit two-backend loops that the draft
  never mentioned, so the validator would have rejected the receipts this plan generates.
- `render-cost-receipt.py:27` pins `SCHEMA_VERSION = "5"`, which rejects every v6 receipt before
  reaching any copilot code.
- `assets/cost-receipt.html` was missing from the plan entirely, along with the field-by-field
  export whitelist at `render-cost-receipt.py:395`.

Also folded: one shared binary-resolution contract across submit, resume, setup, and the UI, with
`cmd_resume`'s unchecked parent-path reuse closed; denial counting moved from a grep to the parsed
pass; a concrete definition of "treated as failed" that does not change machine state; on-demand
rather than page-load model discovery, with the probe's own invocation and tool restriction
specified; a dated cache instead of a version-keyed one, plus the unhappy paths and a server-side
refresh; a backend selector for the viewer's metadata-free drag-and-drop path; the CI gates the
Verification block had under-listed.

**Three factual corrections to the draft.** `e2e_specifier` is a writer, not a `--read-only`
consumer, so a failing assumption 1 does not remove it. `cmd_cleanup` keeps the job directory, so
`--log-dir` co-locates evidence but does not cause its removal. The lifecycle mixin is
`BackendLifecycle` with 12 shared tests, not `WorktreeTests` with 8.

**One decision reversed.** The draft excluded `none` from the copilot effort enum because the API
rejected it on the observed model. Reading that 400 again, the same model also rejects `xhigh` and
`max`, which the draft kept — so the stated reason excluded one of three arbitrarily. The enum now
mirrors the CLI's and a per-model rejection surfaces as Copilot's own error, which is what the
plan's own no-silent-downgrade principle already required.

**Nothing rejected.** Every finding either verified or was a correction of the plan.
