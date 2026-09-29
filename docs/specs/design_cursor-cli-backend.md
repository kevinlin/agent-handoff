# Design: Cursor CLI as the fourth backend

## Context

Handoff runs delegated work on three backends: `codex`, `claude`, and `copilot`. This design adds `cursor`, driven through `cursor-agent -p --output-format stream-json`, at the parity bar the flow's second invariant sets: a cursor-backed row is the same job as any other.

- What must hold: [requirements_cursor-cli-backend.md](requirements_cursor-cli-backend.md). Requirement ids (R1.1 …) are cited below.
- What the CLI actually does: [../research/cursor-cli-specification.md](../research/cursor-cli-specification.md), fifteen live runs on build `2026.09.28-64d2043`. Nothing here rests on Cursor's docs alone where a probe was possible.

## Approach

**A fourth branch in each place that branches on backend**, following the Copilot integration in v3.7.0. Tables already keyed by backend (`BACKENDS`, `BACKEND_EFFORTS`, `BACKEND_FIELDS`, `USAGE_FIELDS`) each gain a row. The five event parsers each gain an explicit cursor branch:

1. `STATUS_SCAN_PY` in `delegate-codex.sh`
2. `cmd_result` in `delegate-codex.sh`
3. `fold_usage` in `render-cost-receipt.py`
4. `denials()` in `handoff-session-ui.py`
5. `HV.normalize` and `HV.inferBackend` in `assets/transcript-viewer.html`

The alternative was to move the four Python parsers onto one event-normalization module first and add Cursor as a single entry. It was rejected for this release. It would refactor three working backends and add a fourth in one change, which `references/darwin-ratchet.md` rules out ("one dimension per change"). The JavaScript viewer would also still need its own branch. It is listed under *Follow-ups*.

## Decisions

### 1. Effort lives in the model slug; cursor's effort enum is `("model",)`

Cursor has no effort flag. Effort sits inside the slug (`claude-opus-5-5-high`) next to two other axes (`-thinking`, `-fast`). The spelling is irregular (`gpt-5.5-extra-high`), some slugs have no effort at all (`composer-2.5`), and the bracket form the help advertises (`model[effort=low]`) was refused on both models tried.

So a cursor identity stores the whole slug as `model`, and `effort` holds the single value `model`, meaning "set by the model id". The effort value never reaches the command line. Any other value is refused (R1.2).

Rejected alternatives:

- **Derive effort from the slug and cross-check it.** Readable receipts, but it needs a parser for a naming scheme Cursor does not promise and already breaks.
- **Store base + effort and compose the slug.** The cleanest-looking config, but it cannot express `claude-opus-5-thinking-high-fast` and depends on the same unpromised scheme.

Cost of the choice: cursor rows show effort `model` in `--status` and `roles_used`. The slug beside it already names the effort.

### 2. Posture: `default` and `allow-all` both mean `--force`; read-only means `--mode plan` <!-- risk-ok: Cursor CLI flag name -->

The three existing `default` postures share one property: checks run, nothing prompts, and one guard on the provider's side stays on. On Cursor, the probes left one option with that property:

| Candidate for `default` | Probed result on this machine |
|---|---|
| No flag: the user's Cursor allowlist decides | `bash check.sh` and `curl` rejected. A worker cannot verify its work, which is the v3.5.1 failure. |
| `--sandbox enabled` | The shell could not start (`spawn /bin/zsh ENOENT`), and the worker stopped after that first attempt. |
| `--force --sandbox enabled` | Confined nothing. The outside write landed and the network call returned 200. | <!-- risk-ok: Cursor CLI flag name -->
| `--force` | Checks run, nothing prompts, and explicit deny rules in user or project config still apply. | <!-- risk-ok: Cursor CLI flag name -->

`--force` is the Cursor analogue of Copilot's `--allow-all-tools`: everything is auto-approved except what the user's own rules deny. `allow-all` maps to the same flag, because `--force` already is Cursor's native unrestricted mode (`--yolo`, "Run Everything"). The posture field is therefore a no-op on Cursor, and R3.2 requires the docs to say so. <!-- risk-ok: Cursor CLI flag name -->

`allow-all` does not add `--sandbox disabled`. The research doc forbids that, and on this machine it would change nothing observable.

Read-only stays `--mode plan` and never carries `--force`. Probe E found no silent failure when the two are combined, unlike on Copilot. So on Cursor the rule guards against a failure nobody has observed. It costs nothing and keeps the four backends' read-only rule identical. <!-- risk-ok: Cursor CLI flag name -->

### 3. Session id from the log, not pre-assigned

