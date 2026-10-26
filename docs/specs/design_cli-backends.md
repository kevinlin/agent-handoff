# Agent CLI backends

## Context

Handoff runs delegated work on five worker CLIs: `codex`, `claude`, `copilot`, `cursor`, and `antigravity`. A task row names an identity, and the identity's configured `backend` picks the CLI (`docs/specs/design_agent-identities-and-config.md`). This document is the design of record for what happens after that choice: how each CLI is found, which command line it gets, which permission posture it runs under, how a fix round resumes it, how setup lists its models and checks it, and where the five differ.

### The invariant: all five backends are the same job

A row on any backend gets the same jobId, job directory, monitor loop, bounded `resume` fix round, worktree lifecycle, and receipt evidence (`docs/specs/design_agent-handoff.md`). Where a CLI differs (binary name, effort, permission flags, session-id timing, event shape), the difference stays inside the scripts and never becomes a branch in the flow.

### Evidence

Each CLI's behaviour comes from live probes, recorded in `docs/research/`:

| CLI | Research doc | Probed on |
| --- | --- | --- |
| Copilot | `github-copilot-cli-specification.md`, `fetch-copilot-models.md` | GitHub Copilot CLI 1.0.83 |
| Cursor | `cursor-cli-specification.md` | build `2026.09.28-64d2043`, fifteen runs plus the live end-to-end run |
| Antigravity | `antigravity-cli-specification.md` | `agy` 1.3.5, 55 evidence records plus three driver runs and the live end-to-end run |
| all | `cross-agent-cli-permissin-mode.md` | the permission-posture proposal this design deviates from (*Deviations*) |

Probe ids cite those documents: single letters (A, G, H1) are Cursor's section 11, and names (P3, RSAME, MUNKNOWN) are Antigravity's section 11. No decision here rests on a vendor's docs where a probe was possible. On `agy` the probes overruled its own documentation three times. The docs say workspace writes are auto-allowed in headless mode, but P0 was denied. The docs give a five-minute print timeout, where help says unlimited. And `--mode plan` sounds read-only, but RO2 wrote files.

### Scope

Covered: binary discovery, effort and model, command lines, permission posture, sessions and resume, launch failures and the `status`/`result` output contract, model catalogues and CLI prompts in the setup wizard, smoke, and what adding a sixth backend touches.

| Out of scope | Where it lives |
| --- | --- |
| Identities, resolution, the config schema, the setup wizard's steps | `docs/specs/design_agent-identities-and-config.md` |
| The flow that plans, delegates, monitors, and reviews jobs | `docs/specs/design_agent-handoff.md` |
| Event parsing for transcripts, the session receipt, the cost receipt | `docs/specs/design_agent-handoff-evidence.md` |
| Denials on the session page | `docs/specs/design_session-visualisation.md` |
| The job primitive (`submit`/`status`/`result`/`resume`/`cancel`/`cleanup`) | `scripts/delegate-job.sh` header comment |

## The five backends at a glance

| | codex | claude | copilot | cursor | antigravity |
| --- | --- | --- | --- | --- | --- |
| Binary | `codex` | `claude` | `copilot` | `cursor-agent` | `agy` |
| Override | `HANDOFF_CODEX_BIN` | `HANDOFF_CLAUDE_BIN` | `HANDOFF_COPILOT_BIN` | `HANDOFF_CURSOR_BIN` | `HANDOFF_ANTIGRAVITY_BIN` |
| Identity check | none; the macOS app bundle comes before PATH | none | `--version` reads `GitHub Copilot CLI` | none; never `cursor` | `--help` lists `--conversation` and `--dangerously-skip-permissions` |
| Invocation | `codex exec --json` | `claude --print --output-format stream-json` | `copilot -p --output-format json` | `cursor-agent -p --output-format stream-json` | `agy -p --output-format stream-json` |
| Effort | `minimal…ultra` | `low…max` | `none…max` | `model` | `model` |
| Session id | `thread_id` in the log | `session_id` in the log | assigned at submit | `session_id`, from line one | `init`'s `conversation_id` |
| Fix round | `exec resume <id>` | `--resume <id>` | `--resume <id>` | `--resume <id>` | `--conversation <id>`, model and posture passed again |
| Environment | inherited | `ANTHROPIC_*` and `CLAUDE_CODE_*` stripped | inherited | inherited | inherited |
| Shipped in | from the start | 3.5.0 | 3.7.0 | 3.9.0, receipt v7 | 3.10.0, receipt v8 |

## Approach: one explicit branch per backend

Each backend is a branch in every place that branches on backend. Tables keyed by backend (`BACKENDS`, `BACKEND_EFFORTS`, `BACKEND_FIELDS`, `USAGE_FIELDS`, `BACKEND_LABELS`, `BACKEND_ICONS`, `PERMISSION_HINTS`) carry one row each, and the five event parsers each carry an explicit branch per backend:

1. `STATUS_SCAN_PY` in `delegate-job.sh`
2. `cmd_result` in `delegate-job.sh`
3. `fold_usage` in `render-cost-receipt.py`
4. `denials()` in `handoff-session-ui.py`
5. `HV.normalize` and `HV.inferBackend` in `assets/transcript-viewer.html`

The alternative, moving the four Python parsers onto one event-normalization module and adding a backend as one entry, was rejected for Cursor and again for Antigravity. Each time it would have refactored every working backend and added a new one in a single change, which `references/darwin-ratchet.md` rules out ("one dimension per change"), and the JavaScript viewer would still need its own branch. With five event shapes across five parsers the cost of branching now shows, so the module is the first follow-up and should land before a sixth backend.

### No log reaches another backend's parser

A backend's log is read only by its own branch, in both directions. The branch is chosen from `meta`'s `backend=` line, passed in as an input, and never sniffed from event shape. Copilot's terminal event is `type: "result"`, the same type name Claude and Cursor use with different payloads, and Cursor shares three type names with Claude's `stream-json`. Antigravity discriminates on a top-level `event` key where every other backend uses `type`, so its shape collides with none of them.

One exception is deliberate. Codex and Claude share parsing in `cmd_result` and `HV.normalize`, and `tests/test_transcript_viewer.mjs` pins that each existing fixture parses the same under either name.

Some generic catch-alls remain. None affects a named backend:

- the `*)` cases in `resolve_worker_bin` and `validate_effort`, and the final `else` in `write_run_script`, all fall to codex;
- `make-receipt.py` counts a job directory naming an unknown backend as codex;
- the meta-less `BACKEND:-codex` defaults in `delegate-job.sh` treat a job written before backend dispatch as codex, which it is by construction.

Hardening the first two is a follow-up.

### What adding a backend touches

The fall-through scans for Cursor (at v3.9.0) and Antigravity (at `1486e87`) found these places, where a backend nobody named lands in another backend's branch or crashes. A sixth backend needs an explicit branch in each:

- **`scripts/delegate-job.sh`:** the backend guard in `submit`; `resolve_worker_bin` (otherwise it reads `HANDOFF_CODEX_BIN`, then runs `command -v <backend>`); `validate_effort` (otherwise the codex enum); a `resolve_<backend>_permission_mode` function (otherwise `submit` dies with exit 127); the `warn_permission_bypass` whitelist and its remedy line (otherwise no warning); a `write_<backend>_exec_line` and its branch in `write_run_script` (otherwise a codex command line); `extract_session_id`'s keys; whether `resume` pre-writes the parent's session id; `STATUS_SCAN_PY` and `cmd_result`; the header comment and `usage()`.
- **`scripts/handoff-config.py`:** `BACKENDS`.
- **`scripts/handoff-setup.py`:** the effort tuple and `BACKEND_EFFORTS` (otherwise a `KeyError` in setup and the wizard); `cli_available`; `validate_backend_models`; the catalogue reader; a `validate_<backend>_model` for smoke and its branch in `smoke` (otherwise smoke passes after the dry-run alone); the terminal wizard's backend prompt and effort fill.
- **`scripts/handoff-setup-ui.py` and `assets/setup-ui.html`:** `model_options`, `model_discovery`, and the `clis` entry in `build_state` (the comprehension over `CLI_RELEASES` raises `KeyError` for a backend without a release entry); the `normalize_payload` catalogue guard; `BACKEND_LABELS`, `BACKEND_ICONS`, `PERMISSION_HINTS`, the `models()` source label, `syncPermissionWarning`, the effort-lock backend set, and a detection tile.
- **Receipts:** `make-receipt.py` (`BACKENDS`, a `--<backend>-jobs` flag, the emitted count pair, which `count_mismatches()` reads); `validate-receipt.py` (`REQUIRED_FIELDS`, `ROLE_HOSTS`, the count and duration tuples, the version gate); `assets/receipt-schema.json`; `render-cost-receipt.py` (`SCHEMA_VERSION`, `BACKEND_FIELDS`, `USAGE_FIELDS`, `fold_usage`, the cost cell, the summary card) and `assets/cost-receipt.html`; the PASS message in `scripts/check-skill-repo.sh`.
- **Readers:** `denials()` in `handoff-session-ui.py`; `HV.normalize` and `HV.inferBackend` in the transcript viewer.
- **Tests:** a `BackendLifecycle` subclass in `tests/test_delegate_role.py`, fake binaries in its `make_env()`, and an explicit branch in `assert_posture_argv`, whose final `self.fail` refuses any backend it does not name.

