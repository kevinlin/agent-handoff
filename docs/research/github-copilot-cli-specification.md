# GitHub Copilot CLI Integration Specification

## Status and provenance

Rewritten 2026-09-09 from live probing of **GitHub Copilot CLI 1.0.83** on macOS (Darwin 25.6.0),
account `kevinlin`, six real `-p` runs against a scratch git repo. The previous version of this
document was written from published docs alone and got several load-bearing details wrong; section
14 lists the corrections.

Every claim carries its provenance:

- **[probed]** — observed by running the CLI here, with the evidence quoted.
- **[help]** — read from `copilot --help` on 1.0.83.
- **[open]** — not verified. A planning session should settle it before relying on it.

The consumer of this document is the `agent-handoff` skill, which wants Copilot as a third
delegation backend beside `codex` and `claude`. Sections 1-12 are facts about the CLI. Section 13
draws the implications for that integration and is explicitly marked as inference, not observation.

---

## 1. Objective

Add GitHub Copilot CLI as a non-interactive coding-agent backend that can be delegated a durable
background job: run a prompt, edit files, run the repo's own verification commands, emit a
machine-readable event stream, expose a session id, and accept a follow-up round on that same
session.

---

## 2. Installation, binary identity, and the name collision

**[probed]** `npm install -g @github/copilot` installs a `copilot` shim:

```
~/.nvm/versions/node/v24.15.0/bin/copilot -> ../lib/node_modules/@github/copilot/npm-loader.js
```

**[probed] The binary name collides with AWS Copilot CLI, and PATH order decides the winner.**
Homebrew's `copilot-cli` formula installs an unrelated tool (ECS/App Runner deployment) at
`/opt/homebrew/bin/copilot`. Both were present here:

```
$ which -a copilot
/Users/keli/.nvm/versions/node/v24.15.0/bin/copilot
/opt/homebrew/bin/copilot
```

The two are distinguishable only by their version output:

| Binary | `--version` output |
|---|---|
| GitHub Copilot CLI | `GitHub Copilot CLI 1.0.83.` |
| AWS Copilot CLI | `copilot version: v1.34.1` |

Consequence: `command -v copilot` is not sufficient discovery. Resolution must probe identity —
match `GitHub Copilot CLI` in `--version` output — and fail closed with an actionable message when
the name resolves to something else. Neither `codex` nor `claude` has this problem, so this is new
work rather than a copy of an existing pattern.

**[probed]** Authentication needed no explicit `copilot login`. An existing `gh auth` session
(scopes `gist`, `read:org`, `repo`, `workflow`) was sufficient. A `copilot login` subcommand exists
for the browser and device-code flows.

**[probed]** First run creates `~/.copilot/`:

```
config.json          settings
logs/                per-run logs (--log-dir default)
session-state/       per-session transcripts
session-store.db     sqlite + -shm/-wal
```

Resumable session state lives here, outside any repo. Same situation as `~/.codex` and Claude
Code's own store, so it introduces no new class of problem.

---

## 3. Non-interactive invocation

**[probed]** The verified working shape:

```bash
copilot \
  -p "$PROMPT" \
  --output-format json \
  -C "$REPO_ROOT" \
  --model "$MODEL" \
  --effort "$EFFORT" \
  --no-ask-user \
  --no-remote --no-remote-export \
  --no-auto-update \
  --allow-all-tools \
  --usage-output-file "$USAGE_JSON" \
  </dev/null
```

**[help]** The options that matter, quoted from 1.0.83:

