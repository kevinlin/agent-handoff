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
  instead of documented with an exception.

- **Effort excludes `none`.** The CLI accepts `none|minimal|low|medium|high|xhigh|max`, but `none`
  is a literal value passed through to the API, not a sentinel meaning "omit the flag", and the API
  rejected it on the observed model with a 400. An identity carrying `effort = "none"` is a config
  that fails at runtime, so `validate_effort copilot` refuses it at submit, where the error is
  cheap and legible. Handoff's copilot enum is `minimal|low|medium|high|xhigh|max`.

- **`model = "auto"` is refused for a configured identity.** Mechanically, `auto` plus any effort is
  rejected at the CLI layer, and every identity requires a non-empty effort. Conceptually, an
  identity is a deliberate `backend + model + effort` choice and the flow's own rule is that the
  driver never re-decides routing per run; `auto` hands that choice back to the vendor. The refusal
  uses the same "change the config" wording as the existing `--backend` contradiction guard.

- **A delegated Copilot job runs with `--allow-all-tools`.** This mirrors the `bypassPermissions`
  default a delegated Claude job already gets, and the reasoning is the v3.5.1 retro. A background
  `-p` job has no approval surface, so any rule that would prompt denies instead; a denied tool call
  does not move the exit code; and the worker then reports that gates passed which it was in fact
  refused. What bounds a delegated worker is its worktree and its packet. Copilot's typed
  `error.code: "denied"` event is the safety net, and a job with denials is treated as failed
  whatever its exit code says.

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

- **No credential stripping for the copilot branch.** `handoff_runtime.clean_claude_env()` exists
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

Assumption 1 is the one that can change scope: if `--mode plan` is not read-only in a `-p` run,
the copilot backend refuses `--read-only` in 3.7.0 and the flow says so, which removes spec
review, arbiter blind-solve, and `e2e_specifier` from a copilot identity.

Verify: five recorded results, each with the command run and the observed output.

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
  path. `--log-dir` points inside the job dir so `cleanup` takes Copilot's own logs with it instead
  of leaving them under `~/.copilot/logs`.
- `warn_permission_bypass`: fires for a copilot job under `--allow-all-tools` as well, with the
  backend named in the message.
- `extract_session_id`: add `sessionId` to the key tuple. The assigned id means the cache is
  already populated for a Handoff-submitted job; this covers a job submitted by hand.
- `cmd_status` denial count: one expression covering both shapes,
  `grep -cE '"subtype":"permission_denied"|"code":"denied"'`. No branch, correct on all three
  backends, and over-reporting is the safe direction for a signal whose whole purpose is that exit 0
  lies.
- `cmd_result`: pass the backend from `meta` into the Python heredoc as an argv, and add a copilot
  branch. Skip events with `ephemeral: true`. Message from `assistant.message` where
  `data.phase == "final_answer"`; commands from `tool.execution_start.data.arguments.command`;
  denials from `tool.execution_complete` where `data.error.code == "denied"`, named from the
  matching `toolCallId`'s start event; `session.error` surfaced as a failure line with its
  `statusCode`; usage from the flat terminal `result` event. Gating on backend is not tidiness: the
  `result` type name collides with Claude's.

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
  `delegate-codex.sh submit --read-only --dry-run`. That exercises binary identity resolution,
  effort validation, and the `auto` refusal at zero request cost. The deliberate codex/claude smoke
  asymmetry is out of scope, as it was in v3.5.0.
- `PRESETS` unchanged, per the decision above.

Verify: `python3 -m unittest tests.test_handoff_setup`.

### 5. Setup UI — a probed model catalog

- `scripts/handoff-setup-ui.py`: new `_copilot_model_options(path, env)` beside
  `_codex_model_options` and `_claude_model_options`. It runs one throwaway `copilot -p` job in a
  temporary directory and reads `session.auto_mode_resolved.data.availableModels` from the JSONL.
- **Cache it.** Write the catalog to `${XDG_CACHE_HOME:-$HOME/.cache}/handoff/copilot-models.json`
  keyed on the `copilot --version` string, and read the cache before probing. Without this every
  wizard load spends a premium request. The UI gets an explicit Refresh that busts the cache.
- The probe runs in a temp directory with no `--allow-all-tools`, so the built-in baseline applies
  and there is nothing in reach to edit. Bound it with a timeout.
- Report the source honestly in the per-identity source label the UI already renders: the model
  came from a probe job, and whether the value was cached.
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
- `scripts/validate-receipt.py`: two fields into `REQUIRED_FIELDS` reusing `JOB_DURATIONS`;
  version gate `5` → `6`.
- `SKILL.md`: the Output Contract text block gains the pair after `cc_job_durations`, and the prose
  below it explains the three-way partition and the `6`.
- Regenerate `examples/session-receipt.md`. Leave the archived
  `examples/v2.0.*` and `examples/v3.6.1-*` artifacts alone; they are historical, in older formats.