Unchanged by a new backend: `job_state` and its Python mirror in `handoff_runtime.py`, `render-transcript.py` (its payload already carries `meta.backend`), `showcase-cost-ledger.py`, `goal-sync.py`, `PRESETS`, and config `schema_version`.

## Binary discovery

`resolve_worker_bin` in `delegate-job.sh` and `cli_available` in `handoff-setup.py` apply the same rule, so setup reports a CLI as available exactly when a job would find it. The order is the `HANDOFF_<NAME>_BIN` override, then (codex on macOS only) the ChatGPT or Codex app bundle, so app-only models use a compatible client, then PATH. A fix round reuses the parent's recorded binary while that path is still executable and, on copilot and antigravity, still passes the identity check; otherwise it resolves again.

Three backends share a name with something else, and each is mapped explicitly:

- **copilot.** AWS Copilot CLI, an ECS deployment tool, is also `copilot`, and PATH order decides which one wins. The two differ only in `--version` output (`GitHub Copilot CLI 1.0.83.` against `copilot version: v1.34.1`). PATH discovery walks every match and takes the first that identifies as GitHub Copilot CLI, the override is checked the same way, and so is a fix round's recorded path.
- **cursor.** `cursor` on PATH is the Cursor IDE launcher; the agent is `cursor-agent`. `agent` is too generic and is never resolved. There is no identity check: `cursor-agent --version` prints a bare build string with nothing to match, and no other tool is known to ship a `cursor-agent`.
- **antigravity.** `antigravity` is the IDE launcher's name, so the CLI is looked up as `agy`. An earlier Homebrew cask linked the IDE launcher as `agy` too, and `agy --version` prints a bare `1.3.5` (VERSION). An IDE launcher handed `-p "<packet>"` from a background job would at best fail and at worst open an editor window. So a candidate passes only when `<candidate> --help`, stdout and stderr together, lists both `--conversation` and `--dangerously-skip-permissions`. The CLI prints its help on stderr (HELP). The call runs under a 10-second limit held by `python3`, because macOS ships no `timeout`, and a candidate that hangs or fails does not pass. Those are the two flags Handoff depends on, so the check tests the protocol rather than a brand string. `agy changelog` would carry a product string, but it prints the whole release history, too heavy for every submit. The check screens for compatibility; it does not prove what a real IDE launcher prints, which was not available to probe.

Each failure names what it found and the override that fixes it.

## Effort and model

### Effort enums are per CLI