| Option | Behaviour |
|---|---|
| `-p, --prompt <text>` | Execute a prompt in non-interactive mode (exits after completion). Takes a value. |
| `--output-format <format>` | `text` (default) or `json` (JSONL, one JSON object per line). |
| `-s, --silent` | Output only the agent response, no stats. For scripting with `-p` in text mode. |
| `--stream <mode>` | `on` or `off`. |
| `-C <directory>` | Change working directory before doing anything else. |
| `--model <model>` | Set the model (`auto` lets Copilot pick). |
| `--effort, --reasoning-effort <level>` | `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. |
| `--mode <mode>` | `interactive`, `plan`, or `autopilot`. |
| `--plan` | Start in plan mode. |
| `-r, --resume[=value]` | Resume a previous session by id, task id, id prefix, or name. |
| `--continue` | Resume the most recent session. |
| `--session-id <id>` | Resume by id, **or set the UUID for a new session**. |
| `--no-ask-user` | Disable the `ask_user` tool so the agent never waits on a question. |
| `--allow-all-tools` | Allow all tools without confirmation; help text says "required for non-interactive mode" (env `COPILOT_ALLOW_ALL`). |
| `--allow-tool[=tools...]` / `--deny-tool[=tools...]` | Per-tool allow and deny rules. |
| `--available-tools` / `--excluded-tools` | Restrict which tools the model can see at all. |
| `--usage-output-file <file>` | Write final usage statistics as JSON. |
| `--log-dir <directory>` / `--log-level <level>` | Log destination and verbosity. |
| `--max-ai-credits <credits>` | Cap AI credits for the session. |
| `--secret-env-vars[=vars...]` | Strip named env values from shell and MCP environments, redact from output. |
| `--no-remote` / `--no-remote-export` | Disable remote control and session export (see section 11). |
| `--no-auto-update` | Skip the update download; already off in CI environments. |
| `--worktree` | Create a new worktree and start the session there (see section 12). |
| `--add-dir <directory>` | Grant file access to another directory and trust its `.github/skills` and `.github/agents`. |
| `--agent <agent>` | Use a named custom agent. |
| `--no-custom-instructions` | Skip `AGENTS.md` and related files. |
| `--enable-memory` | Cross-session memory; off by default in prompt mode. |
| `--allow-all`, `--yolo` | `--allow-all-tools` plus `--allow-all-paths` plus `--allow-all-urls`. |

Subcommands: `app`, `completion`, `help`, `init`, `login`, `mcp`, `plugin`, `plugins`, `skill`,
`update`, `version`. Help topics: `billing`, `commands`, `config`, `environment`, `limits`,
`logging`, `monitoring`, `permissions`, `providers`, `sandbox`.

**[probed]** `</dev/null` matters. A background `-p` job with an open stdin is the same hazard the
other two backends have, so the redirect stays.

---

## 4. The JSONL event stream

**[probed]** `--output-format json` emits one JSON object per line. Common envelope:

```json
{"type":"...","data":{...},"ephemeral":true,"id":"<uuid>","timestamp":"2026-09-09T05:31:17.966Z","parentId":"<uuid>"}
```

`parentId` links events into a tree. **`ephemeral: true` marks streaming noise** — one small job
produced 82 `assistant.message_delta` events, all ephemeral. Dropping every ephemeral event is the
transcript filter.

The terminal `result` event is the one exception to the envelope: no `id`, `parentId`, or `data`.

### 4.1 Event vocabulary observed

A no-tool run produced 21 lines; a tool-using run added the `tool.*` and background-task events.

| Event type | Carries |
|---|---|
| `session.managed_settings_resolved` | policy and managed-settings posture |
| `session.mcp_server_status_changed` | MCP server lifecycle (`pending`, `connected`) |
| `session.mcp_servers_loaded` | server list and their injected instructions |
| `session.auto_mode_resolved` | `chosenModel`, `candidateModels`, **`availableModels`**, `routingMethod` |
| `session.tools_updated` | resolved model for the tool set |
| `session.info` | generic info notices, e.g. `{"infoType":"file_created","message":"<path>"}` |
| `session.background_tasks_changed` | background task bookkeeping |
| `session.usage_checkpoint` | running usage counters |
| `session.error` | **structured API and session failures** (see 4.4) |
| `user.message` | the prompt as sent |
| `assistant.turn_start` / `assistant.turn_end` | turn boundaries |
| `model.call_start` / `model.call_finished` | one model call |
| `assistant.message_start` / `assistant.message_delta` | streaming text, ephemeral |
| `assistant.tool_call_delta` | streaming tool arguments, ephemeral |
| `assistant.message` | a complete assistant message (see 4.2) |
| `tool.execution_start` | a tool invocation (see 4.3) |
| `tool.execution_complete` | its outcome, including denials (see 4.3) |
| `assistant.idle` | agent finished working |
| `result` | terminal event: `sessionId`, `exitCode`, `usage` |

### 4.2 Assistant messages

**[probed]** Two shapes share the `assistant.message` type. A tool-calling turn has empty content
and a populated `toolRequests`:

```json
{"type":"assistant.message","data":{
  "messageId":"...","model":"mai-code-1.1-flash","content":"",
  "toolRequests":[{"toolCallId":"call_qjDQ...","name":"create",
    "arguments":{"path":"...probe.txt","file_text":"ok\n"},
    "type":"function","intentionSummary":"create a new file at ...probe.txt."}],
  "turnId":"0"}}
