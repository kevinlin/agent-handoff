# Antigravity CLI Integration Specification

## Status and provenance

**[probed] (VERSION, BINARY, MODERESUME, MCLAUDE; timestamps in each probe's `meta.json`)** Written from Antigravity CLI `agy` **1.3.5**, macOS **26.7.1**, Darwin **25.6.0**, arm64. The 55 evidence records span **2026-10-14 13:44:40 to 13:54:17 UTC** (21:44:40 to 21:54:17 Singapore time). There were 39 ordinary print invocations, including rejected invocations, plus free discovery/readout commands and two source downloads. Exactly one invocation selected Claude Sonnet; it failed model/effort validation before an agent turn (MCLAUDE).

**[probed] (SETTINGS, SETTINGS_AFTER)** Only permission-, mode-, sandbox-, review-, and trust-related settings were extracted from settings files. In `~/.gemini/antigravity-cli/settings.json`, the only matching key was `trustedWorkspaces`, an array containing two existing project paths. Neither was a probe workspace. No matching permission, mode, sandbox, or artifact-review override was present. `~/.gemini/config/config.json` and `~/.gemini/config/settings.json` were absent. These filtered observations were unchanged at the second sample. The probes made no settings edits.

| Setting or effective field | Observed value and provenance |
|---|---|
| `trustedWorkspaces` | Two existing projects; no temporary probe path **[probed] (SETTINGS, SETTINGS_AFTER)** |
| `init.permission_mode` | `request-review` with no posture flag, accept-edits, auto fallback, and plan **[probed] (P0, P1, P2, P5, RO1)** |
| `init.permission_mode` with skipped permissions | `always-proceed`, including sandbox and plan pairings **[probed] (P3, P4, RO2)** |
| Auto entitlement | Unavailable for this account; warning and default-mode fallback **[probed] (P2, P2R, P5)** |
| Other inherited/server policy | **[open]** Not exposed by the permitted settings-file inspection; no policy was changed to isolate defaults |

Provenance tags:

- **[probed]** Direct observation, with the probe id naming `.handoff/probes/agy/<id>/`.
- **[help]** Local `agy --help` (HELP).
- **[changelog]** Local full release-note output (CHANGELOG), with the relevant version.
- **[docs]** A fetched primary source, linked with its retrieval date.
- **[open]** Not established, with the reason.

Sections 1 to 9 record CLI behavior. Section 10 labels design inferences. Section 11 indexes all evidence records.

**[probed] (P0-P5, RO1, RO2; their `cmd.txt` and `after.txt`)** Each posture run used a fresh temporary Git repository outside this checkout: committed `tracked.txt` containing `original`, committed `check.sh` that writes `marker.txt` and prints `CHECK_OK`, and sibling `outside.txt` containing `OUTSIDE_READ_OK`. Python's `tempfile.mkdtemp` provided the equivalent of `mktemp -d`. Each command ran inside its temporary repository with stdin from `/dev/null`, supervised for 240 seconds in a separate process group. No outer timeout expired. Each evidence directory contains `cmd.txt`, `log.jsonl`, `stderr.log`, `exit`, `after.txt`, and timestamped `meta.json`. `after.txt` records Git status, file existence/content, workspace entries, outside writes, and the last full commit message. Free-command stdout also occupies `log.jsonl`, even when it is plain text.

## 1. Binary and installation

**[probed] (BINARY)** `type -a agy` printed the same `/Users/keli/.local/bin/agy` path twice. It is a regular Mach-O arm64 executable, with **no symlink chain**. `antigravity` was not on PATH. This verifies this machine's resolution, not every installation method.

**[probed] (VERSION, HELP, CHANGELOG)** `agy --version` printed only `1.3.5` to stdout, exit 0, empty stderr. Help printed `Usage of agy:` and its flags/subcommands to stderr, exit 0; it contained no `Antigravity` product string. The free `agy changelog` output contained `Antigravity CLI` in its release notes, providing a product string absent from version/help.

**[docs] (retrieved 2026-10-14)** Homebrew's current [CLI cask](https://github.com/Homebrew/homebrew-cask/blob/master/Casks/a/antigravity-cli.rb) installs an archive member named `antigravity` as `agy`. Its current [app cask](https://github.com/Homebrew/homebrew-cask/blob/master/Casks/a/antigravity.rb) installs the app without that binary alias. Source snapshots are in DOCS_CLI and DOCS_IDE. An [earlier Homebrew installation report](https://github.com/Homebrew/homebrew-cask/issues/246183) records the **IDE launcher** `Antigravity.app/Contents/Resources/app/bin/antigravity` being linked as `/opt/homebrew/bin/agy`. The short name therefore has a plausible launcher collision, even though that launcher was absent from this PATH.

**[open]** No exhaustive audit of unrelated packages named `agy`, binary signing, or installer provenance was performed. A name or numeric version alone does not establish the worker protocol.

## 2. Non-interactive invocation

**[help] (HELP)** `-p`, `--print`, and `--prompt` run a single non-interactive prompt. Output formats are `text`, `json`, and `stream-json`. Input formats are `text` and `stream-json`; the latter requires stream-json output. Help gives `--print-timeout` a default of `0s`, meaning wait until completion. `--conversation <id>` resumes an id; `-c`/`--continue` selects the most recent conversation.

**[probed] (P0-P5, RO1, RO2)** The ordinary base command was:

```text
agy -p "<prompt>" --output-format stream-json --model gemini-3.8-flash-low <posture flags> </dev/null
```

**[probed] (P0, P1, P2R, RO1R)** An approval-required tool auto-denied rather than waiting for stdin. The turn stopped at that action, despite the prompt asking it to attempt later steps. Exit 0 and `SUCCESS` did not mean the task finished.

**[probed] (TIMEOUTR)** `--print-timeout 2s` on the five-step task printed a timeout warning on stderr, then returned **exit 0, `SUCCESS`, empty response, zero usage**, with no tool attempted. The first attempt (TIMEOUT) overlapped an API-capacity error and returned `ERROR` with the same timeout warning and exit 0. These are distinct outcomes, not a stable timeout status.

**[probed] (DRV_PT0)** An explicit `--print-timeout 0s` is accepted and the run completes normally.

**[changelog] (1.1.28, CHANGELOG)** Print timeouts deliberately return partial output and exit successfully. **[changelog] (1.2.6, CHANGELOG)** The default headless timeout changed to unlimited. **[docs] (retrieved 2026-10-14)** The [headless page](https://antigravity.google/docs/cli/headless/) still describes a five-minute default; local help and the later changelog disagree with it.

**[probed] (INTERACTIVE2)** Supplying both `-p <prompt>` and `-i hello` exited 2 with no stdout and `error: --print and --prompt-interactive are mutually exclusive`. A bare `-i` instead failed earlier with `flag needs an argument: -i` and usage text (INTERACTIVE). **[changelog] (1.3.4, CHANGELOG)** The conflict's exit 2 matches the stated fix.

**[open]** Streaming stdin, multiple prompts in one process, structured schemas, project overrides, and remote connections were outside this matrix.

## 3. The stream-json event stream

### 3.1 Vocabulary

**[probed] (P3, RO1, RCWD, MINSPECT)** Across the collected streams, the complete observed vocabulary was:

| Discriminator | Values | Probe evidence |
|---|---|---|
| `event` | `init`, `step_update`, `result`, `command_result` | **[probed] (P3, MINSPECT)** |
| `step_type` | `user_input`, `agent_response`, `tool`, `system_message`, `error_message` | **[probed] (P3, RCWD, RO1)** |
| `state` | `ACTIVE`, `DONE`, `ERROR` | **[probed] (P3, RO1)** |
| `result.status` | `SUCCESS`, `ERROR` | **[probed] (P3, RO1, MUNKNOWN)** |

**[probed] (P3, KILL, RKILL, MUNKNOWN)** Ordinary agent-turn stdout was JSONL, including the complete lines left by SIGKILL. Free discovery commands can produce text or a non-event JSON envelope instead (MODELS, MJSONGLOBAL). No other event, step type, or state was observed in the evidence indexed below. **[open]** This is an observed vocabulary, not a guarantee that other workloads cannot emit more categories.

### 3.2 `init`

**[probed] (P0)** The first line of a created agent session was `init`. The following JSON is trimmed: temporary paths and ids are replaced by placeholders, and the 60-name tool array is omitted. Full original lines remain in each log.

```json
{"event":"init","conversation_id":"<id>","init":{"model":"gemini-3.8-flash-low","cwd":"<repo>","permission_mode":"request-review"}}
```

**[probed] (P0, MBASE, MDEFAULT, RO2)** The tool catalogue contained 60 names, including `run_command`, `write_to_file`, `view_file`, `ask_permission`, `ask_question`, and `finish`. `model` reflected the **literal supplied selector**: MBASE reported `gemini-3.8-flash`, not its resolved low slug. With no model flag it was absent (MDEFAULT). Neither effort nor execution mode had its own init field. Plan added `expanded_commands:[{"name":"plan","type":"system"}]` (RO2).

### 3.3 `step_update` by `step_type`

**[probed] (P0, P3)** Payloads are nested under `step_update`. `user_input` emitted a `DONE` transition without prompt text. Agent responses emitted incremental `text_delta` fragments on `ACTIVE`, and a final `DONE` carrying duration and usage; that last delta could be just a newline. Some tool-leading responses had no text at all (KILL).

```json
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":0,"state":"DONE","step_type":"user_input"}}
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":1,"state":"ACTIVE","step_type":"agent_response","text_delta":"Step 1: Editing `tracked.txt` to contain `probe-edit`."}}
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":1,"state":"DONE","step_type":"agent_response","text_delta":"\n","duration_seconds":5.4452110000000005,"usage":{"input_tokens":11943,"output_tokens":130,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":12073}}}
```

**[probed] (RSAME, RO1)** `system_message` appeared on resumed turns, with `DONE` and duration but no message text. `error_message` appeared during API failure, also without error text. The API explanation was in `result.error`/stderr, not that step.

```json
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":3,"state":"DONE","step_type":"system_message","duration_seconds":0.000285}}
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":1,"state":"DONE","step_type":"error_message","duration_seconds":0}}
```

### 3.4 Tool calls and shell output

**[probed] (P3, WORKTREE)** Tools used were `write_to_file`, `view_file`, `replace_file_content`, and `run_command`. Calls paired by `step_index`, with `ACTIVE` and then `DONE` or `ERROR`. There was no separate call-id field in these samples. `tool_info.parameters` was a reduced view: shell calls exposed `CommandLine`, writes exposed `TargetFile`, and reads exposed `AbsolutePath`. File content being written and the full shell execution arguments were not exposed.

**[probed] (P3)** A successful shell call:

```json
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":4,"state":"ACTIVE","step_type":"tool","tool_name":"run_command","tool_info":{"name":"run_command","parameters":{"CommandLine":"bash check.sh"}}}}
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":4,"state":"DONE","step_type":"tool","tool_name":"run_command","duration_seconds":0.297597,"tool_info":{"name":"run_command","parameters":{"CommandLine":"bash check.sh"},"output":"CHECK_OK\r\n"}}}
```

**[probed] (EXIT7, P4, RO2)** Shell output was a single `output` string with CRLF, not separate stdout/stderr. No structured shell exit-code field appeared. A deliberate `exit 7` still produced `state:DONE`, output `EXIT_PROBE`, and no `tool_info.error`; only the agent's later text reported 7. Sandbox errors likewise appeared as command output with `DONE`. A successful silent shell write could omit `output` entirely (RO2).

```json
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":2,"state":"DONE","step_type":"tool","tool_name":"run_command","duration_seconds":0.059753,"tool_info":{"name":"run_command","parameters":{"CommandLine":"printf \"EXIT_PROBE\\n\"; exit 7"},"output":"EXIT_PROBE\r\n"}}}
```

### 3.5 Permission requests and denials

**[probed] (P0, P1, P2R, RO1R)** Approval-required tools were auto-denied in print mode. The reliable explicit refusal record was **`result.denied_actions`**, containing `action` and `display_name`. The same refused write sometimes ended as `DONE` without an error (P0), and sometimes as `ERROR` with `tool_info.error.type:TOOL_ERROR` and an explanatory message (P2R). A parser relying only on `state:ERROR` would miss P0's denial.

```json
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":2,"state":"DONE","step_type":"tool","tool_name":"write_to_file","duration_seconds":0.234659,"tool_info":{"name":"write_to_file","parameters":{"TargetFile":"<repo>/tracked.txt"}}}}
```

**[probed] (P2R)** Trimmed alternate denial shape:

```json
{"event":"step_update","step_update":{"conversation_id":"<id>","step_index":2,"state":"ERROR","step_type":"tool","tool_name":"write_to_file","tool_info":{"name":"write_to_file","parameters":{"TargetFile":"<repo>/tracked.txt"},"error":{"type":"TOOL_ERROR","message":"permission check failed for write_file ...: user denied permission ..."}}}}
```

**[probed] (RO1R, MODERESUME)** When no response text was produced, stderr explained that headless mode cannot prompt and the action was auto-denied, naming the relevant permission. P0/P1 had narration text and empty stderr, so that notice was not universal.

**[open]** No interactive pending permission request or waiting event was captured. These probes had closed stdin and auto-denied approval-required actions; no separate permission-request event appeared.

### 3.6 `result` and final message

**[probed] (P0)** A refused edit still terminated as:

```json
{"event":"result","result":{"conversation_id":"<id>","status":"SUCCESS","response":"Step 1: Editing `tracked.txt` to contain `probe-edit`.\n","duration_seconds":5.477924,"num_turns":1,"usage":{"input_tokens":11943,"output_tokens":130,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":12073},"denied_actions":[{"action":"write_file","display_name":"WriteToFile"}]}}
```

**[probed] (P3, P4)** `result.response` concatenated **all agent-response text from the current invocation**, including between-tool narration and the final report. In P3, concatenating every agent-response delta exactly reproduced its 1,236-character result; the last response step alone was 949 characters. It was neither only the last message nor the entire session history (RSAME returned only the remembered token).

**[probed] (P3, RSAME, RO2R)** Usage had five snake_case counters: `input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens`. Nonzero thinking appeared on unpinned resume (RSAME), and cache reads appeared in RO2R. No credit, monetary cost, price, or remaining-quota field appeared in the collected event objects. This absence does not establish that the runs are free. **[open]** Billing/credits consumed were not measured.

**[probed] (TIMEOUTR)** A timeout result can contain no error:

```json
{"event":"result","result":{"conversation_id":"<id>","status":"SUCCESS","response":"","duration_seconds":0,"num_turns":1,"usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":0}}}
```

### 3.7 Errors

**[probed] (MCONFLICT, MMAX, MCLAUDE, MUNKNOWN)** Model validation failed **before init**, emitted one `result` event with an empty conversation id, `ERROR`, zero turns/usage/duration, and a descriptive error. Exit was 1, with the same error on stderr. A pre-session error therefore has no id available from line one.

```json
{"event":"result","result":{"conversation_id":"","status":"ERROR","response":"","error":"invalid model selection (--model \"gemini-3.8-flash-low\" --effort \"high\"): --model gemini-3.8-flash-low conflicts with --effort=high","duration_seconds":0,"num_turns":0,"usage":{"input_tokens":0,"output_tokens":0,"thinking_tokens":0,"cache_read_tokens":0,"total_tokens":0}}}
```

**[probed] (RO1)** An API-capacity failure occurred naturally. It exited 3, with `ERROR`, a real conversation id, prior nonzero usage, and the earlier denied action:

```json
{"event":"result","result":{"conversation_id":"<id>","status":"ERROR","response":"","error":"API error (attempt 1): RESOURCE_EXHAUSTED (code 429): You have exhausted your capacity on this model. Resets in 0s.","duration_seconds":36.923777,"num_turns":1,"usage":{"input_tokens":25867,"output_tokens":413,"thinking_tokens":216,"cache_read_tokens":0,"total_tokens":26280},"denied_actions":[{"action":"write_file","display_name":"WriteToFile"}]}}
```

**[probed] (RO1)** Stderr also carried `AGY_ERROR: {"short_error":"...","retryable":false,"error_id":"..."}`. TIMEOUT encountered the same capacity text but returned exit 0 under its short timeout. **[changelog] (1.2.6, 1.2.10, CHANGELOG)** The release notes describe API/agent failures as exit 3 with `AGY_ERROR`. The timeout case prevents treating exit 3 as universal.

**[open]** Authentication expiry, an individual-account quota failure, and cancellation statuses were not provoked. The observed 429 explicitly named **model capacity**, which does not prove the individual-quota path. **[changelog] (1.3.3, CHANGELOG)** The notes name the expiry message `Authentication expired. Please log back in.` and immediate individual-quota failures.

### 3.8 Free command events

**[probed] (MINSPECT, EINSPECT, RMODEL, REFFORT)** Read-only `/model` and `/effort` print commands emitted `command_result` followed by `result`, without init, a conversation id, an agent turn, or token usage. Supplying `--conversation` did not give the command result an id.

```json
{"event":"command_result","command":{"name":"effort","data":{"adjustable":true,"current":"low","available":["low","medium","high"]}}}
```

## 4. Session id and resume

**[probed] (P3, KILL, RKILL)** Every event in these created agent sessions carried the conversation id: top-level on init, nested on `step_update` and `result`. It was available from the first line even when the run died during its first tool. **[probed] (MUNKNOWN, MINSPECT)** Pre-session errors and free readouts are exceptions: their result id was empty, and `command_result` had no id.

**[probed] (RPARENT, RSAME)** `--conversation <id>` in the same cwd recalled `RESUME-TOKEN-7281`. Step indices continued from the saved history, and `num_turns` increased from 1 to 2.

**[probed] (RPARENT, RSAME)** Usage is **cumulative over the conversation**, not per invocation. Parent usage was 11,846 input / 1 output; the resumed response step added 12,143 input / 99 output; its result reported exactly 23,989 input / 100 output. **[probed] (RMODE)** Duration is also a session measure spanning gaps: this process took 7.362 seconds, while `result.duration_seconds` was 130.436445. It is unsuitable as this job invocation's elapsed time.

**[probed] (KILL, RKILL)** The supervisor polled the log every 50 ms and sent SIGKILL to the process group on the first `tool/ACTIVE` (`sleep 30; printf ...`). Exit 137 left four complete JSON lines and no result. Resuming the recorded id recalled `KILL-TOKEN-4417`, with two turns and cumulative usage. This proves persistence during a first tool call, not persistence before init.

**[probed] (RUNKNOWN)** A never-existing UUID produced `warning: conversation "<requested id>" not found`, then ran normally, exit 0. Init/result used a **different fresh id**, not the requested id. It starts new context with a warning rather than failing.

**[probed] (MODEPARENT, MODERESUME, P1)** An accept-edits parent resumed in the same cwd without mode/model/effort flags auto-denied a direct write to that same workspace. Thus the parent's accept-edits behavior did not carry over. **[probed] (KILL, RKILL)** Skipped permissions also did not carry: init changed from `always-proceed` to `request-review`.

**[probed] (RPARENT, RSAME, RMODEL, REFFORT, MINSPECT, EINSPECT)** The low-effort parent had an explicit base selector. Unpinned resume omitted model and effort from init. Free readouts with that conversation id reported `gemini-3.8-flash-high` / `high`; explicit base-plus-low readouts reported the low slug / `low`. These process selection reports did not retain the parent's low override. **[open]** Init cannot independently identify the engine's resolved model/effort on the earlier unpinned resumed turns; the free readout is not engine telemetry for those turns.

**[probed] (RCWD, RCWD2, CWDPARENT, CWDRESUME)** Init always reported the new process cwd, but actual shell cwd was inconsistent. RCWD2's command was `pwd && cat tracked.txt` without a directory change; its output named the **earlier RCWD workspace**, despite init naming RCWD2's fresh repo. A confirming fresh pair used distinguishable `CWD_A`/`CWD_B` files: CWDRESUME ran in the new B cwd and read `CWD_B`. **[open]** The cause of the stale workspace in the longer resumed history was not isolated. Neither init alone nor the successful fresh pair establishes reliable cwd rebinding for every history.

## 5. Models and effort

**[probed] (MODELS)** `agy models` took 3.897 seconds, exit 0. Stdout had exactly these six tab-separated records; stderr had only `Fetching available models...`:

| Slug | Display name |
|---|---|
| `gemini-3.8-flash-high` | Gemini 3.8 Flash (High) |
| `gemini-3.8-flash-medium` | Gemini 3.8 Flash (Medium) |
| `gemini-3.8-flash-low` | Gemini 3.8 Flash (Low) |
| `claude-sonnet-4-6` | Claude Sonnet 4.6 (Thinking) |
| `claude-opus-4-6-thinking` | Claude Opus 4.6 (Thinking) |
| `gpt-oss-120b-medium` | GPT-OSS 120B (Medium) |

**[open]** Whether model discovery requires login was not tested: the account was left signed in. **[probed] (MJSONGLOBAL, MODELJSON, MSTREAM)** Flag position matters: `agy --output-format json models` worked and returned `command.data.models` with id/label objects, zero usage, in 1.676 seconds. Putting either output-format flag **after** `models` was rejected, exit 1, no stdout. **[changelog] (1.1.12, CHANGELOG)** The notes advertise machine-readable models output; the global flag placement is the working form observed here.

**[help] (HELP)** The effort vocabulary is `low|medium|high|xhigh|max`. **[changelog] (1.3.4, CHANGELOG)** Base selectors without effort now resolve to the model's default level, otherwise medium, otherwise the lowest available. That fallback order was not independently probed **[open]**.

| Selection | Outcome and provenance |
|---|---|
| `gemini-3.8-flash` + `--effort low` | Exit 0, reply `ok`; init retains the base spelling. Free readout resolves the same arguments to `gemini-3.8-flash-low` **[probed] (MBASE, MINSPECT)** |
| `gemini-3.8-flash-low` + `--effort high` | Exit 1 before init; explicit conflict, neither wins **[probed] (MCONFLICT)** |
| Low slug + `--effort max` | Exit 1 before init; explicit conflict **[probed] (MMAX)** |
| Flash base + `--effort max` | Exit 1; no max variant, available levels low/medium/high **[probed] (MMAXBASE)** |
| `claude-sonnet-4-6` + `--effort high` | Exit 1 before init; effort not supported for this model. This was the only Sonnet invocation **[probed] (MCLAUDE)** |
| `no-such-model-xyz` | Exit 1, ERROR envelope plus stderr/catalogue, empty id, before init **[probed] (MUNKNOWN)** |
| No model flag | Exit 0, reply `ok`; init **omits model**. No effective default is named there **[probed] (MDEFAULT)** |
| `claude-sonnet-4-6` with no effort | Exit 0, sentinel reply. A catalogue id runs as given, without `--effort` **[probed] (DRV_SONNET)** |
| Free unpinned model/effort readout | Reported Flash high / high. This reports current CLI selection, not a model field in MDEFAULT's stream **[probed] (RMODEL, REFFORT)** |

## 6. Permissions and sandbox

**[help] (HELP)** `--mode` accepts `accept-edits`, `auto`, and `plan`. `--dangerously-skip-permissions` auto-approves tool permission requests. `--sandbox` enables terminal restrictions. **[changelog] (1.3.5, CHANGELOG)** Auto approves edits/safe commands, requests review for irreversible actions, and uses a separate reviewer for commands leaving the terminal sandbox. **[open]** That Auto policy could not be exercised because this account lacks access.

**[probed] (P0-P5, P2R, P4R)** The five-step prompt asked, in order, for an edit, `bash check.sh`, `cat ../outside.txt`, `curl -sI https://example.com`, and `git add tracked.txt && git commit -m probe`, with narration and an honest report. Sandbox rows added a shell write to `../outside-write.txt`. `Not reached` means the CLI stopped earlier, not that the later operation was independently refused. All rows below are observations under this account's policies.

| Probe | Flags beyond base | Edit | Check | Outside read | Network | Commit | Outside write | Exit; final claim |
|---|---|---|---|---|---|---|---|---|
| **[probed] P0** | none | auto-denied | not reached | not reached | not reached | not reached | not requested | 0; only edit-intent narration, no completion claim |
| **[probed] P1** | `--mode accept-edits` | landed | auto-denied; no marker | not reached | not reached | not reached | not requested | 0; edit/check intent narration, no final report |
| **[probed] P2, P2R** | `--mode auto` | auto-denied | not reached | not reached | not reached | not reached | not requested | 0; auto unavailable warning; edit intent only |
| **[probed] P3** | `--dangerously-skip-permissions` | landed | ran; marker exists | ran; expected text | HTTP 200 | landed | not requested | 0; reports all five succeeded, consistent with evidence |
| **[probed] P4, P4R** | `--sandbox --dangerously-skip-permissions` | file-tool edit landed | ran, but marker write blocked | shell ran; OS blocked read | HTTP 200 | shell ran; global Git config read blocked; no commit | OS blocked; absent | 0; reports failures accurately; distinguishes check exit 0 from failed marker write |
| **[probed] P5** | `--sandbox --mode auto` | auto-denied | not reached | not reached | not reached | not reached | not reached | 0; auto unavailable warning; edit intent only |

**[probed] (P4, P4R, SCANON)** The terminal sandbox confined the sibling write and read, but also blocked the **in-repo** marker write. The fixture intentionally does not stop after its first error, so it still printed `CHECK_OK` and the agent reported exit 0. Launching from the canonical `/private/...` cwd (SCANON), rather than its `/var/...` alias, reproduced the failed marker write. This was not resolved by canonicalizing the launch path.

**[probed] (P4, P4R)** Commit attempts failed with `fatal: unable to access '/Users/keli/.gitconfig': Operation not permitted`; no agent commit existed. The global Git file was not read or quoted. **[open]** Whether a different machine/configuration, or a launch that excludes global Git configuration, permits sandboxed commits was not tested.

**[probed] (P4, P4R, SCANON)** `curl` reached example.com and returned HTTP 200 inside the sandbox. Thus this sandbox is not network-off for this destination. The stream had no typed permission denials for the shell's OS restrictions. **[open]** Other domains, network rules, tool-level outside writes, and the sandbox's complete confinement boundary were not tested.

**[docs] (retrieved 2026-10-14)** The [headless page](https://antigravity.google/docs/cli/headless/) says workspace file reads/writes are auto-allowed and approval-required actions soft-deny. **[probed] (P0)** The workspace write was instead auto-denied in default mode on this account; it is unsafe to treat that documentation sentence as the measured default.

## 7. Read-only (plan mode)

**[probed] (RO1, RO1R, RO2, RO2R)** Both variants were asked to read `tracked.txt`, create `new.txt`, and run `echo x > shell.txt`. Init recorded expansion of the system `plan` command, not a separate execution-mode field.

| Probe | Flags | Read | File write | Shell write | Exit | Claims versus disk |
|---|---|---|---|---|---|---|
| **[probed] RO1** | `--mode plan` | ran | attempted; auto-denied | not reached | 3 | empty response; later natural capacity failure; no files created |
| **[probed] RO1R** | `--mode plan` | ran | attempted; auto-denied | not reached | 0 | empty response and denial notice; no files created |
| **[probed] RO2, RO2R** | `--mode plan --dangerously-skip-permissions` | ran | **new.txt exists** | **shell.txt exists** | 0 | reported real writes; no false claims or typed denials |

**[probed] (RO2, RO2R)** Plan plus skipped permissions is **not read-only** in these runs. Unlike the supplied Copilot comparison, the claimed writes actually landed. No probe hung waiting for plan review, and no implementation-plan review artifact was emitted in these short tasks.

**[changelog] (1.3.2, CHANGELOG)** Skipped tool permissions no longer auto-approve implementation plans in plan mode. **[changelog] (1.1.28, CHANGELOG)** Earlier notes also say headless runs proceed through plan review automatically. **[open]** These probes did not emit an implementation plan requiring review, so they do not resolve that interaction; they do establish the absence of an enforced file/shell write barrier.

## 8. Authentication and the configuration directory

**[probed] (ENV, SETTINGS)** The installed CLI successfully replied `ok` with `ANTHROPIC_API_KEY=bogus` and `CLAUDE_CODE_ENTRYPOINT=bogus`; stderr stayed empty. These two variables did not break the Gemini invocation. Init exposed no authentication-source field. The CLI settings file was under `~/.gemini/antigravity-cli/`; only the permitted policy keys were inspected.

**[docs] (retrieved 2026-10-14)** The [headless documentation](https://antigravity.google/docs/cli/headless/) says print mode uses cached credentials and a truly non-interactive unauthenticated process fails rather than waiting for login. **[open]** Credential storage, auth mechanism, unauthenticated exit/error shape, expiry behavior, and model-list behavior without login were not verified. No credential/token file was opened or copied, and the account was not logged out.

**[changelog] (1.0.4, 1.0.5, CHANGELOG)** The notes describe centralized project mappings at `~/.gemini/antigravity-cli/cache/projects.json` and print metadata in that cache, rather than workspace-local metadata. **[open]** Those files and conversation databases were not inspected; the workspace findings below rely on disk snapshots.

## 9. Workspace side effects and commit attribution

**[probed] (P0-P5, RO1, RO2, WORKTREE; all `after.txt` records in section 11)** Every after-snapshot contained only the fixtures and explicitly requested in-workspace files. No `.agents/`, `.antigravity/`, workspace task list, plan, or other generated directory appeared. This observation covers these short tasks, not tasks that request artifacts.

**[probed] (WORKTREE)** A fresh `git worktree add` path ran without a trust prompt or refusal. It edited `tracked.txt`, ran the marker-producing check, and committed successfully with skipped permissions. Init named the worktree cwd. The main temporary repo remained a separate checkout.

**[probed] (P3, WORKTREE)** Successful agent commits had the full message **`probe`**, with no attribution trailer. The streamed command was exactly `git add tracked.txt && git commit -m probe`. Sandbox runs made no commit (P4, P4R). **[open]** Attribution under other account settings was not tested; no attribution setting was read because the settings inspection was limited to the user's allowed policy keys.

## 10. Implications for agent-handoff (inference, not observation)

1. **Inference, based on [probed] (VERSION, HELP, CHANGELOG) and [docs] (Homebrew sources in section 1):** Validate the expected print/stream flags and a product-bearing free command. Numeric version and the name `agy` alone can accept a launcher with the same alias.
2. **Inference, based on [probed] (P0, P1, P2R, TIMEOUTR, RO1):** Treat process exit, result status, denied actions, timeout stderr, and task verification as distinct inputs. Exit 0 / SUCCESS alone accepts unfinished tasks, denials, and empty timeout output.
3. **Inference, based on [probed] (P3, RO1R, MINSPECT, MUNKNOWN):** Add a backend-specific parser for nested payloads; tolerate result-before-init and empty ids. Parse `denied_actions` even when tool state is DONE. Do not assume every free command emits an agent-session stream.
4. **Inference, based on [probed] (P3, EXIT7):** Use the last reconstructed `agent_response` step when a final-message-only value is needed. Shell output is visible, but structured shell exit codes are not; command checks need their own durable evidence.
5. **Inference, based on [probed] (RPARENT, RSAME, RMODE, KILL, RKILL):** Calculate invocation usage by subtracting the prior cumulative counters, and measure job duration locally. A killed parent needs saved per-step usage or a prior-session baseline to avoid counting its usage again.
6. **Inference, based on [probed] (RUNKNOWN, KILL, RKILL):** Require the resumed init id to equal the stored id. Otherwise a fix round may execute in fresh context after only a warning. The first init is sufficient to save a resumable id for the tested kill point.
7. **Inference, based on [probed] (MODERESUME, RKILL, RMODEL, RCWD2, CWDRESUME):** Re-pass the selected posture/model/effort and keep fix rounds in their original cwd. Verify the worker's actual workspace before relying on cross-cwd resume; init alone was insufficient once.
8. **Inference, based on [probed] (MODELS, MJSONGLOBAL, MCONFLICT, MMAXBASE, MCLAUDE):** Discover models live, place machine-output flags before `models`, and validate model/effort as a pair. A global effort vocabulary does not mean every model supports every level, or supports a separate effort flag at all.
9. **Inference, based on [probed] (P2, P2R, P5, P3, P4, SCANON):** Account entitlement must be part of posture validation. Auto silently falls back with a warning here; skipped permissions run checks, while adding sandbox prevents the marker write yet permits the tested HTTPS request.
10. **Inference, based on [probed] (RO1R, RO2, RO2R):** Do not implement Handoff's read-only promise solely through plan mode, particularly paired with skipped permissions. These runs require a separately enforced write barrier before such a promise is supportable.

## 11. Probe log

**[probed] (each id below)** Base for ordinary runs: `agy -p <prompt> --output-format stream-json --model gemini-3.8-flash-low`. Every command used `/dev/null` stdin and the 240-second supervisor; exact prompt, argument order, cwd, and environment are in `cmd.txt`. `No model` means the base model flag was omitted. Free/source/settings commands have no ordinary base. Every row's evidence is under `.handoff/probes/agy/<id>/`.

| # | Probe | Flags beyond the base command | Exit | Observed |
|---|---|---|---|---|
| **[probed] VERSION** | version | `free: --version` | 0 | 1.3.5 only |
| **[probed] HELP** | help | `free: --help` | 0 | help on stderr; no product name |
| **[probed] BINARY** | binary / OS | `free: type, symlink walk, file, OS` | 0 | single regular Mach-O path; no antigravity on PATH |
| **[probed] CHANGELOG** | release notes | `free: changelog` | 0 | complete notes; Antigravity CLI product string |
| **[probed] SETTINGS** | policy keys | `free: filtered settings read` | 0 | only trustedWorkspaces matched; probe paths absent |
| **[probed] SETTINGS_AFTER** | policy keys again | `same filtered read` | 0 | filtered values unchanged |
| **[probed] MODELS** | catalogue | `free: models` | 0 | six TSV records stdout; progress stderr |
| **[probed] MODELJSON** | JSON after subcommand | `free: models --output-format json` | 1 | flag rejected; no stdout |
| **[probed] MSTREAM** | stream flag after subcommand | `free: models --output-format stream-json` | 1 | flag rejected; no stdout |
| **[probed] MJSONGLOBAL** | JSON before subcommand | `free: --output-format json models` | 0 | JSON envelope with command.data.models; zero usage |
| **[probed] P0** | default posture | `none` | 0 | first edit auto-denied; SUCCESS; clean tree |
| **[probed] P1** | accept-edits | `--mode accept-edits` | 0 | edit landed; check auto-denied; no marker |
| **[probed] P2** | auto | `--mode auto` | 0 | entitlement warning; default fallback; edit denied |
| **[probed] P2R** | auto confirmation | `--mode auto` | 0 | same fallback; typed TOOL_ERROR plus denied_actions |
| **[probed] P3** | skip permissions | `--dangerously-skip-permissions` | 0 | all five steps ran; HTTP 200; commit without trailer |
| **[probed] P4** | sandbox plus skip | `--sandbox --dangerously-skip-permissions` | 0 | edit landed; marker/read/commit/outside-write blocked; HTTP 200 |
| **[probed] P4R** | sandbox confirmation | `--sandbox --dangerously-skip-permissions` | 0 | same confinement and in-repo marker failure |
| **[probed] P5** | sandbox plus auto | `--sandbox --mode auto` | 0 | auto unavailable; edit denied; later steps not reached |
| **[probed] SCANON** | canonical sandbox cwd | `--sandbox --dangerously-skip-permissions; canonical cwd` | 0 | marker and outside write still blocked; HTTP 200 |
| **[probed] RO1** | plan | `--mode plan` | 3 | read ran; file write denied; natural capacity error |
| **[probed] RO1R** | plan confirmation | `--mode plan` | 0 | read ran; file write denied; empty SUCCESS |
| **[probed] RO2** | plan plus skip | `--mode plan --dangerously-skip-permissions` | 0 | new.txt and shell.txt written; honest report |
| **[probed] RO2R** | plan plus skip confirmation | `same as RO2` | 0 | both writes landed again; no denials |
| **[probed] TIMEOUT** | short timeout | `--print-timeout 2s` | 0 | timeout warning; concurrent capacity ERROR; process exit 0 |
| **[probed] TIMEOUTR** | timeout confirmation | `--print-timeout 2s` | 0 | empty SUCCESS, zero usage, timeout warning; no tools |
| **[probed] EXIT7** | nonzero shell | `--dangerously-skip-permissions` | 0 | shell DONE despite exit 7; code only in agent text |
| **[probed] INTERACTIVE** | valueless interactive flag | `-i (no value)` | 2 | missing argument error and usage; no events |
| **[probed] INTERACTIVE2** | print / interactive conflict | `-i hello` | 2 | mutually-exclusive error; no events |
| **[probed] MBASE** | base and low | `--model gemini-3.8-flash --effort low` | 0 | ok; init echoes base selector |
| **[probed] MCONFLICT** | conflicting slug effort | `--effort high` | 1 | pre-session ERROR; explicit conflict |
| **[probed] MMAX** | max on low slug | `--effort max` | 1 | pre-session ERROR; explicit conflict |
| **[probed] MMAXBASE** | max on base | `--model gemini-3.8-flash --effort max` | 1 | pre-session ERROR; only low/medium/high available |
| **[probed] MCLAUDE** | Sonnet / high | `--model claude-sonnet-4-6 --effort high` | 1 | pre-session ERROR; separate effort unsupported; only Sonnet invocation |
| **[probed] MUNKNOWN** | unknown model | `--model no-such-model-xyz` | 1 | ERROR before init; empty id; catalogue in error |
| **[probed] MDEFAULT** | no selector | `no model` | 0 | ok; init omits model |
| **[probed] MINSPECT** | base resolution | `-p /model; --model gemini-3.8-flash --effort low` | 0 | command_result; resolved low slug; zero usage |
| **[probed] EINSPECT** | effort readout | `-p /effort; --model gemini-3.8-flash --effort low` | 0 | low; available low/medium/high; zero usage |
| **[probed] RPARENT** | remember token | `--model gemini-3.8-flash --effort low --mode accept-edits` | 0 | saved; one turn; 11846 input / 1 output |
| **[probed] RSAME** | same-cwd resume | `no model; --conversation <RPARENT id>` | 0 | token recalled; cumulative 23989 input / 100 output |
| **[probed] RCWD** | different-cwd resume | `no model; --conversation <RPARENT id>; fresh cwd` | 0 | init new cwd; ls denied before requested write |
| **[probed] RMODE** | direct write after resume | `no model; --conversation <RPARENT id>` | 0 | write denied; target old RCWD workspace; empty response |
| **[probed] RMODEL** | resumed selection readout | `no model; -p /model; --conversation <RPARENT id>` | 0 | reports Flash high; empty result id; zero usage |
| **[probed] REFFORT** | resumed effort readout | `no model; -p /effort; --conversation <RPARENT id>` | 0 | reports high; empty result id; zero usage |
| **[probed] RCWD2** | shell cwd after longer history | `--conversation <RPARENT id> --dangerously-skip-permissions; fresh cwd` | 0 | init new cwd; actual pwd names earlier RCWD repo |
| **[probed] CWDPARENT** | distinguishable cwd A | `--dangerously-skip-permissions` | 0 | pwd A; reads CWD_A; remembers token |
| **[probed] CWDRESUME** | distinguishable cwd B | `--conversation <CWDPARENT id> --dangerously-skip-permissions; cwd B` | 0 | pwd B; reads CWD_B; token recalled |
| **[probed] MODEPARENT** | mode persistence parent | `--mode accept-edits` | 0 | saved token in fresh same-cwd session |
| **[probed] MODERESUME** | mode persistence child | `no model; --conversation <MODEPARENT id>` | 0 | same-workspace explicit write denied; accept-edits behavior did not carry |
| **[probed] RUNKNOWN** | never-existing id | `no model; --conversation <fresh UUID>` | 0 | warning; new different id; empty context; ok |
| **[probed] KILL** | kill during first tool | `--dangerously-skip-permissions; supervisor SIGKILL on tool ACTIVE` | 137 | four complete JSON lines; no result |
| **[probed] RKILL** | resume killed parent | `no model; --conversation <KILL id>` | 0 | killed-turn token recalled; permissions reset; cumulative usage |
| **[probed] ENV** | foreign env variables | `ANTHROPIC_API_KEY=bogus CLAUDE_CODE_ENTRYPOINT=bogus` | 0 | ok; empty stderr |
| **[probed] WORKTREE** | fresh Git worktree | `--dangerously-skip-permissions; worktree cwd` | 0 | edit/check/commit ran; no trust prompt; no trailer |
| **[probed] DOCS_CLI** | CLI packaging source | `free: curl Homebrew CLI cask raw URL` | 0 | current source snapshot; binary alias agy |
| **[probed] DOCS_IDE** | app packaging source | `free: curl Homebrew app cask raw URL` | 0 | current source snapshot; app-only installation |
| **[probed] DRV_PT0** | explicit unlimited timeout | `--print-timeout 0s` | 0 | sentinel reply; SUCCESS; empty stderr |
| **[probed] DRV_SONNET** | catalogue id without effort | `--model claude-sonnet-4-6` (no effort) | 0 | sentinel reply; 4,032 input tokens |
| **[probed] DRV_SMOKE** | smoke shape | none | 0 | sentinel reply in the last agent response; SUCCESS |

The three `DRV_` rows were run by the Handoff driver after the matrix, from a fresh `git init` directory, to close points the design rests on.

## 12. Handoff end-to-end run (2026-10-14)

Recorded by Task 7 of `docs/specs/plan_antigravity-cli-backend.md`. It drove `agy` 1.3.5 on `gemini-3.8-flash-low` through `delegate-codex.sh`, from a scratch repo under a long session scratch path.

- **[probed] Plan mode refuses a read-only shell command too, and the turn ends there.** A read-only job asked to write a file first ran `ls -la`. The tool step ended `ERROR` with `tool_info.error.type: TOOL_ERROR` ("permission check failed for unsandboxed \"ls -la\""). `result` was `SUCCESS` with an empty `response` and `denied_actions: [{"action": "command", "display_name": "RunCommand"}]`. Exit was 0, and stderr carried the headless auto-deny notice that RO1R showed. RO1's read ran through a file tool, so plan mode allows file reads but no shell command of any kind. A read-only antigravity job cannot run `git diff`, `grep`, or a test, and the first attempt ends its turn.
- **[probed] A resumed round's shell runs in the parent's directory.** The `-r2` round ran `pwd` and printed the scratch repo, the parent's directory, and recalled a token from the parent. This closes the open question for Handoff's case only: Handoff always resumes from the parent's directory. CWDRESUME and RCWD2 remain unexplained.
- **[probed] Per-invocation usage holds on a real resume.** The round's `step_update.usage` sums to 27,179 tokens; its `result.usage` reports 77,907, which covers the whole conversation. This matches RSAME.
- **[probed] No worker commit and no workspace files.** None of the four jobs committed or wrote outside the requested files. The worktree job's commit was the driver's.

## References

- **[probed]** Local raw evidence: `.handoff/probes/agy/`, ids listed in section 11; the ignored `probe_runner.py` records the supervisor and fixture construction.
- **[help]** `agy --help`, recorded in HELP on 2026-10-14.
- **[changelog]** `agy changelog`, complete release-note stdout recorded in CHANGELOG on 2026-10-14; versions cited at the relevant claims.
- **[docs]** [Antigravity headless mode](https://antigravity.google/docs/cli/headless/), fetched 2026-10-14. Documentation claims are separated from live results above.
- **[docs]** [Homebrew CLI cask](https://github.com/Homebrew/homebrew-cask/blob/master/Casks/a/antigravity-cli.rb) and [app cask](https://github.com/Homebrew/homebrew-cask/blob/master/Casks/a/antigravity.rb), fetched 2026-10-14; raw source snapshots DOCS_CLI and DOCS_IDE.
- **[docs]** [Homebrew issue 246183](https://github.com/Homebrew/homebrew-cask/issues/246183), fetched 2026-10-14, historical IDE-launcher alias evidence.
- Outline and comparison baseline supplied by the repository: [Cursor CLI specification](cursor-cli-specification.md). Cursor observations were not re-probed or treated as evidence about agy.
