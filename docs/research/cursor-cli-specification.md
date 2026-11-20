# Cursor CLI Integration Specification

## Status and provenance

Written 2026-09-30 from live probing of **Cursor CLI build `2026.09.28-64d2043`** (`cursor-agent`) on macOS (Darwin 25.6.0, arm64), one account logged in through `cursor-agent login`. The probes ran on 2026-09-29 between 15:07 and 15:24 UTC. They made fifteen `-p` runs against a throwaway git repo, plus free calls to `models`, `status`, `about`, and `create-chat`.

The probes did not change the user's global `~/.cursor/cli-config.json`, and several results depend on it:

| Key | Value |
|---|---|
| `approvalMode` | `allowlist` |
| `permissions.allow` | `["Shell(ls)"]` |
| `permissions.deny` | `[]` |
| `sandbox.mode` | `disabled` |
| `sandbox.networkAccess` | `user_config_with_defaults` |
| `attribution.attributeCommitsToAgent` | `true` |

Every claim carries its provenance:

- **[probed]** observed by running the CLI here, with the evidence quoted.
- **[help]** read from `cursor-agent --help` or `cursor-agent models` on this build.
- **[docs]** read from cursor.com/docs/cli through Context7 on 2026-09-29.
- **[open]** not verified.

The consumer is the `agent-handoff` skill, which wants Cursor as a fourth delegation backend beside `codex`, `claude`, and `copilot`. Sections 1 to 9 are facts about the CLI. Section 10 is inference and says so. Section 11 is the probe log.

---

## 1. Binary and installation

**[probed]** The install is versioned, with two symlinks:

```
~/.local/bin/cursor-agent -> ~/.local/share/cursor-agent/versions/2026.09.28-64d2043/cursor-agent
~/.local/bin/agent        -> ~/.local/share/cursor-agent/versions/2026.09.28-64d2043/cursor-agent
```

**[probed] `cursor` is a different program.** `/usr/local/bin/cursor` is the Cursor IDE launcher. A PATH search for the backend name `cursor` finds the IDE, not the agent. `agent` is too generic a name to resolve by.

**[probed]** `cursor-agent --version` prints `2026.09.28-64d2043` and nothing else: no product name to match on. Nothing else on this machine is called `cursor-agent`.

**[probed]** `cursor-agent about` prints CLI version, latest version, subscription tier, OS, shell, and the account email. `cursor-agent status` prints `✓ Logged in as <email>` or `Not logged in`.

## 2. Non-interactive invocation

**[help]** `-p, --print` is the script mode and "has access to all tools, including write and shell". `--output-format` takes `text | json | stream-json`. `--stream-partial-output` adds text deltas to `stream-json`.

**[probed]** Every run used this shape, with stdin from `/dev/null`, from inside the repo:

```
cursor-agent -p "<prompt>" --output-format stream-json --trust [--workspace <repo>] [--model <slug>] [flags]
```

No run waited on stdin. `--trust` was always passed; a run without it was not probed **[open]**.

**[help]** `--workspace <path-or-name>` defaults to the current directory. **[probed]** Fresh runs passed both a `cd` and `--workspace`; resume runs passed only the `cd`, and ran in the right directory.

**[help]** Flags present that a delegation backend has no use for: `--worktree`, `--worktree-base`, `--skip-worktree-setup`, `--add-dir`, `--plugin-dir`, `--approve-mcps`, `--api-key`, `-H/--header`, `-e/--endpoint`, `--auto-review`, `--continue`, and the `persist`, `worker`, `mcp`, `plugin`, and `bedrock` subcommands.

**[probed]** Across all runs, including one killed with SIGKILL, every line of stdout parsed as JSON.

## 3. The stream-json event stream

### 3.1 Vocabulary

**[probed]** Eight `type/subtype` pairs appeared. Probe A, a five-step task, produced:

```
system/init 1, user 1, assistant 8, tool_call/started 8, tool_call/completed 8,
thinking/delta 333, thinking/completed 4, result/success 1
```