```

The final answer carries text and `phase: "final_answer"`:

```json
{"type":"assistant.message","data":{
  "content":"HANDOFF_SMOKE_OK","toolRequests":[],
  "turnId":"1","phase":"final_answer","model":"mai-code-1.1-flash"}}
```

So the agent's answer is `data.content` where `data.phase == "final_answer"`. Selecting on
non-empty content alone would also pick up intermediate turns.

### 4.3 Tool calls, results, and denials

**[probed]** Start:

```json
{"type":"tool.execution_start","data":{
  "toolCallId":"call_RUZJ...","toolName":"bash",
  "arguments":{"command":"curl -s https://example.com -o /dev/null && echo \"curl_exit:$?\"",
               "description":"Run the required curl request and report exit status",
               "mode":"sync","initial_wait":30},
  "turnId":"0","model":"mai-code-1.1-flash",
  "shellToolInfo":{"possiblePaths":["https://example.com","/dev/null"],"hasWriteFileRedirection":false}}}
```

Success:

```json
{"type":"tool.execution_complete","data":{
  "toolCallId":"call_qjDQ...","success":true,
  "result":{"content":"Created file ...probe.txt with 3 characters",
            "detailedContent":"\ndiff --git a/...probe.txt b/...probe.txt\ncreate file mode 100644\n..."}}}
```

**Denial — machine-readable, with the rule that caused it:**

```json
{"type":"tool.execution_complete","data":{
  "toolCallId":"call_RUZJ...","success":false,
  "error":{"message":"Permission to run this tool was denied due to the following rules: `shell(curl)`",
           "code":"denied"},
  "toolTelemetry":{"properties":{"shell_error_category":"permission_denied"}}}}
```

**[probed] The job still exited 0.** The worker wrote its file, was refused `curl`, reported the
refusal honestly in its final message, and the process exit code was `0`. A denied tool call does
not move the exit code. This is the same failure mode `agent-handoff` hit on its first real Claude
job (v3.5.1): a blocked acceptance check leaves exit 0 behind, and a self-report saying the gates
passed is not evidence. Copilot's advantage is that the denial is a typed event with `error.code ==
"denied"` rather than something that has to be pattern-matched.

Tool names seen: `create` (file write), `bash` (shell).

### 4.4 Errors

**[probed]** API-level failures arrive as a `session.error` event, and stderr can be empty:

```json
{"type":"session.error","data":{
  "errorType":"query",
  "message":"Execution failed: 400 Unsupported value: 'none' is not supported with the 'mai-code-1-flash-2026-06-02' model. Supported values are: 'minimal', 'low', 'medium', and 'high'. (Request ID: ...)",
  "statusCode":400,"providerCallId":"...","serviceRequestId":"..."}}
```

followed by `{"type":"result","exitCode":1,...}`. Error classification must read the log, not only
stderr. CLI-layer argument rejections behave differently — see section 7.

---

## 5. Session id and resume

**[probed]** The session id appears in exactly one place in the stream, the terminal event:

```json
{"type":"result","timestamp":"2026-09-09T05:31:25.752Z",
 "sessionId":"c33ddfad-4c6b-47a3-af49-6b2ca80182ce","exitCode":0,
 "usage":{"premiumRequests":1,"totalApiDurationMs":3533,"sessionDurationMs":8717,
          "codeChanges":{"linesAdded":0,"linesRemoved":0,"filesModified":[]}}}
