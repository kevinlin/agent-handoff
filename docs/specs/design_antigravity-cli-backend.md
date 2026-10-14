# Design: Antigravity CLI as the fifth backend

## Context

Handoff runs delegated work on four backends: `codex`, `claude`, `copilot`, and `cursor`. This design adds `antigravity`, driven through `agy -p --output-format stream-json`. It meets the parity bar the flow's second invariant sets: an antigravity-backed row is the same job as any other.

- What must hold: [requirements_antigravity-cli-backend.md](requirements_antigravity-cli-backend.md). Requirement ids (R1.1 …) are cited below.
- What the CLI actually does: [../research/antigravity-cli-specification.md](../research/antigravity-cli-specification.md). That is 55 evidence records from a deep_reasoner job plus three driver runs, on `agy` 1.3.5. Probe ids (P3, RSAME) refer to its section 11. No decision here rests on `agy`'s help or docs where a probe was possible, and three times the probes overruled them:
  - the docs say workspace writes are auto-allowed in headless mode, but P0 was denied;
  - the docs give a five-minute print timeout, but help says unlimited;
  - `--mode plan` sounds read-only, but RO2 wrote files.

## Approach

**A fifth branch in each place that branches on backend,** as Cursor did in v3.9.0. Tables keyed by backend gain a row (`BACKENDS`, `BACKEND_EFFORTS`, `BACKEND_FIELDS`, `USAGE_FIELDS`, `BACKEND_LABELS`, `BACKEND_ICONS`, `PERMISSION_HINTS`). The five event parsers each gain an explicit antigravity branch:

1. `STATUS_SCAN_PY` in `delegate-codex.sh`
2. `cmd_result` in `delegate-codex.sh`
3. `fold_usage` in `render-cost-receipt.py`
4. `denials()` in `handoff-session-ui.py`
5. `HV.normalize` and `HV.inferBackend` in `assets/transcript-viewer.html`

The alternative was to first move the four Python parsers onto one event-normalization module and add Antigravity as one entry. It is rejected again for this release, for the reason Cursor gave: refactoring four working backends and adding a fifth in one change breaks `references/darwin-ratchet.md`'s "one dimension per change". With five event shapes across five parsers, the cost of the branching now shows. Antigravity's shape collides with none of the others (decision 7), so its branch never has to guess which backend a line came from. The follow-up is listed again, and it should come before a sixth backend.

## Decisions

### 1. Name `antigravity`, binary `agy`, identity checked by `--help`

The backend is named after the product, as `cursor` is, and the binary is `agy`. Every place that maps a backend name to a binary maps it explicitly:
- `resolve_worker_bin` gets an `antigravity)` case: `HANDOFF_ANTIGRAVITY_BIN`, then `agy` on PATH.
- Its `*)` case today would read `HANDOFF_CODEX_BIN` and then run `command -v antigravity`, which is the IDE launcher's name.
- `cli_available` in `handoff-setup.py` gets the same mapping; today it would call `shutil.which("antigravity")`.

Unlike Cursor, `agy` gets an identity check, because a collision is documented. An earlier Homebrew cask linked the Antigravity IDE launcher as `agy` (research section 1). An IDE launcher handed `-p "<packet>"` from a background job would at best fail and at worst open an editor window. `agy --version` prints a bare `1.3.5` (VERSION), so there is nothing to match there. Instead, a candidate passes when `<candidate> --help` prints both `--conversation` and `--dangerously-skip-permissions`, read from stdout and stderr together, because the CLI prints its help on stderr (HELP). The call runs under a short timeout, and a candidate that hangs or fails does not pass. Those are the two flags Handoff depends on, so the check tests the protocol, not a brand string. It screens for compatibility; it does not prove what a real IDE launcher prints, which was not available to probe.

The check runs where Copilot's does: in PATH discovery (first passing match), on `HANDOFF_ANTIGRAVITY_BIN` (refused when it fails), and on a fix round's recorded parent binary. `agy changelog` would carry a product string, but it prints the whole release history, which is too heavy for a per-submit check.

### 2. Effort lives in the model id; antigravity's effort enum is `("model",)`

`agy` has a real `--effort` flag (`low|medium|high|xhigh|max`), but the probes show it cannot be a routing field:
- the catalogue lists ids, some with the effort inside (`gemini-3.8-flash-low`) and some without (`claude-sonnet-4-6`), and no base names (MODELS);
- an id plus any `--effort` is refused before a session (MCONFLICT, MMAX);
- `claude-sonnet-4-6` refuses `--effort` outright (MCLAUDE) and runs fine without it (DRV_SONNET);
- the flag's enum is global, while each model accepts its own subset (MMAXBASE: Flash has no `max`).