`system/init`, `assistant`, `user`, and `result` share their type names with Claude Code's `stream-json`. `thinking` and `tool_call` are unique to Cursor.

### 3.2 `system/init`

**[probed]** Always the first line:

```json
{"type":"system","subtype":"init","apiKeySource":"login","cwd":"<repo>","session_id":"58e01ac4-1c71-4ab7-8d18-d46ccfc6274e","model":"GPT-5.4 Mini Low","permissionMode":"default"}
```

`model` is the **display name of the model that ran**, not the slug that was passed. `permissionMode` read `default` in every run, including runs with `--force`, `--mode plan`, and `--sandbox enabled`, so it does not reflect the flags. <!-- risk-ok: Cursor CLI flag name -->

### 3.3 `assistant` and `user`

**[probed]** Same shape as Claude Code's:

```json
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"probe-edit"}]},"session_id":"8ef50e00-3dfc-4056-90c9-6694bd73001f"}
```

A multi-step run emits one `assistant` event per message: progress narration between tool calls, then the final report.

### 3.4 `thinking`

**[probed]** Streamed reasoning, 182 to 337 events in a small job:

```json
{"type":"thinking","subtype":"delta","text":"**Addressing a typo issue**\n\nI","session_id":"58e01ac4-…","timestamp_ms":1790694467824}
{"type":"thinking","subtype":"completed","session_id":"58e01ac4-…","timestamp_ms":1790694469198}
```

`completed` carries no text. The deltas arrive without `--stream-partial-output`.

### 3.5 `tool_call`

**[probed]** Tool kinds observed: `readToolCall`, `editToolCall`, `shellToolCall`, `globToolCall`, `grepToolCall`, `getMcpToolsToolCall`, `awaitToolCall`. The kind is the single key under `tool_call`. `started` carries `args`. `completed` carries a `result` with exactly one key: `success`, `rejected`, or `spawnError`.

Started and completed pair on `call_id`, which equals the `toolCallId` inside. **These ids contain an embedded newline**, escaped in the JSON: `"call_XRkT9hgyCIUUr6v3o21ME5Um\nfc_00862ac6…"`.

A shell `success` result carries `command`, `workingDirectory`, `exitCode`, `signal`, `stdout`, `stderr`, and `executionTime`. Shell `args` include Cursor's own parse of the command line (`simpleCommands`, `parsingResult`).

### 3.6 Denials are typed

**[probed]** A refused shell command:

```json
{"type":"tool_call","subtype":"completed","call_id":"call_XRkT…\nfc_0086…","tool_call":{"shellToolCall":{"result":{"rejected":{"command":"bash check.sh","workingDirectory":"<repo>","reason":"","isReadonly":false}}},…},"session_id":"58e01ac4-…"}
```

`reason` was empty in every rejection. The job still exited 0. The worker's final message did report each refusal: "`bash check.sh` was refused by the shell tool."

### 3.7 `spawnError`

**[probed]** A shell that could not start:

```json
{"tool_call":{"shellToolCall":{"result":{"spawnError":{"command":"","workingDirectory":"","error":"spawn /bin/zsh ENOENT"}}}}}
```

It occurred in two places. Probe B3 made one shell call under `--sandbox enabled` without `--force`, and the worker stopped after it failed. Probe A hit it on `cat ../outside.txt`. This is a tool failure, not a permission denial. The inference, not verified, is that Cursor ran the read-only `cat` inside its sandbox, which cannot start a shell on this machine (section 6). <!-- risk-ok: Cursor CLI flag name -->

### 3.8 `result`

**[probed]**

```json
{"type":"result","subtype":"success","duration_ms":3317,"duration_api_ms":3317,"is_error":false,"result":"probe-edit","session_id":"8ef50e00-…","request_id":"fb887c56-…","usage":{"inputTokens":130,"outputTokens":17,"cacheReadTokens":17408,"cacheWriteTokens":0}}
```