```

`session.info` is a generic notice event and carries no identity. So a *running* job has no session
id in its log; the id lands when the job ends.

**[help]** `--session-id <id>` also sets the UUID for a new session. That makes the id knowable at
submit time instead of parsed at the end, which is worth considering rather than assuming the
extract-from-log route.

**[probed] Non-interactive resume works and keeps history.** Run a job that creates `probe.txt`,
then:

```bash
copilot -p "What was the exact name of the file you created in your previous turn? Answer with just the filename." \
  --resume "$SID" --output-format json --no-ask-user --effort medium
```

It answered `probe.txt`, exit 0, and the `result` event returned **the same** `sessionId`. No
`--model` was passed on the resume and it was not needed. Working directory came from the shell,
matching how `codex exec resume` and `claude --resume` behave.

---

## 6. Model selection and discovery

**[probed]** `--model auto` works and resolves per request. `session.auto_mode_resolved` records
the decision:

```json
{"type":"session.auto_mode_resolved","data":{
  "chosenModel":"mai-code-1.1-flash","candidateModels":["mai-code-1.1-flash"],
  "availableModels":["mai-code-1.1-flash"],"routingMethod":"auto_v2","fallback":false,
  "categoryScores":{"reasoning":0.059,"code_gen":0.0114,"debugging":0.0025,"tool_use":0.0069}}}
```

**[probed] There is no non-interactive model catalog.** `copilot model` and `copilot models` are
not commands (`error: Invalid command format.`), there is no `models` subcommand in `--help`, and
`copilot help config` points only at the interactive `/model` command and the `--model` flag. The
two programmatic sources are:

1. `session.auto_mode_resolved.data.availableModels`, which requires running a job.
2. Execution itself, which rejects an unavailable model.

**[probed]** Rejection is a plain-text stderr line at the CLI layer, before any session starts:

```
Error: Model "definitely-not-a-model" from --model flag is not available.
```

**[probed]** On this account only `mai-code-1.1-flash` was available. `claude-sonnet-4.5` and
`gpt-5.4` both returned "not available". The catalog is gated by account plan, org policy, and
model rollout, so a hardcoded model list will be wrong for someone.

---

## 7. Reasoning effort

**[help]** The CLI accepts `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, `max`. That is
codex's enum minus `ultra`, plus `none`.

**[probed] `none` is a literal value passed through to the API, not "omit the flag".** With
`--model auto --effort none` the API returned 400: `'none' is not supported with the
'mai-code-1-flash-2026-06-02' model. Supported values are: 'minimal', 'low', 'medium', and
'high'.`

**[probed] `--model auto` is incompatible with an explicit effort, and rejection happens at two
different layers.**

| Invocation | Layer | Evidence | Exit |
|---|---|---|---|
| `--model auto --effort medium` | CLI, before session start | stderr: `Error: Model "auto" does not support reasoning effort configuration (requested: "medium").` No `result` event. | 1 |
| `--model auto --effort none` | API, after session start | `session.error` with statusCode 400, then `result` with `exitCode: 1`. stderr empty. | 1 |
| `--model mai-code-1.1-flash --effort medium` | accepted | `result` with `exitCode: 0`, `premiumRequests: 1` | 0 |

So effort validity is decided per model by the API, and the CLI's enum is only a superset. Passing
the requested effort and surfacing the rejection is right; silently downgrading is not.

---

## 8. Permissions

**[probed]** With a narrow allow list and one explicit deny:

```bash
--allow-tool 'write' --allow-tool 'shell(git:*)' --deny-tool 'shell(curl)'
```

the job created a file (allowed by `write`), was refused `curl` with the typed denial in 4.3, and
exited 0.

**[probed]** With only `--allow-tool 'write'` and no deny rules, the agent ran `echo
hello-from-copilot` successfully. So Copilot has its own built-in baseline of commands it treats as
safe, and an allow list adds to that baseline rather than replacing it.

**[help]** `--allow-all-tools` is described as "required for non-interactive mode". `--allow-all`
and `--yolo` additionally disable path and URL verification. `--assisted-approval` routes requests
through a safety judge and needs `--experimental`.