- `scripts/check-skill-repo.sh`: the "validates against the v5 schema" message becomes v6.
- `scripts/showcase-cost-ledger.py` stays as it is. Its `session_receipt_fields` list is
  illustrative and never carried `cc_jobs` either; the ledger is a workload pressure model, not a
  receipt contract. Confirm the reproducibility check still shows no diff.

Verify: `python3 -m unittest tests.test_receipt`, plus the receipt roundtrip in `CLAUDE.md`.

### 7. Cost receipt — keyed internals and the credit meter

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
  two columns in the delegated-jobs table.
- One Summary sentence: how many jobs ran on the Copilot AI-credit meter, with the premium-request
  and nano-AIU totals, named as credits. Reporting them as a dollar figure would be a fabrication;
  they are neither codex's "subscription, no number" nor claude's `total_cost_usd`.
- `_figure`'s USD filter stays claude-only, which is already correct: Copilot reports no currency.
- The denials sentence stops saying "across claude-backed jobs" and names both backends that
  report denials.

Verify: `python3 -m unittest tests.test_cost_receipt`.

### 8. Transcript viewer

- `assets/transcript-viewer.html`: a copilot branch in the log parser, **gated on the payload's
  backend** rather than the event shape, for the `result` collision reason.
- Drop every event with `ephemeral: true`; one small job produced 82 ephemeral
  `assistant.message_delta` events, so this is the transcript filter, not an optimisation.
- Fold `tool.execution_start` and `tool.execution_complete` by `toolCallId` — the same folding the
  viewer already does by `item.id` and `tool_use_id`. Map `assistant.message` with
  `phase == "final_answer"` to an agent row, `session.error` to an error row, and the turn events to
  lifecycle rows.
- `scripts/render-transcript.py` needs no parsing change; all log parsing lives in the page.

Verify: `node tests/test_transcript_viewer.mjs`,
`python3 -m unittest tests.test_render_transcript`.

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
- `README.md` badge and identity table, `CHANGELOG.md` `## v3.7.0`, a `docs/releases/` entry.
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
`python3 scripts/run-test-prompts.py`,
`python3 -c "import xml.dom.minidom,glob;[xml.dom.minidom.parse(f) for f in glob.glob('docs/user-guide/diagrams/*.svg')]"`.

### 10. Tests — three-backend parity, asserted

The v3.5.1 lesson drives this task. That suite asserted the argv it was told to expect and never
asked whether that argv let the worker work: `--permission-mode` appeared in no assertion at all,
so the defect was invisible to a green suite. A parity claim needs a test for the capability, not
only for the plumbing.

- `tests/test_delegate_role.py`: `make_env` gains a fake `copilot` whose `--version` prints
  `GitHub Copilot CLI 1.0.83.`, **and a second fake printing `copilot version: v1.34.1`** so the
  binary-identity guard is tested rather than asserted in prose. Cover both orderings on PATH.
- A copilot subclass of the backend-agnostic `WorktreeTests` base v3.5.0 introduced, so all eight
  lifecycle assertions run on the third backend too.
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

```bash
python3 -m unittest discover -s tests
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
python3 scripts/english-only-scan.py
node tests/test_transcript_viewer.mjs

SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

Live end-to-end, needing a real `copilot` CLI and a scratch repo with a copilot-backed identity:

```bash
bash scripts/delegate-codex.sh submit --repo <tmp> --prompt-file <packet> \
  --label smoke --role fast_worker
bash scripts/delegate-codex.sh status <jobId> --repo <tmp> --wait --timeout 300
bash scripts/delegate-codex.sh result <jobId> --repo <tmp>
```

Confirm the job edited files, `meta` records `backend=copilot` and the assigned session id, a fix
round resumes that same session, `usage.json` carries token counts, and the receipt counts the job
under `copilot_jobs` rather than either existing field.

## Assumptions to probe

Planning chose spec-first, so these are stated rather than verified. Task 1 closes them and writes
each result back into the research document.

1. **`--mode plan` is genuinely read-only in a `-p` run**, and composes sensibly with the fact that
   a read-only job does not pass `--allow-all-tools`. Highest risk of the five: spec review,
   arbiter blind-solve, and `e2e_specifier` all ride on `--read-only`. The research marked this
   `[open]` and explicitly warned against trusting the flag name.
2. **`--session-id <uuid>` sets the id for a new `-p` session**, and the terminal `result` echoes
   the same id back. This is `[help]` text, not a probed behaviour, and the whole resume-parity
   decision rests on it.
3. **`--log-dir` inside the job dir works** without fighting `--output-format json` on stdout.
4. **`--usage-output-file` is written on a non-zero exit too.** If it is only written on success, a
   failed job has no token counters and the cost receipt has to say so rather than report zeros.
5. **A repository's own verification command runs under `--allow-all-tools`** in a background `-p`
   job with no tty. This is the capability that made v3.5.1 necessary on the claude backend.

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