`session_id` is on every event starting at line one, and a session killed mid-run resumed with its context (probe G). That is the Claude backend's shape, so the existing `extract_session_id` works unchanged: its key tuple already includes `session_id`. `create-chat` would give Copilot-style assignment at submit, but it would buy nothing here and adds a network call that can fail before launch.

### 4. Binary: `cursor-agent`, mapped explicitly, no identity check

The backend is named `cursor` and the binary `cursor-agent`, while `cursor` on PATH is the IDE launcher. Every place that maps a backend name to a binary name does so explicitly:

- `resolve_worker_bin` gets a `cursor)` case (`HANDOFF_CURSOR_BIN`, then `command -v cursor-agent`). Today its `*)` case would read `HANDOFF_CODEX_BIN`, and its final fallback would run `command -v cursor` and find the IDE.
- `cli_available` in `handoff-setup.py` does the same. Today it would call `shutil.which("cursor")`.

There is no `--version` identity check like Copilot's. `cursor-agent --version` prints a bare build string with no product name to match, and no other tool is known to ship a `cursor-agent`. `agent` is never used.

### 5. `auto` refused; base names checked at setup only

`model = "auto"` is refused at setup and at submit, for Copilot's reason: it hands the choice back to the vendor on every request (R1.3). A base name outside the catalogue (`gpt-5.4-mini`) is accepted by the CLI and quietly resolved to a variant. The wizard only offers catalogue slugs, so this reaches a job only through a hand-written config. Submit does not re-read the catalogue. The `init` event's display name, which the transcript and cost receipt show, records what actually ran.

### 6. Command lines

| Case | Command line in `run.sh` |
|---|---|
| fresh | `cd "$WORKDIR"` then `"$CODEX_BIN" -p "$PROMPT" --output-format stream-json --trust --workspace "$WORKDIR" --model "$MODEL" $POSTURE` |
| resume | `cd "$WORKDIR"` then `"$CODEX_BIN" -p "$PROMPT" --output-format stream-json --trust --resume "$SESSION_ID" $POSTURE` |
| `$POSTURE` | `--mode plan` when read-only, else `--force` | <!-- risk-ok: Cursor CLI flag name -->

All redirect to `log.jsonl` and `stderr.log`, with stdin from `/dev/null`. `--trust` is always passed, because a fresh worktree is a workspace Cursor has never seen. The never-passed list is R2.4. `$CODEX_BIN` keeps its historical name, as it does for the other backends.

No environment scrubbing: Cursor authenticates through its own login or `CURSOR_API_KEY`, and reads none of the `ANTHROPIC_*` or `CLAUDE_CODE_*` variables `clean_claude_env()` removes. That is Copilot's rule, for the same reason.

### 7. Parsing a cursor log

- **Denial:** `type == "tool_call"`, `subtype == "completed"`, and a `rejected` key under `tool_call.<kind>.result`. Typed, so it is counted in the parsed pass and never by grep.
- **Agent message:** the text of the last `assistant` event. Cursor's `result.result` concatenates every assistant message. The generic path that `cmd_result` uses for Claude appends `result.result`, so it would present the whole narration as the final answer.
- **Commands:** `tool_call/started` → `shellToolCall.args.command`.
- **Usage:** the last `result.usage`: `inputTokens`, `outputTokens`, `cacheReadTokens`, `cacheWriteTokens`. It is per invocation, so the usage of a parent and its fix round is summed.
- **Model:** the `system/init` `model` field, a display name.
- **Noise:** `thinking/*`, 182 to 337 events per small job, skipped everywhere.
- **Tool-call ids** contain an embedded newline. They fold correctly as map keys, but must be escaped wherever they are displayed.

### 8. No backend's log may reach another backend's parser

Tracing the five parsers and the delegate script found six places where an unrecognized backend silently lands in another backend's branch:

| Place | Catch-all today | Effect on an unhandled cursor job |
|---|---|---|
| `write_run_script` | `else` → codex exec line | a codex command line run against the cursor binary |
| `resolve_worker_bin` | `*)` → `HANDOFF_CODEX_BIN`, then `command -v cursor` | the IDE launcher |
| `validate_effort` | `*)` → codex enum | `model` refused, `high` accepted |
| `STATUS_SCAN_PY` | non-copilot → Claude's `permission_denied` subtype | denials never counted |
| `fold_usage` | `else` → Claude | tokens unknown, and `denials = 0` reported as measured |
| `handoff-session-ui.py` `denials()` | non-codex, non-claude → Copilot | "reported", 0 items |

Each gains an explicit `cursor` branch. The Claude branch of `fold_usage` and the Copilot branch of `denials()` become explicit `elif`s, so the next backend added cannot inherit them (R7.4). The generic catch-alls in the bash script stay unchanged. The three backends they cover still work, and hardening them is a separate change.

### 9. Receipt v7, flat pair