**[open]** The case that decides the integration was not probed: under a curated allow list, is a
repository's own verification command — `bash scripts/check-skill-repo.sh`, `python3 -m unittest
discover`, `npm test` — allowed by the built-in baseline or denied? The `echo` result suggests the
baseline is broader than the allow list alone, but `echo` is not evidence about a project script.

**Decision recorded 2026-09-09** (this is a decision, not a probe): a delegated Copilot job defaults
to `--allow-all-tools`, mirroring the `bypassPermissions` default a delegated Claude job already
gets, with an env escape hatch and a warning at submit time. The reasoning is the v3.5.1 retro. A
background `-p` job has no approval surface, so any rule that would prompt denies instead; the
denial does not move the exit code; and the worker then reports that gates passed which it was in
fact refused. What bounds a delegated worker is its worktree and its packet. The typed
`error.code: "denied"` event is the safety net, and a job with denials must be treated as failed
whatever its exit code says.

This decision retires the repository-stack-detection permission builder that the previous version
of this document specified. It is machinery whose failure mode is silent.

---

## 9. Usage, tokens, and the AI-credit meter

**[probed]** `result.usage` gives coarse figures:

```json
{"premiumRequests":1,"totalApiDurationMs":3533,"sessionDurationMs":8717,
 "codeChanges":{"linesAdded":0,"linesRemoved":0,"filesModified":[]}}
```

`--usage-output-file` gives the detailed set:

```json
{"totalPremiumRequestCost":1,"totalUserRequests":1,"totalNanoAiu":291564000,
 "tokenDetails":{"input":{"tokenCount":16144},"cache_read":{"tokenCount":0},
                 "cache_write":{"tokenCount":0},"output":{"tokenCount":9}},
 "totalApiDurationMs":3533,"sessionStartTime":"2026-09-09T05:31:17.035Z",
 "codeChanges":{"linesAdded":0,"linesRemoved":0,"filesModifiedCount":0,"filesModified":[]},
 "modelMetrics":{"mai-code-1.1-flash":{
   "requests":{"count":1,"cost":1},
   "usage":{"inputTokens":16144,"outputTokens":9,"cacheReadTokens":0,
            "cacheWriteTokens":0,"reasoningTokens":0},
   "totalNanoAiu":291564000,"tokenDetails":{...}}},
 "agentMetrics":{"main":{...}},
 "currentModel":"mai-code-1.1-flash","lastCallInputTokens":16144,"lastCallOutputTokens":9}