- **`result` is every assistant message concatenated**, not the final one. Probe A's begins with the first narration line, "I'll follow the five steps in order…", and ends with the final report. Claude Code's `result` holds the final message only.
- `usage` has four camelCase token counters and no reasoning count.
- There is no cost field of any kind.
- `is_error` was `false` in every run. The shape of a failed `result` was not observed **[open]**.

### 3.9 Errors

**[probed] Model refusal is free and happens before any session.** An unknown slug, or the bracket form (section 5), produces no events, exit 1, and one stderr line followed by the whole catalogue:

```
Cannot use this model: no-such-model-xyz. Available models: auto, gpt-5.3-codex-low, …
```

**[probed]** Not logged in, `cursor-agent models` fails with `Error: Authentication required. Run 'agent login', pass --api-key/--auth-token, or set CURSOR_API_KEY/CURSOR_AUTH_TOKEN.`

**[probed]** A run killed with SIGKILL (exit 137) leaves a log ending on a complete `tool_call/started` line and no `result` event.

**[open]** An API failure after the session exists (quota, rate limit, network) was not provoked. Whether it arrives as an event, a failed `result`, stderr, or all three is unknown.

## 4. Session id and resume

**[probed]** `session_id` is on every event, starting with `system/init` on line one. A job that dies after init has its id in the log.

**[probed] Resume keeps context.** `cursor-agent -p "<question>" --output-format stream-json --trust --resume <id>`, run from the repo with no `--model` and no `--workspace`, answered from the parent's context (probe F returned `probe-edit`). The `init` event showed the same model and session id. Section 13 corrects the model half: a resume with no `--model` runs the CLI's saved selection, which here was the parent's own model.

**[probed] A session killed mid-run resumes.** Probe G was killed while its first tool call was running (exit 137, no `result`). Resuming its id recovered `KILL-TOKEN-4417` from the killed turn.

**[probed]** `cursor-agent create-chat` prints a UUID. `-p --resume <that uuid>` as the first turn ran with it as the `session_id` (probe J). So an id can be assigned before launch, but section 4's first finding makes that unnecessary.

**[probed] Usage is per invocation.** The resumed turn in F reported `inputTokens: 130` against its parent's 18350.

**[open]** Resuming an id whose job died before `init`, resuming an id that never existed, and resuming a job killed between `init` and its first tool call. Probe G was killed during its first tool call, so it does not settle the last case.

## 5. Models and effort

**[probed]** `cursor-agent models` (also `--list-models`) is free, needs a login, and prints text only, one `slug - Display name` per line. It listed 246 entries including `auto`. **[help]** It has no JSON option.

**[probed] Effort lives in the slug**, alongside two other axes:

| Slug | Display name |
|---|---|
| `claude-opus-5-5-high` | Claude Opus 5.5 1M High |
| `claude-opus-5-thinking-max-fast` | Claude Opus 5 1M Max Thinking Fast |
| `gpt-5.6-sol-xhigh-fast` | GPT-5.6 Sol 1M Extra High Fast |
| `gpt-5.5-extra-high` | GPT-5.5 1M Extra High |
| `gpt-5.6-sol-medium` | GPT-5.6 Sol 1M |
| `claude-opus-4-7-xhigh` | Claude Opus 4.7 1M |
| `composer-2.5` | Composer 2.5 |
| `kimi-k3-max` | Kimi K3 |

The spelling is irregular (`extra-high` against `xhigh`). Which effort a family treats as its unsuffixed default varies. Some slugs carry no effort at all. Twenty display names, all Claude Fable models, are marked `(NO ZDR)`: no zero data retention for those models.

**[help]** "Parameterized models also accept quoted overrides, e.g. `--model 'claude-opus-4-8[context=1m,effort=high,fast=false]'`."

**[probed] The bracket form was refused** on `gpt-5.4-mini[effort=low]` and `claude-sonnet-5-5[effort=low]`, before any session, with the same stderr as an unknown model. The CLI does not say which models are "parameterized" **[open]**, and the help's own example, `claude-opus-4-8`, was not tried. On build `2026.11.19-f005e9d`, `claude-sonnet-5-5[effort=low]` is still refused, and the model's own parameter ids run (section 13). Whether the right ids would have run on this build was not tried.