`cursor_jobs` and `cursor_job_durations` join the three existing pairs, following the flat-field decision from v3.7.0. That release already keyed the Python internals by backend. So the change is one row each in `BACKENDS` (`make-receipt.py`), `ROLE_HOSTS` and its loops (`validate-receipt.py`), and `BACKEND_FIELDS` (`render-cost-receipt.py`). The JSON schema gains the pair, and the version gates move from 6 to 7.

`tests/test_receipt.py` uses `cursor` today as its example of an unknown host (line 131) and an unknown backend (line 337). Both switch to another placeholder.

### 10. Cost receipt: tokens, no figure

Cursor reports four token counters and no cost of any kind. A cursor job row shows tokens, a `None` USD figure, and no credits. The summary says the jobs ran on the Cursor meter and that Cursor reports no cost figure. Showing any dollar amount would be made up.

### 11. The wizard reads the catalogue at page load

`cursor-agent models` is free and takes about a second, the same cost profile as codex's `model/list`, so it runs in `build_state` when the page loads. The output is text only (`slug - Display name`) and gets a small line parser. When the CLI is missing, not logged in, or times out, no model is offered and the page names the fix. A configured slug that has left the catalogue stays in the form, marked, as a Copilot model does.

### 12. Smoke: one free-or-cheap real call

Smoke runs the dry-run chain and then `cursor-agent -p "…HANDOFF_SMOKE_OK…" --output-format stream-json --trust --model <slug> --mode ask`. The cost is lopsided, and measured. A slug the account cannot use is refused before any session, at no cost, and its stderr is quoted verbatim (it lists the available models). A usable slug costs one small request, about 16k input tokens on `gpt-5.4-mini`. This is `validate_cursor_model` beside `validate_copilot_pair`, not a shared helper. The command lines and the parsing differ, and approach A keeps them apart.

## Mapping audit

| Abstraction Handoff needs | Cursor mechanism | Status |
|---|---|---|
| Non-interactive submit | `-p "<prompt>"` | probed |
| Event stream | `--output-format stream-json` | probed |
| Session id | `session_id` on every event from `system/init` | probed |
| Fix round on the same session | `--resume <id>`, cwd from the shell | probed, context intact |
| Resume after a crash | kill after init, then `--resume` | probed, context intact |
| Working directory | `cd` plus `--workspace` fresh; `cd` on resume | probed |
| Effort as a routing field | none; the slug carries it | probed; decision 1 |
| Read-only job | `--mode plan` | probed: no writes, honest report |
| Default posture that can run checks | `--force` | probed; decision 2 | <!-- risk-ok: Cursor CLI flag name -->
| Denial detection | `tool_call` `result.rejected` | probed, typed |
| Final agent message | last `assistant` event | probed |
| Commands run | `shellToolCall.args.command` | probed |
| Token counters | `result.usage`, per invocation | probed |
| Cost figure | none | probed: absent |
| Model catalogue | `cursor-agent models`, free, text | probed |
| Pre-session model check | stderr `Cannot use this model: …`, exit 1 | probed, free |
| Binary discovery | `cursor-agent` | probed; `cursor` is the IDE |
| Mid-run API failure | unknown | **open** |

## Deviations from `docs/research/cross-agent-cli-permissin-mode.md`

That document's Cursor rows are a proposal. The probes contradict part of it, and each deviation is named here:

1. **`default` is `--force`, not `--sandbox enabled` plus an allowlist.** On the probed machine the sandbox cannot start a shell, and the stock allowlist rejects the checks. The document's own rule treats unapproved actions as failures and never escalates to `--force`. Applied here, that rule gives a posture in which no worker can verify its work. <!-- risk-ok: Cursor CLI flag name -->
2. **`default`'s network-denied and workspace-only-writes goals are not met.** No Cursor posture Handoff can set achieved them while still running checks. This matches the named gap for Claude's `default` in `docs/specs/design_agent-handoff.md` (no OS-level sandbox).
3. **Kept:** `allow-all` never adds `--sandbox disabled`.

## Where it fails closed, and where it doesn't

These rows join the table in `docs/specs/design_agent-handoff.md` once the feature ships:

| Guard | Enforced by | Failure mode when skipped |
|---|---|---|
| Cursor effort must be `model` | script (`validate_effort`, `validate_backend_efforts`) | none available |
| `auto` on cursor | script, at submit and setup | none available |
| Binary is `cursor-agent`, never the IDE | script (`resolve_worker_bin`, `cli_available`) | none available |
| Cursor posture narrower than `--force` | nothing; only the user's Cursor deny rules | a worker reads outside the repo, reaches the network, and commits | <!-- risk-ok: Cursor CLI flag name -->
| A base name resolved to an unchosen variant | wizard offers catalogue slugs only | a hand-written config runs whatever Cursor picks; `init` records it |