| Backend | Accepted `effort` |
| --- | --- |
| codex | `minimal`, `low`, `medium`, `high`, `xhigh`, `max`, `ultra` |
| claude | `low`, `medium`, `high`, `xhigh`, `max` |
| copilot | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max` |
| cursor | `model` |
| antigravity | `model` |

`validate_effort` (submit) and `validate_backend_efforts` (setup) refuse anything else. Copilot's enum is the CLI's superset; which efforts a given Copilot model takes is narrower, and the wizard reads that per-model list from the entitlement catalogue (*Copilot model catalogue*). A pair written outside the wizard surfaces as the CLI's own error, never a silent downgrade.

### Cursor and Antigravity carry effort in the model id

`model` means "set by the model id". The identity stores the whole catalogue id as `model`, and the effort value never reaches the command line. A role-less cursor or antigravity job that names no `--effort` gets `model` rather than the codex-shaped default `high`.

- **Cursor has no effort flag.** Effort sits inside the slug (`claude-opus-5-5-high`) next to two other axes (`-thinking`, `-fast`). The spelling is irregular (`gpt-5.5-extra-high`), some slugs carry no effort (`composer-2.5`), and the bracket form the help advertises (`model[effort=low]`) was refused on both models tried (H1, H2).
- **`agy` has an `--effort` flag that cannot be a routing field.** The catalogue lists ids, some with the effort inside (`gemini-3.8-flash-low`) and some without (`claude-sonnet-4-6`), and no base names (MODELS). An id plus any `--effort` is refused before a session (MCONFLICT, MMAX). `claude-sonnet-4-6` refuses the flag outright (MCLAUDE) and runs fine without it (DRV_SONNET). The flag's enum is global while each model accepts its own subset: Flash has no `max` (MMAXBASE).

Rejected alternatives:

- **Derive effort from the slug and cross-check it.** Readable receipts, but it needs a parser for a naming scheme Cursor does not promise and already breaks.
- **Store base plus effort, and compose or pass both.** It cannot express `claude-opus-5-thinking-high-fast` on Cursor. On `agy` it works for Flash (MBASE), but the catalogue never shows base names, Claude models refuse the flag, and the CLI already validates each pair at launch, so Handoff would keep a per-model table it cannot discover.

The cost: these rows show effort `model` in `--status` and `roles_used`, while the id beside it names the effort.

### `auto` and empty models are refused

`model = "auto"` is refused at setup (`validate_backend_models`) and at submit on copilot, cursor, and antigravity. An identity is a deliberate triple, and `auto` hands the choice back to the vendor on every request, so the receipt would record what the vendor picked rather than what the repo configured. Antigravity also refuses an empty model: without `--model`, `agy` runs whatever model the CLI has selected at that moment, `init` does not name it (MDEFAULT, RMODEL), and a fix round has to pass the same model again (*Sessions and resume*). Each refusal names `handoff-config.py set` as the fix.

### Catalogue membership: at setup and smoke, not at submit

A model outside the catalogue can reach a config three ways. The web wizard offers only catalogue entries, and `normalize_payload` refuses any other copilot, cursor, or antigravity model. The terminal wizard and `--role-model` accept a typed model and check only `auto`. And a config can be written by hand. Smoke closes the last two with a free membership check before its paid run (*Smoke*). Submit does not re-read the catalogue: that would add a network call that can fail before every launch.

The gap matters most on Cursor, which quietly resolves a base name outside the catalogue (`gpt-5.4-mini`) to a variant ("GPT-5.4 Mini Low", H0). `init`'s display name records what actually ran, and the transcript and cost receipt show it.

No preset names a copilot, cursor, or antigravity identity; the presets use claude and codex only. Those three are chosen through custom mode or the web wizard.

## Command lines

`write_run_script` writes the command into `run.sh`. Every line reads the prompt from `prompt.md`, takes stdin from `/dev/null`, sends stdout to `log.jsonl` and stderr to `stderr.log`, and records `exit_code`. Stdin matters because a long prompt can make a worker CLI also wait on stdin for more input, and a background job's stdin is never closed on its own: without the redirect the job hangs with no further events. Config and log values (`MODEL`, `SESSION_ID`) are written with `%q`, so they stay data in the generated shell. `$CODEX_BIN` keeps its historical name on every backend.

| Backend | Fresh job | Fix round |
| --- | --- | --- |
| codex | `codex exec "$PROMPT" --json -C "$WORKDIR" -c 'model_reasoning_effort="<effort>"' -m "$MODEL"`, plus `--skip-git-repo-check` outside a git repo | `cd "$WORKDIR"`, then `codex exec resume <id> "$PROMPT" --json -c 'model_reasoning_effort="<effort>"'`: `exec resume` takes no `-C` or `-s`, so cwd comes from the shell and sandbox from `-c` |
| claude | strip `ANTHROPIC_*` and `CLAUDE_CODE_*`, `cd "$WORKDIR"`, then `claude --print --output-format stream-json --verbose --permission-prompts none --permission-mode <mode> --effort <effort> --model "$MODEL" -- "$PROMPT"` | the same with `--resume "$SESSION_ID"` in place of `--model` |
| copilot | `copilot -p "$PROMPT" -C "$WORKDIR" --output-format json --effort <effort> --no-ask-user --no-remote --no-remote-export --no-auto-update --usage-output-file "$JOB/usage.json" --log-dir "$JOB/copilot-logs" --session-id <uuid> --model "$MODEL"` | `cd "$WORKDIR"`, then the same flags with `--resume "$SESSION_ID"` in place of `-C`, `--session-id`, and `--model` |
| cursor | `cd "$WORKDIR"`, then `cursor-agent -p "$PROMPT" --output-format stream-json --trust --workspace "$WORKDIR" --model "$MODEL"` | `cd "$WORKDIR"`, then `--resume "$SESSION_ID"` in place of `--workspace` and `--model` |
| antigravity | `cd "$WORKDIR"`, then `agy -p "$PROMPT" --output-format stream-json --model "$MODEL" --print-timeout 0s` | the same plus `--conversation "$SESSION_ID"`; the model is passed again |

The posture flags (*Permission posture*) follow each line. A fix round always runs in the parent's working directory, the worktree when there is one, because `codex exec resume`, `claude --resume`, `copilot --resume`, `cursor-agent --resume`, and `agy --conversation` all take their cwd from the shell.

Per-backend reasons:

- **copilot** always carries `--no-remote --no-remote-export`: session export to GitHub web and mobile is on by default, and a delegated job's prompt and repository contents are not exportable material. Usage and Copilot's own logs land in the job directory, which `cleanup` keeps.
- **cursor** always carries `--trust`, because a fresh worktree is a workspace Cursor has never seen. Whether a run without it would prompt or fail was not probed.
- **antigravity** has no workspace flag: `agy` takes its working directory from the shell, and `init.cwd` records where it landed (P0, WORKTREE). A fresh worktree path needs no trust step (WORKTREE). Every line pins `--print-timeout 0s`. `agy`'s help and changelog give the default as unlimited, but its headless docs page says five minutes. A timed-out run exits 0 with status `SUCCESS` and partial output (TIMEOUTR), so an unpinned default could cut a long job short with only a stderr line to show for it. `0s` was probed (DRV_PT0). If the help is right, the flag changes nothing.

Flags that never appear, because Handoff owns the worktree protocol, the posture, or the evidence:

| Backend | Never passed |
| --- | --- |
| copilot | `--share`, `--share-gist`, `--worktree`, `--enable-memory`; `--allow-all-tools` together with `--mode plan` |
| cursor | `--worktree`, `--sandbox`, `--approve-mcps`, `--api-key`, `--stream-partial-output`, `--yolo`; the force flag together with `--mode plan` |
| antigravity | `--effort`, `--sandbox`, `--mode auto`, `--mode accept-edits`, `-c`/`--continue`, `-i`, `--project`, `--new-project`, `--add-dir`, `--agent`, `--remote-control`, `--input-format`, `--json-schema`, `--log-file`; `--dangerously-skip-permissions` together with `--mode plan` |

**Environment.** Only the claude branch strips `ANTHROPIC_*` and `CLAUDE_CODE_*` (`clean_claude_env()` in `scripts/handoff_runtime.py`, mirrored in bash in `run.sh`), because a nested Claude Code host injects provider URLs and credentials that would override the user's own CLI login. Any new script that spawns a `claude` uses it. The other branches deliberately do not: Copilot authenticates through `gh auth` and `~/.copilot`, Cursor through its own login or `CURSOR_API_KEY`, and `agy` through its own sign-in. None reads those variables, and on `agy` bogus `ANTHROPIC_API_KEY` and `CLAUDE_CODE_*` values had no effect (ENV).

## Permission posture

Each identity selects `permission_mode = "default"` or `"allow-all"` in config. `default` is the CLI's own normal, bounded posture. Nothing prompts, because a background job has no approval surface, so an unapproved action is denied. `allow-all` means *use the provider's native unrestricted mode*; it does not force all five CLIs into one security posture. On cursor and antigravity the two are the same.

| Backend | `default` | `allow-all` | `--read-only` |
| --- | --- | --- | --- |
| claude | `--permission-mode dontAsk --allowed-tools Read Glob Grep Edit Write Bash` | `--permission-mode bypassPermissions` | `--permission-mode plan`, without the allowlist |
| codex, fresh | no sandbox flag; the user's codex config decides | `--dangerously-bypass-approvals-and-sandbox` | `-s read-only` |
| codex, resume | no sandbox override | `--dangerously-bypass-approvals-and-sandbox` | `-c 'sandbox_mode="read-only"'` |
| copilot | `--allow-all-tools` | `--allow-all-tools --allow-all-paths --allow-all-urls` | `--mode plan`, with no allow-all flag |
| cursor | `--force` | `--force` | `--mode plan`, never the force flag | <!-- risk-ok: Cursor CLI flag name -->
| antigravity | `--dangerously-skip-permissions` | `--dangerously-skip-permissions` | `--mode plan`, never the skip flag |

Job `meta` records the effective mode as `permission_mode`: `dontAsk`, `bypassPermissions`, or `plan` on claude; empty, `allow-all`, or `read-only` on codex; `allow-all-tools`, `allow-all`, or `plan` on copilot; `force` or `plan` on cursor; `skip-permissions` or `plan` on antigravity.

### Precedence and recording

`--read-only` > `HANDOFF_CLAUDE_PERMISSION_MODE` (claude only) > config `permission_mode` > `default`. The legacy environment override is still validated before read-only overrides it.

Resolution materializes the default after merging, so a global `allow-all` plus a project identity that omits the field keeps `allow-all`. Like `model_source`, `permission_posture_source=config:<layer>` names the top participating layer, not per-field provenance. Role-less jobs use `default`. Resume inherits `permission_posture` and `read_only` from the parent, and a missing posture key means `default`, never `allow-all`: an old key does not prove unrestricted authority.

Job `meta` and dry-run output record the requested `permission_posture`, its `permission_posture_source` (`config:<layer>`, `explicit`, `env`, `read-only`, `parent`, or `default`), and the effective `permission_mode`. An override changes the source and the effective mode while the requested posture stays visible. Warnings use the effective mode. Posture evidence stays in `meta`, because the receipt's `roles_used` rejects additional properties. Changing posture does not clear `verified`: smoke checks a model-and-effort pair, not a posture.

### Why each `default` is what it is

The four non-Claude `default` postures share one property: checks run and nothing prompts. Each was picked by probing what a worker could actually do.

**claude.** The default set is enough to edit and verify: a `default` worker was measured editing a file and running its own check script. Under `dontAsk` a tool call that is not already approved is denied, where `bypassPermissions` runs it. That is the whole difference, and it is smaller than it looks. `deny` and `ask` rules in the user's own settings produce hard denials under both postures (measured), and on a machine whose settings already approve a tool, both postures reached it with zero denials, `WebFetch` included. How much `default` narrows therefore depends on the user's Claude Code settings. It is a permission-rule posture, never containment.

**codex.** No sandbox flag, so the user's codex config decides (*Deviations*, 1 and 2).

**copilot.** `--allow-all-tools`, which leaves Copilot's path and URL verification on; `allow-all` turns those off (*Deviations*, 3).

**cursor.** Of the candidates, one let a worker run its checks:

| Candidate for `default` | Probed result |
| --- | --- |
| No flag: the user's Cursor allowlist decides | `bash check.sh` and `curl` rejected (A). A worker cannot verify its work, which is the v3.5.1 failure. |
| `--sandbox enabled` | The shell could not start (`spawn /bin/zsh ENOENT`), and the worker stopped after that first attempt (B3). |
| `--force --sandbox enabled` | Confined nothing: the outside write landed and the network call returned 200 (B, B2). | <!-- risk-ok: Cursor CLI flag name -->
| `--force` | Checks run and nothing prompts (C). That explicit deny rules still apply comes from the CLI help ("unless explicitly denied"); the probed deny list was empty. | <!-- risk-ok: Cursor CLI flag name -->

`--force` is the Cursor analogue of Copilot's `--allow-all-tools`: everything is auto-approved except what the user's own rules deny. `allow-all` maps to the same flag, because force mode already is Cursor's native unrestricted mode (`--yolo`, "Run Everything"). `allow-all` never adds `--sandbox disabled`: the research doc forbids it, and on the probed machine it would change nothing observable. <!-- risk-ok: Cursor CLI flag name -->

**antigravity.** In print mode `agy` never prompts. Any action that needs approval is denied, the turn stops there, and the process exits 0 with status `SUCCESS` (P0, P1). Of the candidates, one let a worker run its checks:

| Candidate for `default` | Probed result |
| --- | --- |
| No flag (`request-review`) | The first edit was denied; nothing else ran (P0). |
| `--mode accept-edits` | The edit landed; `bash check.sh` was denied (P1). |
| `--mode auto` | Not available on the probed account. `agy` printed a warning and fell back to `request-review` (P2, P2R, P5). A posture that silently becomes P0 on some accounts cannot be a default. |
| `--sandbox --dangerously-skip-permissions` | The outside read and write were blocked, but so was the check's in-repo marker write, and `git commit` failed reading `~/.gitconfig`. The network call still returned 200 (P4, P4R, SCANON). |
| `--dangerously-skip-permissions` | Everything ran: edit, check, outside read, network, commit (P3). |

`allow-all` maps to the same flag, because skipping permissions already is `agy`'s unrestricted mode.

On both Cursor and Antigravity the posture field therefore changes nothing, and the docs and wizard say so.

### Read-only never pairs plan mode with an unrestricted flag

- **Copilot:** `--mode plan` with `--allow-all-tools` still won on disk, but the worker attempted only its read, emitted zero denial events, exited 0, and claimed two writes that never happened. Nothing in the stream marks that failure, so the exclusivity is the only defence.
- **Cursor:** `--mode plan` with the force flag showed no such failure (E): same result as plan alone (D). The rule guards against a failure nobody has observed. It costs nothing and keeps the read-only rule the same on every backend.
- **Antigravity:** `--mode plan --dangerously-skip-permissions` wrote `new.txt` and `shell.txt` (RO2, RO2R). Plan mode alone denied the write, and nothing changed on disk (RO1, RO1R). The live run then found that plan mode also refuses every shell command, `ls` included, and that the first refusal ends the turn. A read-only antigravity job can read files with its file tools and do nothing else: no `git diff`, no `grep`, no test. The flow prose tells the driver to inline what the job needs to see and to state check results instead of asking it to run them.

Antigravity's read-only has one stated limit. Plan mode refused writes through the same headless auto-deny that P0 hit, and a `permissions.allow` rule in the user's `agy` settings could in principle approve a write in plan mode. That was not probed, and Handoff writes no settings. So the flow prose tells the driver to check the tree after a read-only antigravity job. On a change, the driver rejects the output, saves it to `.handoff/rejected/<task-id>.patch`, restores the paths, and records the anomaly `read-only write`. Adding `--sandbox` would not close the gap: the sandbox let file-tool writes through (P4 edited `tracked.txt`) and blocked the `~/.gitconfig` read that `git` needs.

### The bypass warning

`warn_permission_bypass` prints a warning for every job whose backend and effective mode appear on its whitelist: `claude:bypassPermissions`, `copilot:allow-all-tools`, `copilot:allow-all`, `codex:allow-all`, `cursor:force`, and `antigravity:skip-permissions`. So every writing cursor or antigravity job warns, whatever its posture. The remedy line is per backend:

- cursor: add deny rules in `.cursor/cli.json`, or use `--read-only`;
- antigravity: Handoff can set no narrower posture, so use `--read-only` for a job that must not write;
- claude, when `HANDOFF_CLAUDE_PERMISSION_MODE` caused it: change or unset that override;
- otherwise: set the identity's `permission_mode=default`, or use `--read-only`.

### Handoff writes no CLI permission config

Handoff authors no writes to `.cursor/cli.json`, `~/.cursor/`, or a relocated `CURSOR_CONFIG_DIR`, and none under `~/.gemini/`, which covers `agy`'s `antigravity-cli/settings.json`, its `permissions.allow` rules, and its `trustedWorkspaces` list. Files a CLI writes there itself, such as Cursor's default `cli-config.json`, are not Handoff writes.

### Deviations from `docs/research/cross-agent-cli-permissin-mode.md`

That document is a proposal, and it has no Antigravity rows. Each place the shipped posture departs from it, measured against its rules:

1. **codex `default` passes no sandbox flag**, where the doc says `workspace-write`. Probed: ordinary file writes succeed but `.git/` writes are refused, and `sandbox_workspace_write.writable_roots` on the repo root does not lift it. Forcing it would break the worker-commits-on-its-worktree-branch contract for users who have not granted `.git` per repo, and would narrow a `danger-full-access` user whose e2e worker commits today. Inheriting the user's codex config keeps the documented contract.
2. **codex has no `--ask-for-approval` on `exec`** (it is a top-level flag only). `codex exec` never prompts, so the doc's `approval=never` half already holds.
3. **copilot `default` keeps `--allow-all-tools`**, where the doc lists `--allow-tool=write` plus stack-specific test, build, and lint rules. The doc permits test execution; the unsolved part is enumerating those commands per repo, which Handoff cannot know.
4. **cursor `default` is the force flag, not `--sandbox enabled` plus an allowlist.** On the probed machine the sandbox cannot start a shell, and the stock allowlist rejects the checks. The doc's own rule treats unapproved actions as failures and never escalates; applied here it gives a posture in which no worker can verify its work. <!-- risk-ok: Cursor CLI flag name -->
5. **antigravity `default` is the CLI's unrestricted mode**, for the same reason: that rule gives P0 or P1 on `agy`.
6. **`default`'s network-denied and workspace-only-writes goals are not met** on claude, cursor, or antigravity. No posture Handoff can set achieved them while still running checks. On `agy`, the only posture that confines anything, `--sandbox`, blocks the in-repo write and the commit a worker needs, and left the network open (P4).
7. **Kept:** cursor `allow-all` never adds `--sandbox disabled`.

### Stated limitations

- **No OS-level sandbox for claude `default`.** The research doc also asks for enforced sandboxing, workspace-only IO, and denied network. This changes the permission-rule layer only. The Darwin ratchet requires one dimension per change, and network denial interacts with installing and testing.
- **Codex denials are not counted.** The scanner counts Claude, Copilot, Cursor, and Antigravity denial shapes; Codex sandbox refusals match none of them, and result extraction discards command outcomes. The shared Claude-shaped test event proves nothing about Codex denial detection.
- **`permission_denied` is advisory, not enforced.** Job state derives DONE from the exit code, and `status` warns but still succeeds. Zero denials cannot prove the checks ran.
- **Cursor `default` equals `allow-all`.** A cursor worker can read outside the repo, reach the network, and commit, bounded only by deny rules the user writes.
- **Antigravity `default` equals `allow-all`,** and Handoff knows of no rule that narrows it. That is looser than Cursor, whose force mode respects deny rules according to its help. A run stopped by a denial still exits 0 with `SUCCESS`, so the denial count from `result.denied_actions` is the only signal.
- **Antigravity read-only rests on plan mode in print mode** (above).

## Sessions and resume

### Where the session id comes from

`extract_session_id` reads the log once and caches the id in the job's `session_id` file. A fix round needs that id: no id means `resume` refuses.

- **codex and claude:** `thread_id` or `session_id` in the event stream.
- **copilot:** Copilot emits `sessionId` only on its terminal event, so a job that died mid-run would have no id to resume from. `--session-id` sets the UUID of a new session, so `submit` generates one, writes it to `session_id` before launch, and records `session_id_source=assigned` in `meta`. A crashed copilot job then has the resume parity the others get for free. An assigned id is a claim on a session, not proof one exists: a job that failed before Copilot created a session cannot be resumed, and `copilot --resume` says so on stderr.
- **cursor:** `session_id` is on every event from line one, which is the Claude shape, so the existing extractor reads it. `cursor-agent create-chat` would allow Copilot-style assignment at submit (J), but it buys nothing here and adds a network call that can fail before launch.
- **antigravity:** `conversation_id` is a top-level field of `init`, line one of every created session, including one killed during its first tool call (KILL). The extractor reads only that field, and only on `init`: a pre-session error writes a `result` with an empty id and no `init` (MUNKNOWN), so the extractor finds nothing and `resume` refuses, as it should. No other backend emits `conversation_id`.

### What a fix round carries over

`resume` reads `backend`, `role`, `effort`, `worktree`, `permission_posture`, and `read_only` from the parent's `meta`, and the review-round caps apply on every backend (`docs/specs/design_agent-handoff.md`). On four backends the resumed session carries its model, so the round records `model=inherit` and passes none.

`agy` carries over neither model nor posture. `init.permission_mode` went from `always-proceed` back to `request-review` (RKILL), an accept-edits parent's child was denied a write (MODERESUME), and an unpinned resume reported the CLI's current selection rather than the parent's model (RMODEL, REFFORT). So an antigravity fix round passes `--model` and the posture flag again. The model comes from the parent's `meta`, and the round records the real id, never `inherit`, so a resume of a resume reads it from its own parent without walking the chain. A parent that recorded no model is refused.

### Requested and observed ids are kept apart on antigravity

Every other backend's `resume` writes the parent's id into the round's `session_id` cache before launch, and `extract_session_id` returns the cache before it reads the log. On `agy` that would be wrong twice. Given an id it cannot find, `agy` warns on stderr, exits 0, and starts a conversation under a new id (RUNKNOWN), so the round would report, and a further resume would use, a conversation that no longer exists. And a round whose model has left the catalogue fails before `init`, but the pre-written cache would still give it a session id.

So an antigravity resume does not pre-write the cache. Its `meta` records the id it asked for as `resumed_session=<id>`, and its session id is whatever its own log's `init` names. When the two differ, the round ran without its parent's context. `status` and `result` then print `context_lost: true (round ran without its parent's context; resubmit with the full packet)`, and `result --json` sets `context_lost: true`. The flow prose tells the driver to resubmit with the full packet rather than resume again. A further `resume` continues the round's own conversation, not the lost parent.

Cursor answers an unknown id differently, and undetectably: `--resume` with an id it has never seen exits 0 with empty stderr and starts a new, empty session under that same id (live run, research section 12). The ids match, so no check is possible. The flow prose tells the driver that a cursor round showing no memory of its parent's work is that case.

### Resume after a crash

- **cursor:** a session killed during its first tool call resumed with its context (G, G2).
- **antigravity:** a session killed after `init` resumed with its context (KILL, RKILL).
- On both, a kill between `init` and the first tool call was not probed. On cursor, if the session was never persisted, the behaviour above follows.
- A job killed before any event has no session id, and `resume` refuses it.

### A retired model on a fix round

`resume` takes no model override, and changing the config does not change a parent's `meta`. So an antigravity round whose model has left the catalogue since its parent ran ends `FAILED` with no session id, and `resume` never substitutes a model. The recovery is a fresh `submit` with the full packet, on the identity's current config, after `handoff-config.py set` names a model the catalogue still offers.

### Working directory on resume

Handoff always resumes from the parent's directory. On `agy` the probes left this open. RSAME resumed in the same directory and recalled its context, but ran no shell command. CWDRESUME resumed from a new directory, and its shell followed. RCWD2, resumed from a second new directory, ran its shell in the first. The live run closed Handoff's case: an `-r2` round ran `pwd`, printed the parent's directory, and recalled the parent's token. CWDRESUME and RCWD2 remain unexplained (*Risks*).

## Launch failures, mid-run failures, and what `status` and `result` print

### A model the CLI rejects at launch is a failed job

Handoff's refusals happen before a job directory exists. A CLI's model check happens when the worker starts, which is after `submit` has written the job directory and `meta`. So an unknown id, an id that has left the catalogue, or (on Cursor) the bracket form produces a job that ends `FAILED`, never a refusal. This follows from not checking the catalogue at submit. The monitor's anomaly rule already covers a `FAILED` job.

- **cursor:** stderr `Cannot use this model: …` with the available models, exit 1 (F1). `stderr.log` keeps the message verbatim, `log.jsonl` is empty, usage is unknown, and there is no session id.
- **antigravity:** exit 1 and one `result` event with status `ERROR`, an empty `conversation_id`, and the message and available models in `result.error` and on stderr (MUNKNOWN). The job keeps both verbatim. `result` shows the error; usage reads zero, as `agy` reported it; there is no session id.
- **copilot:** a wrong model or an effort that model does not support is refused at the CLI layer, on stderr, before any session.

### Mid-run failures

- **copilot:** an API failure can arrive with stderr empty and the detail carried only by a `session.error` event in `log.jsonl`, which is where `result --json` reads it from. An idle or API timeout after model events is not a login error.
- **cursor:** the shape is unknown; none was provoked. `result --json` carries an `errors` list on every backend, and on cursor it stays empty until a cursor error event is established. The monitor reads both `log.jsonl` and `stderr.log`.
- **antigravity:** a model-capacity failure exited 3 with status `ERROR`, `result.error`, and an `AGY_ERROR:` line on stderr (RO1). Authentication expiry and a personal-quota failure were not provoked.

### The output contract

The shapes below exist on every backend, so a consumer never branches on vendor. How each log is parsed is `docs/specs/design_agent-handoff-evidence.md`'s subject (*Five telemetry shapes*); this is what `delegate-job.sh` prints from it.

- **Agent message.** The last agent message of the invocation, never a field that joins them all: Cursor's last `assistant` event rather than `result.result`, and Antigravity's last `agent_response` step (its `text_delta` values joined) rather than `result.response`, which in P3 ran 1,236 characters against the final step's 949.
- **Commands.** Cursor's from `shellToolCall.args.command`; Antigravity's from the `CommandLine` of `ACTIVE` `run_command` steps. `agy` logs no exit code for a command (a deliberate `exit 7` ended `DONE` with no error, EXIT7), so `commands_run` lists them without a result, and a worker's report that a check passed is checked by re-running it.
- **Denials.** `status` reads the last event and the denial count in one parsed pass. A non-zero count prints `permission_denied: <n> (worker blocked; its self-report is not evidence)`. Antigravity reports denials only in the `denied_actions` list on its terminal `result`, so before that event the count is unknown, never zero. A running job prints no denial line, as a clean job does, because `status` is polled. A finished job with no `result` prints `permission_denied: unknown (no result event; agy reports denials only when a run ends)`, and `result --json` gives `null`.
- **Errors.** Objects with `status_code`, `error_type`, and `message`. Copilot's come from `session.error`. An antigravity `result` with status `ERROR` adds `{"status_code": null, "error_type": "result_error", "message": <result.error>}`. A stderr line containing `print timeout after` adds `error_type: "print_timeout"`, because a timed-out run can exit 0 with status `SUCCESS` and nothing else shows it. The pinned timeout should make that unreachable; if it fires, the driver treats the job as failed and resubmits.
- **`context_lost`.** Present on every backend: `true` or `false` on an antigravity resume, `null` wherever it is not measured.

## Setup: model catalogues, CLI tiles, and smoke

The wizard's steps and rules are in `docs/specs/design_agent-identities-and-config.md`. This section covers what each backend contributes to them.

### Where each model list comes from

Nothing is a hardcoded guess.

| Backend | Source, read when the page loads | Effort list |
| --- | --- | --- |
| codex | the account-aware `model/list` | per model, from the same call |
| claude | `claude --help`, plus the stable `fable`/`opus`/`sonnet`/`haiku` aliases, with `[1m]`/`1M` context variants normalized and deduplicated | the claude enum |
| copilot | the account's entitlement API (*Copilot model catalogue*) | per model, from the same call |
| cursor | `cursor-agent models`, about a second, free | `model`, locked |
| antigravity | `agy --output-format json models`, about two seconds, free | `model`, locked |

`cursor-agent models` prints text, one `slug - Display name` line per model, and a small line parser reads it, dropping `auto`. `agy`'s JSON form needs no line parser: `command.data.models[]` carries `id` and `label`. The output flag goes before the subcommand, because after it `agy` rejects it (MODELJSON, MJSONGLOBAL); the text form took four seconds.

Rules shared by every catalogue:

- **No guessed model.** When the CLI is missing, not signed in, times out, or prints something unexpected, the page offers no discovered model and names the fix: `cursor-agent login` or `HANDOFF_CURSOR_BIN`; `agy` sign-in or `HANDOFF_ANTIGRAVITY_BIN`; for Copilot, starting the wizard again, because the page holds the snapshot it was opened with.
- **A configured model is never silently replaced.** One missing from today's catalogue stays in the form, marked, so reopening the page offline cannot rewrite a working config. When discovery failed, the page still shows the failure and its fix beside the retained model. A retained Copilot model offers only the effort already configured, because nothing available knows what else it accepts.
- **The engine refuses a model the served list does not offer** (`normalize_payload`), on copilot, cursor, and antigravity, so the wizard's own API cannot write a pair nothing checked. `auto` is exempt from that refusal only so the engine's reasoned message is what the user reads; it is refused either way.
- **Effort is locked to `model`** for cursor and antigravity in both wizards. The code paths are keyed by a backend set, not by one name.

Opening the page starts no worker session. It runs `--version` on Codex, Copilot, and Cursor, `cursor-agent models`, and `agy --version`, `agy --help` (the identity check), and the `agy` catalogue command. `tests/test_handoff_setup_ui.py` pins those call lists: no Copilot session, exactly `--version` and `models` on Cursor, and exactly those three on `agy`.

### Copilot model catalogue

A `copilot` identity's model is chosen from a list, not typed.

**Source.** `gh auth token` for the bearer, then a read of the Copilot entitlement endpoint at `api.githubcopilot.com`. The Copilot CLI publishes no catalogue of its own: it has no model-list subcommand, and a wrong `--model` is refused without naming the alternatives. The entitlement API is the only list available without adding a Node dependency for the Copilot SDK.

**Filter.** An entry is offered when `model_picker_enabled` is exactly `true` and it carries either no `policy` or `policy.state = "enabled"`. The endpoint also returns embedding and legacy chat models that no coding session can use. The reader fails closed on anything it cannot parse: a field present in an unexpected shape drops that entry, because an unreadable constraint is not an absent one. A single bad entry costs that entry and nothing else; this runs during controller construction, and an exception escaping here would take the Claude and Codex lists down with it.

**Effort.** Each entry's `capabilities.supports.reasoning_effort` becomes that model's effort list, intersected with the CLI enum, so the effort control offers only what the chosen model takes. The served list is checked again before a preview is built. An entry reporting no reasoning efforts keeps the full CLI superset rather than becoming unconfigurable. An entry that reports efforts none of which Handoff can pass is dropped: reporting a constraint nothing satisfies is not the same as reporting none.

**What this replaced (3.8.1).** Apply used to run each configured Copilot pair past the real CLI and refuse to write a config the CLI rejected. The catalogue answers the same question without starting a session, so that gate is gone and `validate_copilot_pair` runs on the smoke path only.

**Limits.** The endpoint is not a documented GitHub API contract. Enterprise Cloud data-residency tenants serve their catalogue from a per-tenant host, which Handoff does not discover, and the wizard then offers no Copilot model. The token never reaches the served page state or a log line. A failed read is not fatal to the rest of the wizard.

### CLI tiles, install and update prompts (3.9.3)

Opening the wizard probes each worker CLI, reads the newest published release where a source exists, and says on the tile when one is missing or out of date.

**Probe.** Each CLI is found by the rule in *Binary discovery*, and each probe runs only `--version`, which starts no session and costs no premium request. A Copilot whose `--version` times out is reported missing.

**Latest release.** Codex and Copilot come from the npm registry: the `latest` tag of `@openai/codex` and `@github/copilot`. Cursor has no registry entry, so the page reads Cursor's install script, `https://cursor.com/install`, which names its build in a `downloads.cursor.com/lab/<build>/` URL; exactly one distinct build must appear. The requests carry no credentials, time out after 3 seconds, and run in parallel with the local probes. Claude and Antigravity have no `CLI_RELEASES` entry, because no free, probed source for their latest version exists; their tiles show the installed version alone. Antigravity joins `state["clis"]` directly, beside Claude, outside the comprehension that indexes `CLI_RELEASES`.

**Comparison.** Each side is reduced to its leading dotted number, and the two are compared numerically after the shorter is padded with zeros, so `codex-cli 0.147.0` is older than `0.162.1`. A prerelease counts as its stable number. Two Cursor builds from the same date with different hashes count as equal. The result is one status per CLI: `missing`, `outdated`, `current`, or `unknown`. `unknown` means the installed version or the newest release could not be read, and the page then makes no claim either way.

**What the tile says.** Codex says MISSING, UPDATE, or FOUND. Copilot and Cursor say MISSING, then NO MODELS when their model list failed, then UPDATE, else LISTED. Antigravity says MISSING, NO MODELS, or LISTED, and never UPDATE. FOUND and LISTED also show when the status is `unknown`, so neither word means up to date.

**The prompt.** A missing or outdated CLI gets a `How to install` or `How to update` button, including on a NO MODELS tile. The button opens a native HTML popover (click to open, Esc or Close to dismiss) holding the command, the detected binary path for an outdated CLI, a note when a `HANDOFF_*_BIN` override is set, "Then start the wizard again.", and an `Official install guide` link that opens in a new tab.

| CLI | Install | Update | Official page |
| --- | --- | --- | --- |
| Codex | `npm install -g @openai/codex` | `codex update` | https://developers.openai.com/codex/cli |
| GitHub Copilot | `npm install -g @github/copilot` | `copilot update` | https://docs.github.com/en/copilot/how-tos/set-up/install-copilot-cli |
| Cursor | `curl https://cursor.com/install -fsS \| bash` | `cursor-agent update` | https://cursor.com/docs/cli/installation |

**Limits.** The install line is one fixed command per CLI; other installers, such as Homebrew, are on the official page. A typed `copilot update` runs the first `copilot` on PATH, which may not be the binary the wizard found when AWS Copilot sits earlier, so the popover shows the detected path. A Codex bundled in the ChatGPT or Codex app updates with the app. Cursor's install script is not a stable API: if its format changes, Cursor reads `unknown`.

### How each backend appears in the wizard

Each backend has a label, an icon from a permissively licensed set credited in the page's attribution comment, and a per-row permission hint. The hints for Cursor and Antigravity say that the posture field changes nothing. The permission summary (`syncPermissionWarning`) names each identity under `allow-all`. It also names every cursor and antigravity identity, whatever its posture, in a sentence of its own. Without that, an identity on `default` would be missing from the summary while its jobs skip every permission check.

### Smoke

Smoke is the installation probe: it checks that a CLI answers on the configured model and effort. Only a pass writes `verified = true`, with a shared `verified_at`. A failure leaves `verified = false` and says so; apply stays applied.

| Backend | Checks | Pass needs |
| --- | --- | --- |
| codex | the delegate dry-run chain | exit 0 |
| claude | one fresh, tool-free, non-persistent session: `claude --print --no-session-persistence --no-chrome --permission-mode plan --tools "" --model <model> --effort <effort>` (plus `--agent handoff-<identity>` when that agent file exists), in a cleaned environment, 120-second limit | exit 0 and `HANDOFF_SMOKE_OK` in stdout |
| copilot | the dry-run chain, then `validate_copilot_pair`: one `copilot -p <sentinel prompt> --output-format json --model <model> --effort <effort> --mode plan --no-ask-user --no-remote --no-remote-export --no-auto-update`, 120-second limit | exit 0 |
| cursor | the dry-run chain; the slug's membership in `cursor-agent models`; then one `cursor-agent -p <sentinel prompt> --output-format stream-json --trust --model <slug> --mode ask` | exit 0 and `HANDOFF_SMOKE_OK` in the last `assistant` event's text |
| antigravity | the dry-run chain; the id's membership in the catalogue; then one `agy -p <sentinel prompt> --output-format stream-json --model <id>` with no posture flag | exit 0, `result.status` `SUCCESS`, and `HANDOFF_SMOKE_OK` in the last `agent_response` text |

The paid step's cost is lopsided on Copilot, Cursor, and Antigravity. A model or pair the account cannot use is refused before any session, at no cost. Smoke quotes the CLI's message verbatim. Copilot's names the exact pair to change (`Reasoning effort "max" is not supported for model …`), and Cursor's and `agy`'s list the available models. A Copilot rejection can arrive at two layers, and only the CLI layer writes stderr, so an API-layer refusal is read back out of the JSON stream's `session.error`. A usable pair costs one small request: one premium request on Copilot, about 16k input tokens on Cursor's `gpt-5.4-mini`, and about 12k on Antigravity's Flash or 4k on Sonnet (DRV_SMOKE, DRV_SONNET). When the catalogue cannot be read, smoke fails and names the fix.

Why the read-only flags differ: Cursor's `--mode ask` is read-only Q&A, and the spec does not claim the run is tool-free. `agy` takes no posture flag. In print mode its default denies every action that needs approval (P0), and the prompt asks only for a reply. `--mode plan` would add a planning instruction that a one-line smoke has no use for, and it was never probed on a reply-only prompt.

`validate_copilot_pair`, `validate_cursor_model`, and `validate_antigravity_model` sit side by side rather than behind one helper: the command lines and the parsing differ.

`/agent-handoff tryout` then runs one small real task per identity. Smoke proves the CLI answers; tryout proves an identity can complete its intended work.

## Evidence on each backend

Receipts, transcripts, and the cost receipt are `docs/specs/design_agent-handoff-evidence.md`'s subject. Decisions taken there for these backends:

- **One flat count pair per backend.** `copilot_jobs`, `cursor_jobs` (receipt v7), and `antigravity_jobs` (receipt v8) joined `codex_jobs` and `cc_jobs`, each with its `*_job_durations`, following the flat-field decision from v3.7.0. `make-receipt.py` splits jobs by the `backend=` line in `meta`, and a job on one backend is never folded into another's count. In `roles_used`, `host` is the CLI that executed the role.
- **Tokens, never an invented cost.** Cursor reports four token counters and Antigravity five, and neither reports a cost of any kind. Their cost cells read "n/a - no cost figure", like codex and copilot rows read "n/a", never "unknown", which would mean a reading is missing. Showing a dollar amount would be made up. The outside-driver figure counts codex, cursor, and antigravity jobs a second time, and its caveat says so.
- **Usage is per invocation.** Cursor's last `result.usage` is per invocation. `agy`'s `result.usage` and `result.duration_seconds` are cumulative over the whole conversation (RSAME: 23,989 input tokens, the parent's 11,846 plus the round's 12,143), so the reader sums the `usage` on the log's own `step_update` events. A parent and its fix round then add up on both. A job with no `result` event has unknown usage, never zero.
- **The model column.** Cursor's shows the `system/init` display name when the log has one, else the slug from `meta` (a job rejected at launch or killed before `init`). Antigravity's shows the `meta` slug, because `init.model` echoes the selector Handoff passed (MBASE) and `agy` emits no display name.

## Where it fails closed, and where it doesn't

These rows also appear in the flow-wide table in `docs/specs/design_agent-handoff.md`.

| Guard | Enforced by | Failure mode when skipped |
| --- | --- | --- |
| Effort in each backend's enum; `model` only on cursor and antigravity | script (`validate_effort`, `validate_backend_efforts`) | none available |
| `auto` on copilot, cursor, antigravity; an empty model on antigravity | script, at submit and setup | none available |
| Binary is GitHub Copilot CLI, `cursor-agent`, or a real `agy`, never AWS Copilot or an IDE launcher | script (`resolve_worker_bin`, `cli_available`, resume's re-check) | none available |
| Read-only never pairs plan mode with an unrestricted flag | script (the posture resolvers and exec lines) | none available |
| Every antigravity job pins `--print-timeout 0s` | script (`write_run_script`) | none available |
| A copilot model and effort pair | catalogue (the wizard refuses a model its served list lacks, and an effort that model does not accept) | a hand-written or `--role-model` config reaches its first job unchecked; smoke and tryout surface it |
| A cursor base name resolved to an unchosen variant | wizard offers catalogue slugs only; smoke checks membership | a config that skipped smoke runs whatever Cursor picks; `init` records it |
| Cursor posture narrower than force mode | nothing; only the user's Cursor deny rules | a worker reads outside the repo, reaches the network, and commits |
| Antigravity posture narrower than skip-permissions | nothing | the same |
| A plan-mode antigravity job's writes | `agy`'s headless auto-deny | a user allow rule might approve one; the driver checks the tree and, on a change, rejects the output, saves it to `.handoff/rejected/<task-id>.patch`, restores the paths, and records `read-only write` |
| An antigravity fix round that lost its session | `status` and `result` compare `init` with `resumed_session` | warns; the driver resubmits |
| A cursor fix round that lost its session | nothing; the ids match | the round runs without its parent's context; the driver spots it from the output |
| A run cut short by a print timeout | the pinned `0s`, then the `print_timeout` error | output is partial; the driver treats the job as failed and resubmits |

## Probe coverage for Cursor and Antigravity

The two newest backends were mapped onto each abstraction Handoff needs before any code was written. Status as shipped:

| Abstraction | Cursor | Antigravity |
| --- | --- | --- |
| Non-interactive submit | `-p`: probed | `-p`, stdin `/dev/null`: probed |
| Event stream | `stream-json`, `type` discriminator: probed | `stream-json`, `event` discriminator: probed |
| Session id | `session_id` from `system/init`: probed | `init`'s `conversation_id`, line one: probed |
| Fix round on the same session | `--resume`, cwd from the shell: probed, context intact | `--conversation` with model and posture again: probed, context intact |
| Resume after a crash | kill during the first tool call: probed; a kill between `init` and the first tool call is open | SIGKILL during the first tool call: probed; the same gap is open |
| Resume of an id the CLI never saw | exit 0, new empty session under the same id, no error: probed in the live run | warning, exit 0, new id: probed, and detected |
| Working directory | `cd` plus `--workspace`; `cd` on resume: probed | the shell's, recorded in `init.cwd`: probed fresh; a same-directory resume confirmed in the live run; RCWD2 unexplained |
| Effort as a routing field | none; the slug carries it | none usable; the id carries it |
| Read-only job | `--mode plan`: no writes, honest report | `--mode plan` alone: write denied, tree clean; every shell command refused (live run) |
| Default posture that can run checks | force mode | `--dangerously-skip-permissions` |
| Denial detection | typed `tool_call` `result.rejected` | `result.denied_actions` |
| Final agent message | last `assistant` event | last `agent_response` step |
| Commands run | `shellToolCall.args.command` | `run_command` `CommandLine`, no exit code |
| Token counters | `result.usage`, per invocation | sum of step `usage`, per invocation |
| Reasoning count | none | `thinking_tokens` |
| Cost figure | none | none |
| Model catalogue | `cursor-agent models`, free, text | `agy --output-format json models`, free |
| Model check | stderr refusal, exit 1, at launch | `result` `ERROR` with empty id, exit 1, at launch |
| Binary discovery | `cursor-agent`; `cursor` is the IDE | `agy`, `--help` flags as identity; the IDE launcher's real help is open |
| Print timeout | n/a | `--print-timeout 0s` |
| Mid-run API failure | **open** | model capacity: exit 3, `result` `ERROR`, `AGY_ERROR:`; auth expiry and quota open |

## Version skew

An engine older than the release that added a backend meets that backend's job directories as follows:

| Backend | Older engine | Config | Job directory |
| --- | --- | --- | --- |
| copilot | pre-3.7 | refused by `validate_config` | `make-receipt.py` counts the job as codex |
| cursor | pre-3.9 | refused | counted as codex, and `result` reads the log through the generic parser |
| antigravity | pre-3.10 | refused | counted as codex |

This is accepted because the scripts and the flow prose ship together in one skill directory. The config side of the skew is in `docs/specs/design_agent-identities-and-config.md`, *Version skew*.

## Verification

**Fake-CLI tests prove wiring, not behaviour.** `tests/test_delegate_role.py` runs the shared `BackendLifecycle` suite (submit, posture, worktree pinning, fix rounds inheriting the tree, `cleanup` refusing a dirty tree) against a subclass per backend, with fake binaries from `make_env()`. Each backend adds:

- its posture flags per posture, and their absence under read-only;
- the flags that must never appear, and on antigravity `--print-timeout 0s` on every line;
- the name trap: AWS Copilot ahead of GitHub Copilot on PATH, an IDE `cursor` ahead of `cursor-agent`, an IDE-launcher `agy` ahead of the real one;
- the launch-time model rejection, a killed-job fixture, and on antigravity the lost-session resume, each in text and `--json` output;
- the denial count, including an antigravity denial whose tool step ended `DONE` (P0's shape) and two actions in one `result`, plus the no-fall-through checks;
- agent-message selection, and per-invocation usage on a resume fixture whose `result.usage` is cumulative.

**A live end-to-end run on the real CLI** comes with each new backend. It runs in a scratch repo through an installed copy of the skill, and records the helper paths and the installed revision, which `install.sh` compares with the source. It confirms:

- one expected edit on disk;
- one named repo check that actually ran, with its output in `log.jsonl`;
- `meta` naming the backend;
- a `resume` on the same session, with its usage counted once (on antigravity, one that runs `pwd`);
- a `--worktree` job that pins its base and cleans up;
- a `--read-only` job, asked to write a file, that leaves the tree unchanged;
- a receipt and a cost receipt that count the job.

The results are in each research doc's section 12. Both runs found things the probes and fakes had missed. On Cursor: the silent resume of an unknown id, a mistyped `workingDirectory` surfacing as `spawn /bin/zsh ENOENT`, `askQuestionToolCall` counted as a denial under plan mode, and the commit trailer on worktree commits. On `agy`: plan mode refuses every shell command.

## Not doing

- Writing Handoff-owned permission rules into any CLI's settings: `.cursor/cli.json`, `CURSOR_CONFIG_DIR`, `agy`'s `permissions.allow` or `trustedWorkspaces`.
- `--sandbox` in either direction on Cursor; `--sandbox`, `--mode auto`, or `--mode accept-edits` as an Antigravity posture. Each needs its own probe first.
- `create-chat` pre-assignment and the bracket effort form on Cursor; the `--effort` flag or composing a base name with an effort on `agy`.
- A catalogue check at submit, and a `--version` identity check for Cursor.
- A Copilot, Cursor, or Antigravity preset.
- A cost figure for any backend that reports none.
- Stripping Cursor's `Co-authored-by` trailer. It is the user's setting; Handoff reports it.
- A latest-version lookup and update prompt for `agy`, until a free source exists.

## Open questions

Cursor:

- the shape of a mid-run API failure;
- a `-p` run while logged out (only the `models` error is probed);
- running without `--trust`;
- a kill between `init` and the first tool call.

Antigravity:

- authentication expiry and a personal-quota failure mid-run;
- whether a `permissions.allow` rule lets a plan-mode job write;
- whether `agy` has deny rules, and whether they apply under `--dangerously-skip-permissions`. If they do, the bypass warning's remedy can name them;
- `--mode auto` on an entitled account;
- the cause of CWDRESUME and RCWD2;
- the IDE launcher's real `--help` output;
- a `-p` run while signed out, and whether the catalogue needs a sign-in;
- a kill between `init` and the first tool call.

## Risks

- **`default` equals `allow-all` on Cursor and Antigravity.** Both workers can read outside the repo, reach the network, and commit. Cursor is bounded by deny rules the user writes; Antigravity by nothing Handoff knows of. This is looser than every other backend's `default`, and the bypass warning on every writing job is the reminder.
- **Exit 0 and `SUCCESS` mean little on Antigravity.** A denial-stopped turn (P0) and a timed-out turn (TIMEOUTR) both exit 0 with `SUCCESS`, and commands carry no exit code. The denial count, the pinned timeout, and the full review gate carry the weight. That is the existing contract, but `agy` leans on it harder than the others.
- **A cursor fix round can lose its session silently.** Nothing in the logs distinguishes it; the driver has to notice from the output.
- **A rejected model fails as a job, after the job directory exists.** The user learns about it from the monitor's anomaly path, not from `submit`. Catalogues move: Cursor's had 246 inconsistently named entries, several per model family; Antigravity's held six account-dependent ids. A configured model can disappear between runs.
- **Base names resolve silently on Cursor.** The web wizard refuses them, smoke checks membership for every other path, and `init` records the model that ran. A config that skips smoke reaches its first job unchecked, as a hand-written Copilot config does.
- **The Cursor sandbox finding rests on one machine.** It cannot be told whether force mode bypasses Cursor's sandbox or the sandbox is broken there. If a later build runs shells under `--sandbox enabled`, a sandbox-based `default` becomes possible, as its own change with its own probe.
- **Cursor's allowlist approves things it was not told to.** Probe A's `git commit` ran without an allow rule. Irrelevant under force mode, but the user's allowlist is not a complete description of what Cursor permits.
- **Commits carry `Co-authored-by: Cursor <cursoragent@cursor.com>`** when the user's Cursor attribution setting is on, including e2e worker commits on worktree branches (live run). No attribution trailer was observed on `agy` commits.
- **Cursor `(NO ZDR)` models.** Twenty Claude Fable slugs are marked as having no zero data retention. A delegated job sends repository contents, so the flow prose names the marker.
- **The Antigravity identity check rests on two flag names.** A later `agy` could rename one, and every submit would then refuse the real CLI, naming what it found. That fails closed.
- **`--mode auto` varies by account on Antigravity.** A later posture other than skip-permissions would behave differently per account, and the fallback is a stderr warning.
- **A stale working directory after a two-directory resume chain on Antigravity** (RCWD2). Handoff never resumes from a different directory, but the cause is unknown.
- **The Copilot model catalogue is an undocumented endpoint**, and data-residency tenants get no Copilot model in the wizard.
- **Five event shapes across five parsers.** Contained for now, and the reason the normalization module is the first follow-up.
- **Real behaviour rests on one build, one account, one machine per CLI**, plus one live run each.

## Follow-ups

- **One event-normalization module** for the four Python parsers, as its own release with no behaviour change and the per-backend tests as its safety net, before any sixth backend.
- **Harden the catch-alls** that count an unknown backend as codex, and the bash `*)` cases, so an unknown backend dies instead of borrowing another's branch.
- **A sandbox-based cursor `default`**, if a Cursor build runs shells under `--sandbox enabled` on the platforms Handoff supports.
- **An `--mode auto` antigravity `default`** for accounts that have it, once probed on one, with a guard for the fallback warning.
- **A latest-version source for `agy`**, and with it a `CLI_RELEASES` entry.

## History

| Version | Change | Plan |
| --- | --- | --- |
| 3.5.0 | `claude` as a worker backend, dispatched inside `delegate-codex.sh` | `plan_support-cc-to-cc-handoff.md` |
| 3.7.0 | `copilot`, session id assigned at submit | `plan_support-copilot-cli-backend.md` |
| 3.7.1 | per-identity `permission_mode` | `plan_per-identity-permission-mode.md` |
| 3.8.1 | Copilot model catalogue from the entitlement API | — |
| 3.9.0 | `cursor`, receipt v7 | `plan_cursor-cli-backend.md` |
| 3.9.3 | CLI install and update prompts | — |
| 3.10.0 | `antigravity`, receipt v8 | `plan_antigravity-cli-backend.md` |

This document consolidates, as of 2026-10-15, what were separate requirements and design docs for Cursor and Antigravity plus the permission-posture, Copilot-catalogue, Antigravity-catalogue, and CLI-prompt sections of `design_agent-identities-and-config.md`. The two plans cite requirement ids (R1.1 …) and decision numbers from the originals, which remain in git history: `git show bcd54de:docs/specs/requirements_cursor-cli-backend.md`, and likewise `design_cursor-cli-backend.md`, `requirements_antigravity-cli-backend.md`, and `design_antigravity-cli-backend.md`.

Both backend designs were reviewed once by `deep_reasoner`, read-only, before implementation: Cursor's as job `job-2026-09-30T00-22-19-47227-spec-review` (nine findings, four blocking), Antigravity's as `job-2026-10-14T22-15-34-55254-spec-review` (seven, two blocking). Each packet listed the settled decisions, no finding reopened one, and every finding was accepted. The blocking ones shaped this design:

- receipt generation is more than a table row;
- the cost receipt's model column and cost cells needed their own branches;
- a launch-time rejection is a failed job, not a refusal;
- the no-fall-through rule covers the new backend in both directions;
- on Antigravity, requested and observed session ids are kept apart, and the output contract gained an unknown denial count and error objects.