**[probed] A base name outside the catalogue is accepted and resolved silently.** `--model gpt-5.4-mini` is not in the list, yet it ran, and `init` named it "GPT-5.4 Mini Low". Only the `init` display name shows which variant ran.

**[probed]** With no `--model`, `about` reports `Model Auto`.

## 6. Permissions and sandbox

**[docs]** Permissions live in config files only: `~/.cursor/cli-config.json` globally, or `<project>/.cursor/cli.json`, which may hold permissions and nothing else. `CURSOR_CONFIG_DIR` or `XDG_CONFIG_HOME` relocate the global file. Rules take the forms `Shell(cmd)`, `Read(glob)`, `Write(glob)`, `WebFetch(domain)`, and `Mcp(server:tool)`, in `allow` and `deny` lists. There is no per-run allowlist flag.

**[help]** `-f, --force` means "Force allow commands unless explicitly denied", and `--yolo` is its alias ("Run Everything"). `--sandbox enabled|disabled` overrides the config. <!-- risk-ok: Cursor CLI flag name -->

**[probed]** A, B, and C ran the five-step task (edit `tracked.txt`, run `bash check.sh`, `cat ../outside.txt`, `curl https://example.com`, `git add && git commit`). B2 and B3 ran a four-step shell variant (write inside the repo, write `../outside-write.txt`, the same `curl`, commit). All ran under the config above:

| Probe | Flags | Edit | `check.sh` | Outside read | Network | Commit | Outside write |
|---|---|---|---|---|---|---|---|
| A | none | ran | rejected | spawnError | rejected | ran | not tried |
| B | `--force --sandbox enabled` | ran | ran | ran | 200 | ran | not tried | <!-- risk-ok: Cursor CLI flag name -->
| B2 | `--force --sandbox enabled` | ran (shell) | not tried | not tried | 200 | ran | **landed** | <!-- risk-ok: Cursor CLI flag name -->
| B3 | `--sandbox enabled` | spawnError (shell) | not attempted | not attempted | not attempted | not attempted | not attempted |
| C | `--force` | ran | ran | ran | 200 | ran | not tried | <!-- risk-ok: Cursor CLI flag name -->

What that shows:

- **Under the stock allowlist, a worker cannot run its checks.** `bash check.sh` was rejected and the marker file was never written.
- **`git add && git commit` ran in probe A with no allow rule covering it.** Why is unexplained **[open]**. Cursor's allowlist evidently approves some commands on its own.
- **With `--force`, the sandbox confined nothing.** The outside write landed and the network call returned 200. <!-- risk-ok: Cursor CLI flag name -->
- **Without `--force`, the sandbox could not start a shell** (`spawn /bin/zsh ENOENT`). The worker reported that plainly and stopped after its first attempt. <!-- risk-ok: Cursor CLI flag name -->
- Whether `--force` bypasses the sandbox, or the sandbox is simply broken on this machine, cannot be told apart here **[open]**. <!-- risk-ok: Cursor CLI flag name -->

The Cursor rows in `docs/research/cross-agent-cli-permissin-mode.md` propose `default` = `--sandbox enabled` plus an allowlist. On this machine that combination runs no shell command.

## 7. Read-only (plan mode)

**[help]** `--mode plan` is "read-only/planning (analyze, propose plans, no edits)". `--mode ask` is Q&A, also read-only.

**[probed]** Probe D (`--mode plan`) was asked to read a file, create a file, and run `echo shell > plan-shell.txt`. It globbed and read, attempted neither write, and reported "I can't complete steps 2 or 3 here". The tree stayed clean and there were no `rejected` events, because nothing was attempted.

**[probed] `--mode plan --force` behaves the same** (probe E). The worker wrote "Refusal: plan mode is active, so I'm not allowed to make file changes", nothing changed on disk, and it claimed no write. This differs from GitHub Copilot CLI, where plan mode plus allow-all-tools produced false claims of writes. <!-- risk-ok: Cursor CLI flag name -->

## 8. Authentication and the configuration directory

