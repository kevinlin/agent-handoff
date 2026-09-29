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

**[probed] Resume keeps context.** `cursor-agent -p "<question>" --output-format stream-json --trust --resume <id>`, run from the repo with no `--model` and no `--workspace`, answered from the parent's context (probe F returned `probe-edit`). The `init` event showed the same model and session id.

**[probed] A session killed mid-run resumes.** Probe G was killed while its first tool call was running (exit 137, no `result`). Resuming its id recovered `KILL-TOKEN-4417` from the killed turn.

**[probed]** `cursor-agent create-chat` prints a UUID. `-p --resume <that uuid>` as the first turn ran with it as the `session_id` (probe J). So an id can be assigned before launch, but section 4's first finding makes that unnecessary.

**[probed] Usage is per invocation.** The resumed turn in F reported `inputTokens: 130` against its parent's 18350.

**[open]** Resuming an id whose job died before `init`, and resuming an id that never existed.

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

**[probed] The bracket form was refused** on `gpt-5.4-mini[effort=low]` and `claude-sonnet-5-5[effort=low]`, before any session, with the same stderr as an unknown model. The CLI does not say which models are "parameterized" **[open]**, and the help's own example, `claude-opus-4-8`, was not tried.

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

`docs/specs/design_cursor-cli-backend.md` holds the decisions. In short:

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

## References

- Cursor CLI output format: https://cursor.com/docs/cli/reference/output-format
- Cursor CLI permissions: https://cursor.com/docs/cli/reference/permissions
- Cursor CLI configuration: https://cursor.com/docs/cli/reference/configuration
- Cursor CLI headless mode: https://cursor.com/docs/cli/headless
- Cursor CLI parameters: https://cursor.com/docs/cli/reference/parameters
- The permission abstraction this backend maps onto: `docs/research/cross-agent-cli-permissin-mode.md`
- The previous backend's research, for comparison: `docs/research/github-copilot-cli-specification.md`