## Component changes

| File | Change |
|---|---|
| `scripts/delegate-codex.sh` | `cursor` in the backend guard; `resolve_worker_bin` case; `validate_effort` enum; `auto` refusal; `resolve_cursor_permission_mode`; `write_cursor_exec_line` and its branch in `write_run_script`; warning remedy line; cursor branches in `STATUS_SCAN_PY` and `cmd_result`; header comment and `usage()` |
| `scripts/handoff-config.py` | `BACKENDS` gains `cursor` |
| `scripts/handoff-setup.py` | `CURSOR_EFFORTS`; `cli_available` mapping; `auto` refusal; `validate_cursor_model`; smoke branch; terminal wizard backend list and effort fill |
| `scripts/handoff-setup-ui.py` | `_cursor_model_options`; backend label; locked effort field; stale-slug marking; subtitle |
| `docs/receipt-schema.json`, `scripts/make-receipt.py`, `scripts/validate-receipt.py` | v7 (decision 9) |
| `scripts/render-cost-receipt.py`, `assets/cost-receipt.html` | `SCHEMA_VERSION`; `BACKEND_FIELDS` and `USAGE_FIELDS` rows; cursor fold; explicit Claude branch; summary and denials sentences |
| `scripts/handoff-session-ui.py` | cursor `denials()` branch; explicit Copilot branch |
| `assets/transcript-viewer.html` | cursor branch in `HV.normalize`; `tool_call` and `thinking` in `HV.inferBackend`, checked before the Claude fallback |
| `SKILL.md`, `references/*.md`, `README.md`, `CLAUDE.md`, user guide, diagrams, `CHANGELOG.md`, `docs/releases/v3.9.0.md`, `examples/session-receipt.md`, `scripts/check-skill-repo.sh` (v6 message), `test-prompts.json` | four backends, v7, version 3.9.0, and the Cursor limits (R8.4) |
| `docs/specs/design_agent-handoff.md`, `docs/specs/design_agent-identities-and-config.md` | after implementation only (R8.3) |
| tests | R9.2; a `CursorWorktreeTests` subclass of `BackendLifecycle`; cursor fixtures for each parser |

Unchanged: `job_state` and its Python mirror in `handoff_runtime.py`, `render-transcript.py` (its payload already carries `meta.backend`), `showcase-cost-ledger.py`, `goal-sync.py`, `PRESETS`, and config `schema_version`.

## Risks

- **`default` equals `allow-all` on Cursor.** A cursor worker can read outside the repo, reach the network, and commit, bounded only by deny rules the user writes. This is looser than every other backend's `default`, and the bypass warning on every job is the reminder.
- **The sandbox finding rests on one machine.** It cannot be told whether `--force` bypasses Cursor's sandbox or the sandbox is broken here. If a later build runs shells under `--sandbox enabled`, a sandbox-based `default` becomes possible, as its own change with its own probe. <!-- risk-ok: Cursor CLI flag name -->
- **Cursor's allowlist approves things it was not told to.** Probe A's `git commit` ran without an allow rule. Irrelevant under `--force`, but it means the user's allowlist is not a complete description of what Cursor permits. <!-- risk-ok: Cursor CLI flag name -->
- **The catalogue moves with Cursor releases.** 246 inconsistently named entries, several per model family. A configured slug can disappear, and the first job then fails with Cursor's own stderr, which lists what is available.
- **Base names resolve silently.** Covered by setup offering only catalogue slugs, and by `init` recording the model that ran.
- **The mid-run API failure shape is unknown.** The monitor reads both `log.jsonl` and `stderr.log`, and implementation should provoke one failure to record its shape.
- **Commits carry `Co-authored-by: Cursor`** when the user's Cursor attribution setting is on, including e2e worker commits. That is the user's setting; Handoff reports it and does not strip it.
- **`(NO ZDR)` models.** Twenty Claude Fable slugs are marked as having no zero data retention. A delegated job sends repository contents, so setup docs name the marker.
- **Four event shapes across five parsers.** The branching cost the v3.7.0 plan predicted has arrived. It is contained for this release, and it is the reason for the first follow-up.
- **Fake-CLI tests prove wiring, not behaviour.** Real behaviour rests on the probe log (one build, one account, one config) and the live end-to-end run in R9.3.

## Follow-ups

- **One event-normalization module** for the four Python parsers, as its own release with no behaviour change and the existing per-backend tests as its safety net.
- **A sandbox-based cursor `default`**, if and when a Cursor build runs shells under `--sandbox enabled` on the platforms Handoff supports.
- **Hardening the bash catch-alls** in `delegate-codex.sh` so an unknown backend dies instead of defaulting to codex.