**[probed]** The login survives a relocated config directory. With `CURSOR_CONFIG_DIR` set to an empty directory, `status` still reported the logged-in account, and Cursor wrote a default `cli-config.json` (837 bytes) into that directory. `init` reports `apiKeySource: "login"`.

**[help]** `CURSOR_API_KEY` or `--api-key` authenticate without a login.

## 9. Commit attribution

**[probed]** With `attribution.attributeCommitsToAgent = true`, every commit the agent made had the trailer added to its own command line, visible in the `tool_call` args:

```
git add tracked.txt && git commit --trailer "Co-authored-by: Cursor <cursoragent@cursor.com>" -m probe-commit
```

The prompt asked for `git commit -m probe-commit`; the trailer came from Cursor, not from a git hook.

## 10. Implications for agent-handoff (inference, not observation)

`docs/specs/design_cli-backends.md` holds the decisions. In short:

- Each part of the job primitive has a Cursor equivalent: print mode, a JSONL stream, a session id from line one, `--resume` with context intact after a kill, a typed denial, and per-invocation token usage.
- Two things do not fit Handoff's existing shapes. Effort is not a routing field, because it lives in the slug. And there is no posture narrower than `--force` in which a worker can still run its checks. <!-- risk-ok: Cursor CLI flag name -->
- Three type names collide with Claude Code's `stream-json`. Every parser has to take the backend as an input, and a metadata-free log can only be identified as Cursor by `tool_call` or `thinking`.
- Cursor's `result.result` must not be read as the final agent message.

## 11. Probe log

All runs on `gpt-5.4-mini-low` unless noted, in a throwaway repo reset between runs. Logs were kept in the session scratchpad and are not committed.

| # | Probe | Flags beyond `-p --output-format stream-json --trust` | Exit | Observed |
|---|---|---|---|---|
| F1 | unknown model | `--model no-such-model-xyz --mode ask` | 1 | stderr refusal plus catalogue; no events |
| F3 | config dir | `CURSOR_CONFIG_DIR=<empty>`, `status` | 0 | still logged in; default config written |
| A | stock config | `--workspace` | 0 | 3 `rejected`, 2 `spawnError`; edit and commit landed; checks never ran |
| B | force + sandbox | `--force --sandbox enabled` | 0 | everything ran, network 200 | <!-- risk-ok: Cursor CLI flag name -->
| C | force | `--force` | 0 | everything ran, network 200 | <!-- risk-ok: Cursor CLI flag name -->
| F | resume C | `--resume <C id>`, no model or workspace | 0 | answered `probe-edit`; 130 input tokens |
| D | plan | `--mode plan` | 0 | read only; declined writes; tree clean |
| E | plan + force | `--mode plan --force` | 0 | same as D; no false claims | <!-- risk-ok: Cursor CLI flag name -->
| G | kill | `--force`, SIGKILL after first `tool_call` | 137 | no `result`; log intact | <!-- risk-ok: Cursor CLI flag name -->
| G2 | resume G | `--resume <G id>` | 0 | answered `KILL-TOKEN-4417` |
| H0 | base name | `--model gpt-5.4-mini --mode ask` | 0 | ran as "GPT-5.4 Mini Low" |
| H1 | bracket, GPT | `--model 'gpt-5.4-mini[effort=low]'` | 1 | refused before session |
| H2 | bracket, Claude | `--model 'claude-sonnet-5-5[effort=low]'` | 1 | refused before session |
| J | create-chat | `create-chat`, then `--resume <uuid> --mode ask` | 0 | ran with the assigned id |
| B2 | sandbox, outside write | `--force --sandbox enabled` | 0 | outside write landed; network 200 | <!-- risk-ok: Cursor CLI flag name -->
| B3 | sandbox, no force | `--sandbox enabled` | 0 | first shell call `spawn /bin/zsh ENOENT`; worker stopped; nothing written |

## 12. Handoff end-to-end run (2026-09-30)