So an antigravity identity stores a catalogue id as `model`, and `effort` holds the single value `model`, exactly as on Cursor. `--effort` never reaches the command line (R1.2, R2.4). Cursor's code paths for this are reused, keyed by a set of backends rather than by `cursor` alone: the effort fill, the locked effort field, and the effort-carrying refusal.

Rejected: **store base plus effort and pass both.** It works for Flash (MBASE). But the catalogue never shows base names, Claude models refuse the flag, and the CLI already validates each pair at launch, so Handoff would duplicate a per-model table it cannot discover.

### 3. Posture: `default` and `allow-all` both mean `--dangerously-skip-permissions`; read-only means `--mode plan`, alone

In print mode, `agy` never prompts. Any action that needs approval is denied, the turn stops at that point, and the process exits 0 with status `SUCCESS` (P0, P1). The probes left one posture in which a worker can run its checks:

| Candidate for `default` | Probed result |
|---|---|
| No flag (`request-review`) | The first edit was denied; nothing else ran (P0). |
| `--mode accept-edits` | The edit landed; `bash check.sh` was denied (P1). A worker cannot verify its work, which is the v3.5.1 failure. |
| `--mode auto` | Not available on the probed account. `agy` printed a warning and fell back to `request-review` (P2, P2R, P5). A posture that silently becomes P0 on some accounts cannot be a default. |
| `--sandbox --dangerously-skip-permissions` | The outside read and write were blocked, but so was the check's in-repo marker write, and `git commit` failed reading `~/.gitconfig`. The network call still returned 200 (P4, P4R, SCANON). It blocks what a worker needs and leaves the network open. |
| `--dangerously-skip-permissions` | Everything ran: edit, check, outside read, network, commit (P3). |

`allow-all` maps to the same flag, because skipping permissions already is `agy`'s unrestricted mode. The posture field is therefore a no-op on Antigravity, and R3.2 requires the docs to say so. Every writing job prints the bypass warning (R3.4).

**Read-only is `--mode plan` and never carries the skip flag.** On Copilot that pairing gave false claims of writes; here it gives real writes. RO2 and RO2R both created `new.txt` and `shell.txt` under `--mode plan --dangerously-skip-permissions`. Plan mode alone denied the write, and nothing changed on disk (RO1, RO1R).

One limit is stated, not solved. Plan mode in print mode refused the write through the same headless auto-deny that P0 hit. A `permissions.allow` rule in the user's `agy` settings could, in principle, approve a write in plan mode. This was not probed, and Handoff writes no settings (R3.5). The flow prose tells the driver to check the tree after a read-only antigravity job (R8.4). Adding `--sandbox` to read-only would not close the gap: the sandbox lets file-tool writes through (P4 edited `tracked.txt`), and it blocks the `~/.gitconfig` read that `git` needs.

### 4. Session id from `init`; resume passes model and posture again, and checks the id

`conversation_id` is a top-level field of `init`, which is line one of every created session, including one killed during its first tool call (KILL). `extract_session_id` gains `conversation_id` in its key tuple (R4.1). No other backend emits that key, so the addition changes nothing for them. A pre-session error writes a `result` with an empty id and no `init` (MUNKNOWN), so the extractor finds nothing and `resume` refuses, as it should.

`--conversation <id>` resumes with the parent's context, both after a clean finish (RSAME) and after SIGKILL (RKILL). Two things the other backends inherit do not carry over:
- **posture:** `init.permission_mode` went from `always-proceed` back to `request-review` (RKILL), and an accept-edits parent's child was denied a write (MODERESUME);
- **model:** an unpinned resume reported the CLI's current selection, not the parent's (RMODEL, REFFORT).

So the resume line passes `--model` and the posture flag again (R2.3). The model comes from the parent's `meta`, and the resume's own `meta` records it, so a resume of a resume never has to follow `parent=` to find it.