```

Two things follow.

**The token keys line up with `agent-handoff`'s own counters.** `render-cost-receipt.py` uses
`("input", "cache_read", "cache_write", "output", "reasoning")`, and Copilot's `tokenDetails` uses
`input`, `cache_read`, `cache_write`, `output` verbatim, with `modelMetrics.<model>.usage`
contributing `reasoningTokens`. No translation table needed.

**AI credits are a third kind of meter.** `totalPremiumRequestCost: 1` and `totalNanoAiu:
291564000` are neither codex's "runs on a subscription, the CLI reports no number" nor claude's
`total_cost_usd`. Reporting them as a dollar figure would be a fabrication. Report premium requests
and nano-AIU as what they are, measured, and say what meter they belong to.

**[help]** `--max-ai-credits <credits>` caps a session, and `copilot help billing` and `help limits`
document the model.

---

## 10. Read-only and plan mode

**[help]** `--mode plan` and `--plan` start in plan mode; `--mode autopilot` combined with `--plan`
auto-approves the plan and implements it. `--max-autopilot-continues <count>` bounds autopilot
continuation messages, default 5.

**[open]** Whether `--mode plan` is genuinely read-only for a `-p` run, and how it interacts with
`--allow-all-tools`, was not probed. This is the mapping target for a read-only delegated job, so a
planning session should verify it directly rather than trusting the flag name.

---

## 11. Data egress defaults

**[help]** Session export to GitHub web and mobile is **on by default**. The flags that exist are
`--no-remote` ("Disable remote control of your session from GitHub web and mobile") and
`--no-remote-export` ("Disable exporting your session to GitHub web and mobile"), which implies the
positive is the default. `--share[=path]` writes the session to markdown after a non-interactive
run, and `--share-gist` publishes it to a secret GitHub gist.

For a delegated job carrying repository contents and prompt text, that default is a finding, not a
footnote. Pass `--no-remote --no-remote-export`, and never pass `--share` or `--share-gist`.

**[help]** `--secret-env-vars[=vars...]` strips named environment values from shell and MCP
environments and redacts them from output. Worth considering for a delegation wrapper.

---

## 12. Worktrees

**[help]** `--worktree` creates a new worktree and starts the session there. `--add-dir`,
`--plugin-dir`, and relative paths resolve against "the session working directory (the
`--resume`/`--worktree`/`-C` directory)".

Copilot's own worktree flag should not be used by a caller that already owns a worktree protocol.
`agent-handoff` pins each worktree to an immutable base SHA so a verdict names a commit that was
actually tested; handing that lifecycle to the CLI gives up the pinning and the cleanup. Use `-C`
into a caller-created worktree instead.

---

## 13. Implications for agent-handoff (inference, not observation)

Everything above is fact. This section is the reading of it, and a planning session should argue
with it.

### 13.1 The mapping is close to complete

| Abstraction the skill needs | Copilot mechanism | Fit |
|---|---|---|
| Non-interactive submit | `-p "<text>"` | direct |
| `log.jsonl` event stream | `--output-format json` | direct |
| Session id for `resume` | `result.sessionId`, or assign via `--session-id` | direct |
| Fix round on the same session | `--resume <sid>` | direct, verified with context intact |
| Working directory | `-C` fresh, shell cwd on resume | same shape as codex |
| Effort as a routing field | `--effort` | direct, but model-validated |
| Read-only job | `--mode plan` | plausible, unverified |
| Final agent message | `assistant.message` where `phase == "final_answer"` | direct |
| Commands run | `tool.execution_start.data.arguments.command` | direct |
| Denial count and names | `tool.execution_complete` where `error.code == "denied"` | better than the claude branch |
| Token counters | `tokenDetails` plus `modelMetrics.*.usage.reasoningTokens` | key names already match |
| Transcript rendering | drop `ephemeral: true`, fold `tool.execution_start`/`_complete` by `toolCallId` | same folding the viewer already does by `item.id` and `tool_use_id` |

### 13.2 What genuinely does not fit

**`model = "auto"` conflicts with the identity abstraction, on two counts.** Mechanically, `auto`
plus any effort is rejected, and every configured identity is required to carry a non-empty effort.
Conceptually, an identity is a deliberate `backend + model + effort` choice, and the flow's own rule
is that the driver never re-decides routing per run. `auto` hands that choice back to the vendor.
Refusing `auto` for a configured identity is the smaller change and the more consistent one, but it
is a decision to take rather than assume.

**The receipt schema is shaped for two backends.** v5 hardcodes `codex_jobs`/`codex_job_durations`
and `cc_jobs`/`cc_job_durations` across `docs/receipt-schema.json`, `make-receipt.py`,
`validate-receipt.py`, `render-cost-receipt.py`, and `roles_used[].host` (`claude_code|codex`). A
third backend forces a choice: add a third pair, or generalise to one backend-keyed field. Three is
where a pattern becomes a rule, so the question is whether a fourth backend is plausible.

**Discovery cannot follow the existing pattern.** The setup UI derives models from `codex
model/list` and `claude --help`. Copilot offers neither: no subcommand, and `availableModels` only
after a run. Either the wizard runs a probe job, or it accepts a typed model and validates it at
first use.

**Binary identity has to be probed.** See section 2. This is the only piece of the integration with
no analogue in the existing code.

### 13.3 Open questions for the planning session

1. Does a repo verification script run under the built-in baseline, or only under
   `--allow-all-tools`? (section 8)
2. Is `--mode plan` read-only in a `-p` run? (section 10)
3. Assign the session id with `--session-id` at submit, or extract `result.sessionId` at the end?
   Assigning means the id exists before the job finishes.
4. Receipt schema: third pair, or backend-keyed generalisation?
5. Refuse `model = "auto"` for a configured identity, or support it with an effort sentinel?
6. How are AI credits reported in the cost receipt without inventing a currency figure?
7. Does `--effort` need a per-model validity probe, or is surfacing the API's 400 sufficient?

---

## 14. Corrections to the previous version of this document

The previous version was written from published documentation. These claims were wrong:

| Previous claim | Reality |
|---|---|
| Use `-s` plain stdout; treat JSON as a later separate mode; "do not make the first integration dependent on JSON event parsing" | Backwards for this consumer. Without the JSONL stream there is no session id, so no resume and no fix rounds, no denial detection, no token counters, and no transcript. `--output-format json` is the load-bearing flag, not an optional upgrade. |
| Effort values are `low`, `medium`, `high`, `xhigh`, `max`, with a synthetic `default` that omits the flag | The CLI accepts `none` and `minimal` too. `none` is a real value the API can reject, not an "omit the flag" sentinel. |
| `auto` is a valid model choice, distinct from omitting `--model` | True, but `auto` plus any effort is rejected. The previous version never noticed the interaction. |
| Never use `--allow-all-tools`; build a curated allow list from repository stack detection | The CLI's own help says `--allow-all-tools` is required for non-interactive mode, and a curated list in a background job denies silently at exit 0. See the decision in section 8. |
| Derive the model catalog by parsing `copilot help` for `--model` values, cached against `copilot --version` | `copilot help` advertises no model values. On a machine where the name resolves to AWS Copilot, both commands succeed against the wrong binary and the parse silently yields nothing. |
| `CopilotErrorCode` taxonomy inferred from nothing observed | The real surfaces are a plain-text stderr line for CLI-layer rejections and a `session.error` event with `statusCode` for API-layer ones. Two layers, different shapes. |
| Nothing about data egress | Session export to GitHub web and mobile is on by default. |
| Nothing about the binary name collision | `copilot` also ships with AWS Copilot CLI, and PATH order decides. |
| Node/TypeScript interfaces and `spawn(..., {shell: false})` examples | The consumer is a bash delegation script writing an audited `run.sh`. Kept only as intent: build an argument array, never a concatenated shell string. |

Retained and confirmed: `-p` for non-interactive execution, `-C` for the repository root, explicit
`--model`, `auto` as a supported value, no hardcoded model list, no `--yolo`, do not silently change
model or downgrade effort, do not broaden permissions after a failure, and preserve Copilot's
original error information.

---

## 15. Probe log

Six `-p` runs on 2026-09-09, GitHub Copilot CLI 1.0.83, macOS, scratch git repo:

1. No-tool smoke, `--model auto`, `--output-format json`, `--usage-output-file`. Exit 0, 21 events,
   `result.sessionId` present, usage file written.
2. `--model auto --effort medium`. Exit 1, CLI-layer stderr error, no session.
3. Tool-using run with `--allow-tool write`, `--allow-tool 'shell(git:*)'`, `--deny-tool
   'shell(curl)'`, `--model mai-code-1.1-flash --effort medium`. Exit 0, file created, `curl`
   denied with `error.code: "denied"`.
4. `--resume <sid>` of run 3. Exit 0, recalled the filename from the prior turn, same `sessionId`.
5. `--allow-tool write` only, asked to run `echo`. Exit 0, `echo` ran.
6. `--model auto --effort none`. Exit 1, `session.error` 400, `result` with `exitCode: 1`, empty
   stderr.

Plus: `--model definitely-not-a-model` rejected at the CLI layer; `copilot model` and `copilot
models` are not commands; `which -a copilot` resolved two different tools.

---

## References

1. [GitHub Docs: Copilot CLI programmatic reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-programmatic-reference)
2. [GitHub Docs: Copilot CLI command reference](https://docs.github.com/en/copilot/reference/copilot-cli-reference/cli-command-reference)
3. [GitHub Docs: Allowing and denying tool use](https://docs.github.com/en/copilot/how-tos/copilot-cli/use-copilot-cli/allowing-tools)
4. [GitHub Docs: Running Copilot CLI programmatically](https://docs.github.com/en/copilot/how-tos/copilot-cli/automate-copilot-cli/run-cli-programmatically)
5. [GitHub Docs: Configuring Copilot CLI](https://docs.github.com/en/copilot/how-tos/copilot-cli/set-up-copilot-cli/configure-copilot-cli)
6. `copilot --help`, GitHub Copilot CLI 1.0.83 — the authority where it disagrees with the above.