Recorded by Task 8 of `docs/specs/plan_cursor-cli-backend.md`, driving build `2026.09.28-64d2043` through `delegate-codex.sh` on `gpt-5.4-mini-low`, from a scratch repo under a long session scratch path.

- **[probed] Resuming an id Cursor never saw does not fail.** `-p "Reply with exactly: ok" --output-format stream-json --trust --mode ask --resume <fresh uuid4>` exited 0 with an empty stderr; `init` and `result` both carried the new uuid as `session_id`, and the run answered `ok`. Cursor starts a new, empty session under that id. So a Handoff fix round against a session Cursor never persisted loses its context silently, with no error in `stderr.log`. This corrects the expectation in R4.3.
- **[probed] A mistyped `workingDirectory` surfaces as `spawn /bin/zsh ENOENT`.** The model retyped the long workspace path into `shellToolCall.args.workingDirectory` wrong twice (`agent-hand-off`, then a dropped `harness-`). Both calls completed with `spawnError` "spawn /bin/zsh ENOENT": the missing path is the cwd, not the shell. A fix round that named the typo ran the check. This may also explain probe A's `spawnError` on `cat ../outside.txt` (inference, not verified).
- **[probed] `askQuestionToolCall` is rejected under `-p --mode plan`.** A read-only job asked to write a file tried to ask the user a question; the call completed with a `rejected` result, and Handoff counted it as one denial. Nothing was written.
- **[probed] The commit trailer appears on worktree commits.** A worker commit on a Handoff worktree branch carried `Co-authored-by: Cursor <cursoragent@cursor.com>`.
- **[open] A `-p` run while logged out, and a mid-run API failure.** Not provoked: neither could be caused without logging the user out or exhausting a quota.

## 13. Context window and model selection (build `2026.11.19-f005e9d`, 2026-11-20)

Probed for 4.0.2 after the user asked whether Handoff jobs run Claude Sonnet 5.5 at 300K or at 1M. Cursor's picker offers both. 1M is what Cursor calls Max Mode, and it uses up the account's usage far faster. Every run below was `-p "Reply with exactly: ok" --output-format stream-json --trust --mode ask` (the resume probes asked for a code word instead), on a Pro account, from a scratch repo. Each ran with `CURSOR_CONFIG_DIR` pointed at its own scratch directory: an edited copy of the user's `cli-config.json`, or an empty one. The user's own file had the same SHA-256 before and after. Logs were kept in the session scratchpad and are not committed.

**[probed] `init.model` names the context the run used.** It is the only place a run says so: `"Claude Sonnet 5.5 300K High"` or `"Claude Sonnet 5.5 1M High"`. No other event carries a context or Max Mode field.

**[probed] The catalogue's display names do not predict the context.** `cursor-agent models` lists `claude-opus-5-5-high - Claude Opus 5.5 1M High`, `gpt-5.6-sol-medium - GPT-5.6 Sol 1M` and `claude-opus-5-thinking-high - Claude Opus 5 1M Thinking`, yet each ran at the model's default context (CTXOPUS, CTXGPT, CTXOPUS5). Sonnet 5.5 and Haiku 5.5 print an empty slot there, a double space: `Claude Sonnet 5.5  High`.

**[probed] Every plain slug tried ran at the model's default context.** Four slugs from three model families did (CTXSLUG, CTXOPUS, CTXGPT, CTXOPUS5). `claude-sonnet-5-5-high` also ran under a saved 1m selection and an empty config (CTXSAVED, CTXFRESH), and the saved selection did not reach a run that named `--model`. The other three slugs ran on empty configs only.

**[probed] `about` reads the saved selection and ignores `--model`.** On this build `CURSOR_CONFIG_DIR=<dir> cursor-agent about` printed `Model  Claude Sonnet 5.5 300K High` for a saved 300k selection, `Model  Claude Sonnet 5.5 1M High` for a saved 1m selection, and `Model  Auto` for an empty config, with or without `--model claude-sonnet-5-5-high`. So `about` shows what a run without `--model` would use, and nothing about a run that names one.