**The requested id and the observed id are kept apart.** Today every resume writes the parent's id into the round's `session_id` cache before launch (`delegate-codex.sh:678`), and `extract_session_id` returns that cache before it reads the log (`:405`). On `agy` that is wrong twice:
- given an id it cannot find, `agy` warns on stderr, exits 0, and starts a conversation under a **new** id (RUNKNOWN). With the cache pre-written, the round would report, and a further resume would use, the id of a conversation that no longer exists;
- a round whose model has left the catalogue fails before `init` (MUNKNOWN's shape), but the pre-written cache would still give it a session id, and a further `resume` would fail the same way.

So an antigravity resume does not pre-write the cache. Its `meta` records the id it asked for as `resumed_session=<id>`, and its session id is whatever its own log's `init` names (R4.1). A round with no `init` has no session id, and `resume` refuses it. When `init` names a different id from `resumed_session`, the round ran without its parent's context. `status` and `result` say so, `result --json` sets `context_lost: true`, and the flow prose tells the driver to resubmit with the full packet (R4.3). Cursor reused the requested id, so this check was impossible there. Here it needs only the round's `meta` and the first line of its log.

A retired model has no in-place fix: `resume` takes no model override (`delegate-codex.sh:579`), and changing the config does not change a parent's `meta`. `resume` never substitutes a model. The recovery is a fresh `submit` with the full packet once the identity names a model the catalogue still offers (R2.6).

The working directory is the shell's, and Handoff always resumes in the parent's. No probe established that case directly. RSAME resumed in the same directory and recalled its context, but ran no shell command. CWDRESUME resumed from a new directory, and its shell followed. RCWD2, resumed from a second new directory, ran its shell in the first. The live run's resume runs `pwd` (R9.3), and the stale case is listed as a risk.

### 5. The print timeout is pinned to unlimited

`agy`'s help and changelog say the print timeout defaults to unlimited. Its headless docs page says five minutes. A run that times out exits 0 with status `SUCCESS`, partial output, and only a stderr line to show for it (TIMEOUTR). A delegated job routinely runs past five minutes, and under the documented default it would be cut short silently. Every antigravity command line therefore passes `--print-timeout 0s`, which was probed (DRV_PT0) (R2.7). If the help text is right, the flag changes nothing. If the docs page is right, the flag stops a silent truncation.

### 6. Command lines

| Case | Command line in `run.sh` |
|---|---|
| fresh | `cd "$WORKDIR"` then `"$CODEX_BIN" -p "$PROMPT" --output-format stream-json --model "$MODEL" --print-timeout 0s $POSTURE` |
| resume | `cd "$WORKDIR"` then `"$CODEX_BIN" -p "$PROMPT" --output-format stream-json --conversation "$SESSION_ID" --model "$MODEL" --print-timeout 0s $POSTURE` |
| `$POSTURE` | `--mode plan` when read-only, else `--dangerously-skip-permissions` |

Every line sends stdout to `log.jsonl` and stderr to `stderr.log`, with stdin from `/dev/null`. There is no workspace flag: `agy` takes the working directory from the shell, and `init.cwd` records it (P0, WORKTREE). The flags that must never appear are listed in R2.4. `$CODEX_BIN` keeps its historical name, as it does for the other backends.

There is no environment scrubbing. `agy` authenticates with its own sign-in, and bogus `ANTHROPIC_API_KEY` and `CLAUDE_CODE_*` values had no effect (ENV). That matches the rule for Copilot and Cursor.

### 7. Parsing an antigravity log

Every other backend's log discriminates on `type`. `agy`'s discriminates on `event`, with four values: `init`, `step_update`, and `result` nest their payload under a key of the same name, and `command_result` nests its payload under `command` (RMODEL). A `step_update` is further typed by `step_type`: `user_input`, `agent_response`, `tool`, `system_message`, or `error_message`. No type name collides with another backend's, which removes the problem Cursor had with Claude's shared names.

- **Denial:** the `denied_actions` list on the `result` event, and nowhere else. The same denied write ended as a `DONE` tool step with no error in P0, and as an `ERROR` step with a `TOOL_ERROR` in P2R. Both times it was listed in `denied_actions`. Before a `result` exists, the count is unknown (R5.1).
- **Agent message:** the last `agent_response` step's text, which is the `text_delta` values with that `step_index` joined in order. `result.response` joins every agent message of the invocation (P3: 1,236 characters against the final step's 949).
- **Commands:** the `CommandLine` parameter of `ACTIVE` `run_command` tool steps. `agy` logs no exit code: a deliberate `exit 7` ended `DONE` with no error (EXIT7). `result` lists commands without a status.
- **Usage:** the sum of the `usage` objects on this log's `step_update` events. `result.usage` and `result.duration_seconds` are cumulative over the whole conversation: RSAME's result reported 23,989 input tokens, while its own steps used 12,143 (the parent's 11,846 plus 12,143 make 23,989). The step sum equals `result.usage` on every fresh run checked (P3, P4) and equals the per-invocation delta on every resume (RSAME, RKILL). Usage is known only when the log has a `result` event, which keeps the existing missing-data rule.
- **Reasoning:** `thinking_tokens`, which is known. Cache writes are not reported.
- **Model:** `init.model` echoes the selector passed (MBASE). Handoff always passes the catalogue id, so the slug in `meta` is what ran, and there is no display name to prefer.
- **Errors:** `result.error` when the status is `ERROR`. An API failure also exits 3 and writes an `AGY_ERROR:` line to stderr (RO1). A print timeout leaves only a stderr line, `[agy] print timeout after …` (TIMEOUTR), so `result` reads that line too (decision 14).
- **Noise:** `user_input` steps carry no text; `result` skips them and the viewer drops them. `system_message` steps appear on resumed turns with no text; `result` skips them and the viewer shows them as lifecycle rows.

### 8. No antigravity log may reach another backend's parser

R2's scan of HEAD `1486e87` found these places where a fifth backend nobody named lands in another backend's branch or crashes. The Cursor design's table of this kind is stale: four of its ten rows have changed since, and `assets/setup-ui.html` did not exist.

| Place | Catch-all today | Effect on an unhandled antigravity job |
|---|---|---|
| `resolve_worker_bin` (`delegate-codex.sh:189-236`) | `*)` → `HANDOFF_CODEX_BIN`, then `command -v <backend>` | looks up `antigravity`, the IDE launcher's name |
| `validate_effort` (`:282-292`) | `*)` → codex enum | `model` refused, `ultra` accepted |
| `"resolve_${BACKEND}_permission_mode"` (`:339`) | no function | submit dies with bash "command not found", exit 127 |
| `extract_session_id` (`:425`) | no `conversation_id` key | no session id, so no `resume` |
| resume's `session_id` pre-write (`:678`), returned first by `extract_session_id` (`:405`) | the requested id stands for the round | a lost or failed round reports a conversation that does not exist (decision 4) |
| `write_run_script` (`:712-726`) | `else` → codex exec line | a codex command line against `agy` |
| `STATUS_SCAN_PY` (`:855-872`) | Claude's `permission_denied` subtype | denials never counted |
| `cmd_result` (`:1028-1056`) | shared codex and claude parser | no agent message, no usage |
| `warn_permission_bypass` (`:344-362`) | unlisted → no warning | a skip-permissions job with no warning |
| `BACKEND_EFFORTS[backend]` (`handoff-setup.py:110`, `handoff-setup-ui.py:608`, `:811`) | `KeyError` | setup and the wizard crash |
| `validate_backend_models` (`handoff-setup.py:132`) | copilot and cursor only | an empty or `auto` model accepted |
| `smoke` (`handoff-setup.py:1263-1264`) | pass after the dry-run alone | `verified = true` with no real call |
| `normalize_payload` catalogue guard (`handoff-setup-ui.py:827`) | copilot and cursor only | any typed model written |
| `build_state` (`handoff-setup-ui.py:669-745`) | not in `model_options`, `model_discovery`, `clis` | no models and no tile |
| `clis` comprehension (`handoff-setup-ui.py:722-732`) | every name in it is indexed into `CLI_RELEASES` | `KeyError` if antigravity is added there without a release entry |
| `assets/setup-ui.html` (`:437-467`, `:858`) | `BACKEND_LABELS`, `BACKEND_ICONS`, `PERMISSION_HINTS` lookups; `models()` source ternary | `undefined` label, blank icon, `TypeError`, a "cursor-agent models" source label |
| `syncPermissionWarning` (`assets/setup-ui.html:600-619`) | an identity is named only under `allow-all`, except Cursor's, named always | an antigravity identity on `default` is missing from the summary, though its jobs skip every check |
| `BACKEND_FIELDS`, `USAGE_FIELDS`, `fold_usage` (`render-cost-receipt.py:35-39`, `:123-143`, `:261-303`) | no row; unknown backend folds as unknown | the cost receipt refuses the job |
| cost cells (`render-cost-receipt.py:621-626`, `assets/cost-receipt.html:359-362`) | Claude's reading | "unknown", as if a reading were missing |
| `make-receipt.py:100-101` | counted as codex | antigravity jobs inflate `codex_jobs` |
| `validate-receipt.py` `ROLE_HOSTS` (`:35`) | refused | a receipt naming the host fails |
| `denials()` (`handoff-session-ui.py:223-224`) | explicit `not reported (<backend>)` | no denial count for the job |
| `HV.inferBackend`, `HV.normalize` (`transcript-viewer.html:385-397`, `:554-637`) | `null`, then the shared parser and `unknown` rows | an unreadable transcript |
| `BackendLifecycle.assert_posture_argv` (`tests/test_delegate_role.py:674-675`) | `self.fail` | fails loudly, as intended |

Each row gains an explicit `antigravity` branch (R7.4). The generic catch-alls stay as they are, including the three that silently count an unknown backend as codex (`make-receipt.py:101`, and the meta-less `BACKEND:-codex` defaults at `delegate-codex.sh:639`, `:904`, `:945`). They do not affect a named backend, and hardening them is a separate change.

### 9. Receipt v8, flat pair

`antigravity_jobs` and `antigravity_job_durations` join the four existing pairs, following the flat-field decision from v3.7.0.
- `make-receipt.py`: `BACKENDS` gains `antigravity`. `main()` gains `--antigravity-jobs` (default `0`) and emits the pair, because `count_mismatches()` reads every backend's pair. The version it writes becomes `8`.
- `validate-receipt.py`: the pair joins `REQUIRED_FIELDS`; `antigravity` joins `ROLE_HOSTS`, the count and durations tuples, and the version gate.
- `render-cost-receipt.py`: a row in `BACKEND_FIELDS` and `SCHEMA_VERSION = "8"`.
- `docs/receipt-schema.json`: the pair, the `antigravity` host, and `const: 8`.
- `scripts/check-skill-repo.sh:179`: the PASS message names v8.

`.github/workflows/checks.yml` passes only `--codex-jobs 0` in its roundtrip, so the new flag's default keeps it unchanged.

### 10. Cost receipt: tokens and reasoning, no figure

`agy` reports five counters and no cost of any kind (P3, RO2R). An antigravity job row shows input, output, cache-read, and reasoning tokens, unknown cache writes, a `None` USD figure, and no credits. Its cost cell reads "n/a - no cost figure" in the Markdown and the HTML, like codex, copilot, and cursor rows (R7.3). The summary's Antigravity card shows the summed counters and says that Antigravity reports no cost figure. The jobs are counted again in the outside-driver figure, so its caveat names Antigravity (R7.5).

Because each log's usage is per invocation (decision 7), the existing rule of summing a parent and its fix round is correct as is. The model column needs no new precedence: `meta` holds the id that ran.

### 11. The wizard reads the catalogue at page load

`agy --output-format json models` is free and takes about two seconds (MJSONGLOBAL; the text form took four). That is the same cost profile as Cursor's catalogue, so it runs in `build_state` when the page loads. The JSON form is used because it needs no line parser: `command.data.models[]` carries `id` and `label`. The flag goes before the subcommand, since after it is rejected (MODELJSON). Failure handling and retained ids follow Cursor's decision 11 (R6.2, R6.4).

The page also runs `agy --version` for the tile and `agy --help` for the identity check (decision 1). The existing test that page load starts no copilot subprocess still holds, and a new assertion allows exactly these three `agy` commands.

`agy` gets no `CLI_RELEASES` entry. That entry drives the latest-version lookup and the update hint, and no free, probed source for `agy`'s latest version exists yet. Claude has no entry either, and its tile shows the installed version alone. Antigravity therefore joins `state["clis"]` directly, beside Claude, outside the comprehension that indexes `CLI_RELEASES` (`handoff-setup-ui.py:722-732`).

The permission summary names every antigravity identity whatever its posture, in a sentence of its own, as it does for Cursor (`syncPermissionWarning`, `assets/setup-ui.html:600`). The per-row hint says the same thing in fewer words. The detection grid grows from five tiles to six (`assets/setup-ui.html:68`, `:279-299`).

### 12. Smoke: one cheap real call

Smoke runs three steps for an antigravity identity: the dry-run chain, a catalogue membership check (free), and one `agy -p "…HANDOFF_SMOKE_OK…" --output-format stream-json --model <id>` run with no posture flag. A pass needs exit 0, `result.status` `SUCCESS`, and the sentinel in the last `agent_response` text. That is Claude's rule, plus the status (R6.5).

The run takes no posture flag because, in print mode, `agy`'s default denies every action that needs approval (P0), and the prompt asks only for a reply. `--mode plan` was not used: it adds a planning instruction that a one-line smoke has no use for, and it was never probed on a reply-only prompt. The command was probed as written (DRV_SMOKE): about 12k input tokens on Flash, 4k on Sonnet (DRV_SONNET). `validate_antigravity_model` sits beside `validate_cursor_model` and is not a shared helper. The command lines and parsing differ, and approach A keeps them apart.

### 13. A model `agy` rejects at launch is a failed job

As on Cursor, the model check happens after `submit` has written the job directory, so a rejected id is a `FAILED` job, not a refusal (R2.6). Unlike Cursor, `agy` writes one `result` event with an empty `conversation_id`, status `ERROR`, and the reason in `result.error` (MUNKNOWN). The log is therefore not empty: `result` shows the error, and usage reads zero, as `agy` reported it.

### 14. What `status` and `result` print for an antigravity job

Two things are new to the output contract: a denial count that can be unknown, and a lost session. Both use shapes that already exist, so consumers keep working:
- **Denials.** `agy` reports them only on its terminal `result`. While a job runs, `status` prints no denial line, as for a clean job, because `status` is polled. A finished job whose log has no `result` prints `permission_denied: unknown (…)` (R5.1). In `result --json`, `permission_denied` is `null` in that case; text output prints `unknown`. Today `status` tests the count numerically (`delegate-codex.sh:922`) and `result` prints `len(denied)` (`:1072`, `:1093`), so both gain the unknown case.
- **Errors.** `result.error` is a string (MUNKNOWN), and the text formatter expects objects with `status_code`, `error_type`, and `message` (`:1086`). An antigravity error becomes `{"status_code": null, "error_type": "result_error", "message": <verbatim>}`. A print-timeout stderr line becomes `{"status_code": null, "error_type": "print_timeout", "message": <line>}`. The pinned timeout (decision 5) should make that second case unreachable, and if it ever fires, the job must not read as clean.
- **Lost session.** `result --json` gains `context_lost`, present on every backend so the payload shape does not vary by vendor. It is `true` or `false` on an antigravity resume, and `null` wherever it is not measured. Text output prints a warning line when it is `true`.

The acceptance checks run text and JSON output over three fixtures: a log cut off before `result`, a launch rejection, and a lost-session resume (R5.2).

## Mapping audit

| Abstraction Handoff needs | `agy` mechanism | Status |
|---|---|---|
| Non-interactive submit | `-p "<prompt>"`, stdin `/dev/null` | probed |
| Event stream | `--output-format stream-json`, `event` discriminator | probed |
| Session id | top-level `conversation_id` on `init`, line one | probed |
| Fix round on the same session | `--conversation <id>`, with model and posture passed again | probed, context intact |
| Resume after a crash | SIGKILL during the first tool call, then `--conversation` | probed, context intact; a kill between `init` and the first tool call is open |
| Resume of an id `agy` never saw | warning, exit 0, new id | probed; detectable by comparing `init` with `meta` `resumed_session` (decision 4) |
| Working directory | the shell's; `init.cwd` records it | probed for fresh runs and a resume from a new directory; a same-directory resume's shell is open (R9.3 checks it); one stale case after a two-directory chain (RCWD2) |
| Effort as a routing field | none usable; the id carries it | probed; decision 2 |
| Read-only job | `--mode plan`, alone | probed: write denied, tree clean |
| Default posture that can run checks | `--dangerously-skip-permissions` | probed; decision 3 |
| Denial detection | `result.denied_actions` | probed, typed |
| Final agent message | last `agent_response` step's deltas | probed |
| Commands run | `run_command` `CommandLine` | probed; no exit code |
| Token counters | sum of step `usage`, per invocation | probed |
| Reasoning count | `thinking_tokens` | probed |
| Cost figure | none | probed: absent |
| Model catalogue | `agy --output-format json models`, free | probed |
| Model check | `result` `ERROR` with empty id, exit 1, at launch | probed; a failed Handoff job (decision 13) |
| Binary discovery | `agy`, `--help` flags as identity | probed for the CLI; the IDE launcher's help is open |
| Print timeout | `--print-timeout 0s` | probed |
| Mid-run API failure | exit 3, `result` `ERROR`, `AGY_ERROR:` on stderr | probed for model capacity; auth expiry and personal quota are open |

## Deviations from `docs/research/cross-agent-cli-permissin-mode.md`

That document has no Antigravity rows. Measured against its rules, the deviations are the ones Cursor already made:

1. **`default` is the CLI's unrestricted mode.** The document's rule treats unapproved actions as failures and never escalates. On `agy` that rule gives P0 or P1, a worker that cannot verify its work.
2. **`default`'s network-denied and workspace-only-writes goals are not met.** The only `agy` posture that confines anything, `--sandbox`, blocks the in-repo write and the commit a worker needs, and it left the network open (P4).

## Where it fails closed, and where it doesn't

These rows join the table in `docs/specs/design_agent-handoff.md` once the feature ships:

| Guard | Enforced by | Failure mode when skipped |
|---|---|---|
| Antigravity effort must be `model` | script (`validate_effort`, `validate_backend_efforts`) | none available |
| Empty or `auto` model on antigravity | script, at submit and setup | none available |
| Binary is a real `agy` CLI, never an IDE launcher | script (`--help` identity check in `resolve_worker_bin`, `cli_available`, resume) | none available |
| Read-only never carries the skip flag | script (the posture resolver) | none available |
| Every job pins `--print-timeout 0s` | script (`write_run_script`) | none available |
| Antigravity posture narrower than skip-permissions | nothing | a worker reads outside the repo, reaches the network, and commits |
| A plan-mode job's writes | `agy`'s headless auto-deny | a user allow rule might approve one. The driver checks the tree; on a change it rejects the output, saves the changes to `.handoff/rejected/<task-id>.patch`, restores the paths, and records `read-only write` |
| A fix round that lost its session | `status` and `result` id comparison | warns; the round's output lacks the parent's context, and the driver resubmits |
| A run cut short by a print timeout | the pinned `0s`, then the `print_timeout` error | output is partial; the driver treats the job as failed and resubmits |

## Component changes

| File | Change |
|---|---|
| `scripts/delegate-codex.sh` | `antigravity` in the backend guard; `resolve_worker_bin` case with the `--help` identity check; `validate_effort` enum; empty and `auto` model refusals; `resolve_antigravity_permission_mode`; `write_antigravity_exec_line` and its branch in `write_run_script`; resume re-passing model and posture, recording the model and `resumed_session` in `meta`, and skipping the `session_id` pre-write; `antigravity:skip-permissions` in the `warn_permission_bypass` whitelist and its remedy line; `conversation_id` in `extract_session_id`; antigravity branches in `STATUS_SCAN_PY` and `cmd_result`, with the lost-session comparison, the unknown denial count, and the two error objects (decision 14); `context_lost` in `result --json` for every backend; header comment and `usage()` |
| `scripts/handoff-config.py` | `BACKENDS` gains `antigravity`; the stale "three CLIs" comment (`:77`) |
| `scripts/handoff-setup.py` | `ANTIGRAVITY_EFFORTS`; `antigravity_bin` with the identity check; `cli_available` mapping; the antigravity model refusals in `validate_backend_models`; the JSON catalogue reader, shared with the UI; `validate_antigravity_model`; smoke branch; terminal wizard backend prompt and effort fill |
| `scripts/handoff-setup-ui.py` | `_antigravity_model_options` through the engine's catalogue reader; version and identity probes in `build_state`; `model_options` and `model_discovery` entries; a direct `clis` entry beside Claude's, outside the `CLI_RELEASES` comprehension; `BACKEND_EFFORTS` lookups; antigravity in the `normalize_payload` catalogue guard |
| `assets/setup-ui.html` | `BACKEND_LABELS` and order; `BACKEND_ICONS` with attribution; `PERMISSION_HINTS`; an antigravity sentence in `syncPermissionWarning`; the `models()` source label; effort lock and retained-id marking keyed by a backend set; sixth detection tile; subtitle |
| `docs/receipt-schema.json`, `scripts/make-receipt.py`, `scripts/validate-receipt.py`, `scripts/check-skill-repo.sh` | v8 (decision 9) |
| `scripts/render-cost-receipt.py`, `assets/cost-receipt.html` | `SCHEMA_VERSION`; `BACKEND_FIELDS` and `USAGE_FIELDS` rows; antigravity fold over step usage; the cost cell in both renderings; summary card, outside-driver caveat, and denials sentence |
| `scripts/handoff-session-ui.py` | antigravity `denials()` branch over `denied_actions` |
| `assets/transcript-viewer.html` | antigravity branch in `HV.normalize`; `event` key in `HV.inferBackend` |
| `SKILL.md`, `references/*.md`, `README.md`, `CLAUDE.md` and its `AGENTS.md` copy, `PRODUCT.md`, the user guide and its four diagrams, `assets/promo.html` receipt lines, `CHANGELOG.md`, `examples/session-receipt.md`, `test-prompts.json`, `evals/` | five backends, v8, version 3.10.0, and the Antigravity limits (R8.1, R8.2, R8.4). Locations: R2's prose inventory (job `job-2026-10-14T21-42-57-10499-R2-backend-scan`) |
| `docs/specs/design_agent-handoff.md`, `design_agent-identities-and-config.md`, `design_agent-handoff-evidence.md`, `design_session-visualisation.md` | after implementation only (R8.3) |
| tests | R9.2: an `AntigravityWorktreeTests` subclass of `BackendLifecycle`; antigravity fixtures for each parser; the shared helpers and v7 fixtures it lists |

Unchanged: `job_state` and its Python mirror in `handoff_runtime.py`; `render-transcript.py`, whose payload already carries `meta.backend`; `showcase-cost-ledger.py`; `goal-sync.py`; `PRESETS`; `CLI_RELEASES`; config `schema_version`; `install.sh`, which copies tracked files; and `.github/workflows/checks.yml`.

## Risks

- **`default` equals `allow-all` on Antigravity.** A worker can read outside the repo, reach the network, and commit. Handoff knows of no rule that narrows it. That is looser than Cursor, whose force mode respects deny rules according to its help. The bypass warning on every job is the reminder.
- **Read-only rests on headless auto-deny.** Plan mode refused writes through the same path that denies them in P0. A user allow rule could change that (not probed). The driver checks the tree after a read-only job.
- **Exit 0 and `SUCCESS` mean little.** A denial-stopped turn (P0) and a timed-out turn (TIMEOUTR) both exit 0 with `SUCCESS`. The denial count, the pinned timeout, and the full review gate carry the weight. That is the existing contract, but `agy` relies on it harder than the others.
- **No command exit codes.** `result` cannot say a check failed. A worker's claim that checks passed is verified by re-running them, which Phase 4 already requires.
- **Auto mode varies by account.** A later change to a posture other than skip-permissions would behave differently per account, and the fallback is a stderr warning.
- **A stale working directory after a two-directory resume chain** (RCWD2). Handoff never resumes from a different directory, but the cause is unknown.
- **The catalogue is small and account-dependent.** Six ids here. A configured id can leave it, and the first job then fails with `agy`'s own list of available models.
- **The identity check rests on two flag names.** A later `agy` release could rename one, and every submit would then refuse the real CLI, naming what it found. That fails closed, and the message points at the cause.
- **An engine older than 3.10 meets antigravity job directories.** Its config validation refuses an antigravity config, but its `make-receipt.py` counts the job as codex. This is the Copilot and Cursor version skew again, accepted for the same reason: scripts and flow prose ship together.
- **Five event shapes across five parsers.** This is contained for this release, and it is the reason the normalization module is the first follow-up.
- **Fake-CLI tests prove wiring, not behaviour.** Real behaviour rests on the probe log (one build, one account, one machine) and the live run in R9.3.

## Follow-ups

- **One event-normalization module** for the four Python parsers, as its own release with no behaviour change, before any sixth backend.
- **Hardening the catch-alls** that count an unknown backend as codex, and the bash `*)` cases, so an unknown backend dies instead of borrowing another's branch.
- **An `--mode auto` default** for accounts that have it, once probed on one, with a guard for the fallback warning.
- **A latest-version source for `agy`**, and with it a `CLI_RELEASES` entry.

## Spec review

Reviewed once by `deep_reasoner` (codex / gpt-6.1-sol / xhigh, read-only) as job `job-2026-10-14T22-15-34-55254-spec-review`: 25 commands, 0 denials. The packet listed the settled decisions, six check classes, and six points I was least sure of. No finding reopened a settled decision. Every cited line was checked against the repository and the probe logs before it was folded in.

**Accepted, all seven.** Two were blocking:
1. The requested and observed session ids were not kept apart. A resume pre-writes the parent's id into the round's cache, and the extractor returns the cache before it reads the log, so a lost or failed round would report a conversation that does not exist. The fix is the `resumed_session` meta key, no pre-write on antigravity, and an explicit recovery for a retired model (decision 4, R2.6, R4.1, R4.3).
2. The `status` and `result` output contract was unfinished: an unknown denial count had no representation, and `result.error` is a string where the formatter expects objects (decision 14, R5.1, R5.2).

Five were advisory:
- four existing tests the change breaks (R9.2);
- what the driver does when a read-only job writes, and when a print timeout fires anyway (R8.4, the fail-closed table);
- CWDRESUME ran in a new directory, so it does not show a same-directory resume. The live run now checks that with `pwd` (decision 4, R9.3);
- the event envelope (`command_result` nests under `command`), and the viewer's handling of `user_input`, which the design and R7.6 described differently (decision 7);
- `syncPermissionWarning` was missing from the component table (decision 11, R6.7).

The reviewer also confirmed five things:
- the step-usage sum matched `result.usage` on all 26 fresh invocations in the probe set, and no completed `agent_response` step lacked usage;
- smoke with no posture flag is the right shape;
- re-passing an explicit model on resume is right, provided a rejection fails the round and nothing substitutes a model;
- no wizard path assumes every non-Claude backend has a `CLI_RELEASES` entry, apart from the `clis` comprehension, now avoided;
- no backend-parser fall-through beyond the branches the design already requires.

**Declined:** none.