**[probed] The bracket form works when given the model's own parameter ids, and can select 1M.** `claude-sonnet-5-5[context=1m,reasoning_effort=high]` ran as `Claude Sonnet 5.5 1M High` (CTXBRACKET). H1 and H2 in section 11 were refused because `effort` is not the id those models use. The ids differ by model, and the CLI lists none of them. Refusals for a bad id, a bad value (`context=2m`) or a slug with a bracket (`claude-sonnet-5-5-high[context=300k]`) all print the generic `Cannot use this model` line. The ids below are read from what the CLI saved after each run:

| Model | Parameter ids saved |
|---|---|
| `claude-sonnet-5-5` | `context`, `reasoning_effort` |
| `claude-opus-5-5` | `context`, `effort`, `fast` |
| `claude-opus-5` | `thinking`, `context`, `effort`, `fast` |
| `gpt-5.6-sol` | `context`, `reasoning`, `fast` |
| `gpt-5.4-mini` | `reasoning` |

**[probed] A resume without `--model` runs the saved selection, not the session's model.** CTXRESUME: a parent ran at 300K and was told a code word. The saved selection was then edited to Sonnet 5.5 1M, as a user picking it in interactive mode would. `--resume <id>` with no `--model` ran as `Claude Sonnet 5.5 1M High` and still recalled the code word. CTXRESUMEMODEL: the same resume with `--model claude-sonnet-5-5-high` ran at 300K and also recalled it. Probe F in section 11 showed "the same model" only because its parent had just saved that model.

**[probed] Every `-p` run saves its model into `cli-config.json`.** It writes `model`, `selectedModel`, `modelParameters.<model>`, and on an empty config also `maxMode` and `hasChangedDefaultModel` (CTXSAVED, CTXFRESH). The next interactive session, and the next run without `--model`, start from it. A run never cleared a `maxMode: true` already in the file.

**[probed] Cursor's own `maxMode` flag is not the context.** After the Sonnet 5.5 300K run on an empty config it was `false`. After the Sonnet 5.5 1M run it was `true`, along with `maxModeAutoEnabled`. After the Opus 5.5, Opus 5 and GPT-5.6 Sol runs it was `true` at their default contexts. Cursor's model docs say those models "require Max Mode on legacy request-based plans".

**[docs]** Cursor's pricing page: Max Mode "extends a model's context window beyond the default limit and is billed at the model's API rate plus 20%", on legacy request-based plans only. On other plans Sonnet 5.5 goes "up to 1M tokens with extended context at the same per-token rates". The CLI changelog for v2026.08.11 says a headless `--model` selection that requires Max Mode now sends it, "so max-context variants are no longer clamped down".

**[probed] A 1M run sends more tokens per request.** The same reply-only prompt cost about 44k input tokens at 1M (CTXBRACKET: 8,976 cache read plus 35,419 cache write) against about 24k at 300K (CTXSLUG, CTXFRESH).

**[open]** Whether the Cursor IDE's model picker writes `~/.cursor/cli-config.json`: `ide_state.json` holds no model keys. And whether a sticky `maxMode: true` changes how a default-context run is billed on a legacy request-based plan.

| # | Config dir | `--model` | Exit | `init.model` |
|---|---|---|---|---|
| CTXSLUG | copy, saved Sonnet 5.5 300k | `claude-sonnet-5-5-high` | 0 | Claude Sonnet 5.5 300K High |
| CTXSAVED | copy, saved Sonnet 5.5 1m | `claude-sonnet-5-5-high` | 0 | Claude Sonnet 5.5 300K High; saved selection rewritten to 300k |
| CTXFRESH | empty | `claude-sonnet-5-5-high` | 0 | Claude Sonnet 5.5 300K High |
| CTXBRACKET | empty | `claude-sonnet-5-5[context=1m,reasoning_effort=high]` | 0 | Claude Sonnet 5.5 1M High |
| CTXSLUGBRACKET | empty | `claude-sonnet-5-5-high[context=300k]` | 1 | refused before session |
| CTXOPUS | empty | `claude-opus-5-5-high` | 0 | Claude Opus 5.5 300K High |
| CTXOPUSBRACKET | empty | `claude-opus-5-5[context=300k,reasoning_effort=high]`, and the slug with `[context=300k]` | 1 | both refused before session |
| CTXGPT | empty | `gpt-5.6-sol-medium` | 0 | GPT-5.6 Sol 272K Medium |
| CTXOPUS5 | empty | `claude-opus-5-thinking-high` | 0 | Claude Opus 5 300K High |
| CTXRESUME | parent at 300K, then saved selection set to 1m | none, `--resume <parent>` | 0 | Claude Sonnet 5.5 1M High; recalled the code word |
| CTXRESUMEMODEL | same, saved selection set to 1m again | `claude-sonnet-5-5-high`, `--resume <parent>` | 0 | Claude Sonnet 5.5 300K High; recalled the code word |

### 13.1 Handoff live run (4.0.2)

The 4.0.2 tree ran through the installed skill (a symlink to the working tree at `ba43027` plus the uncommitted change) on build `2026.11.19-f005e9d`, from a scratch repo. `CURSOR_CONFIG_DIR` pointed at a copy of the user's config. Before each launch its saved selection was set to Sonnet 5.5 1M, and that was confirmed twice: by reading `selectedModel` and `modelParameters.claude-sonnet-5-5` (both `context=1m`), and by `about` printing `Claude Sonnet 5.5 1M High`.

- **[probed] A fresh job ran at 300K.** `submit --backend cursor --model claude-sonnet-5-5-high` wrote `--workspace "$WORKDIR" --model "$MODEL"` into `run.sh`. The job edited `notes.txt` and ran the repo check, whose `CHECK_OK` is in its log. `init.model` read `Claude Sonnet 5.5 300K High`, and `result --json` gave `context_window: "300K"`.
- **[probed] Its fix round ran at 300K and kept its context.** After the selection was set back to 1M, `resume` wrote `--resume "$SESSION_ID" --model "$MODEL"` and recorded `model=claude-sonnet-5-5-high` in `meta`. `init.model` read 300K, and the round answered with the parent's code word.
- **[probed] Refusals came before any job directory.** A bracket model, an omitted model, and a resume of a parent whose `meta` recorded `claude-sonnet-5-5[context=1m,reasoning_effort=high]` each exited 1 and named the fix.
- **[probed] A real 1M job was reported while running, and then held.** A wrapper used as `HANDOFF_CURSOR_BIN` swapped the slug for the bracket selector and asked the worker to run `sleep 45`. The first `status` poll after `init` printed `state: RUNNING` and the `context_window: 1M` line, and `cancel` ended the job. After that, a submit of `claude-sonnet-5-5-high` on the real binary, a resume of the 1M job, and a resume of the earlier 300K fix round on the same slug were all refused, each naming the 1M job and the build. A dry-run on `claude-opus-5-5-high` was accepted.
- **[probed] The real CTXBRACKET log, mounted in a scratch job of a second repo,** gave the `context_window: 1M` line in `status` and in text `result`, and `"1M"` in `result --json`.
- **[probed] `cursor-agent -p` starts a detached `worker-server`.** A `node … index.js worker-server` process started three seconds after the first live job, with launchd as its parent, so it is outside the job's process group and `cancel` does not reach it. It carried the job's `CURSOR_CONFIG_DIR` and was still running more than five minutes later, when the driver stopped it.
- The user's own `~/.cursor/cli-config.json` had the same SHA-256 before and after.

## References

- Cursor CLI output format: https://cursor.com/docs/cli/reference/output-format
- Cursor CLI permissions: https://cursor.com/docs/cli/reference/permissions
- Cursor CLI configuration: https://cursor.com/docs/cli/reference/configuration
- Cursor CLI headless mode: https://cursor.com/docs/cli/headless
- Cursor CLI parameters: https://cursor.com/docs/cli/reference/parameters
- The permission abstraction this backend maps onto: `docs/research/cross-agent-cli-permissin-mode.md`
- The previous backend's research, for comparison: `docs/research/github-copilot-cli-specification.md`
