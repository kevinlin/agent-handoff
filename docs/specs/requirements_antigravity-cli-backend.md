# Requirements: Antigravity CLI as the fifth backend

Target version: **3.10.0**. Receipt schema: **v8**.

Design: [design_antigravity-cli-backend.md](design_antigravity-cli-backend.md). Facts: [../research/antigravity-cli-specification.md](../research/antigravity-cli-specification.md), probed on Antigravity CLI `agy` 1.3.5. Probe ids in parentheses (P3, RSAME) name evidence records in that document's section 11.

## Why

A user with an Antigravity account gets a fifth meter to delegate onto. Through it they reach Gemini 3.8 Flash at three effort levels, Claude Sonnet and Opus 4.6, and GPT-OSS 120B.

The bar is the invariant in `docs/specs/design_agent-handoff.md`: **all backends are the same job**. An antigravity-backed row gets the same jobId, job directory, monitor loop, bounded `resume`, worktree lifecycle, and receipt evidence as the other four. Where `agy` differs, the difference stays inside the scripts and never becomes a branch in the flow.

## Scope

In: routing and config, the delegation primitive, permission posture, session and resume, monitoring, setup (terminal and web), smoke, the session receipt, the cost receipt, the session view, the transcript viewer, prose, and tests.

Out: see *Out of scope* at the end.

## Requirements

Each requirement has an acceptance check. "Refused" means that Handoff rejects the request before a job directory exists, with a message that names the fix. A rejection by `agy` itself comes after launch, so it is a failed job, not a refusal (R2.6).

### R1. Routing and configuration

**R1.1** `antigravity` is a fifth accepted `backend` value in the config engine, both setup surfaces, and `submit`. `schema_version` stays `2`.
Acceptance: `handoff-config.py set --role fast_worker --backend antigravity --model gemini-3.8-flash-high --effort model` writes a config that validates; an unknown backend is still refused.

**R1.2** An antigravity identity's `model` is an id from the `agy` model catalogue, and its `effort` is exactly `model`. Any other effort is refused at setup and at submit. The message says the Antigravity model id carries its effort.
Acceptance: an antigravity identity with `effort = "high"` is refused by `handoff-setup.py` and by `delegate-codex.sh submit --role`.

**R1.3** An empty `model` and `model = "auto"` are refused for an antigravity identity at setup and at submit. Without `--model`, `agy` runs whatever model the CLI currently has selected, and `init` does not name it (MDEFAULT, RMODEL). Each refusal names `handoff-config.py set` as the fix.

**R1.4** A per-job `--backend` that contradicts a role configured as `antigravity`, or an antigravity `--backend` that contradicts another role, is refused, as it is today for the other backends.

**R1.5** No preset names an antigravity identity. Antigravity is chosen through custom mode or the web wizard.

### R2. Launching a job

**R2.1** The worker binary resolves from `HANDOFF_ANTIGRAVITY_BIN`, otherwise from `agy` on PATH, and never from `antigravity`. A candidate is accepted only when the output of `<candidate> --help`, stdout and stderr together, contains both `--conversation` and `--dangerously-skip-permissions`. The CLI prints its help on stderr (HELP). The help call runs under a short timeout, and a candidate that times out or fails is not accepted. Homebrew once linked the Antigravity IDE launcher as `agy` (research section 1), and `--version` prints a bare number (VERSION). PATH discovery walks every `agy` match and takes the first that passes. A `HANDOFF_ANTIGRAVITY_BIN` that fails is refused, naming what it found. `resume` re-checks the parent's recorded binary before reusing it. Submit and setup's availability check use this rule.
Acceptance: with a fake `agy` printing IDE-launcher help earlier on PATH and a fake `agy` printing the CLI's help later, the job's `meta` records the later path. With neither installed, submit fails and names `HANDOFF_ANTIGRAVITY_BIN`.

**R2.2** A fresh job's generated `run.sh` changes into the working directory and runs `agy -p "$PROMPT" --output-format stream-json --model "$MODEL" --print-timeout 0s`, plus the posture flag (R3). Stdin is `/dev/null`, stdout goes to `log.jsonl`, and stderr to `stderr.log`.

**R2.3** A resume's `run.sh` changes into the parent's working directory and runs `agy -p "$PROMPT" --output-format stream-json --conversation "$SESSION_ID" --model "$MODEL" --print-timeout 0s`, plus the posture flag. The model and posture are passed again because `agy` carries neither over (MODERESUME, RKILL, RMODEL; re-passing the model was probed in CWDRESUME). The resume's `meta` records the model it passed, never `inherit`.

**R2.4** No generated antigravity command line contains `--effort`, `--sandbox`, `--mode auto`, `--mode accept-edits`, `-c`/`--continue`, `-i`, `--project`, `--new-project`, `--add-dir`, `--agent`, `--remote-control`, `--input-format`, `--json-schema`, or `--log-file`. No line carries both `--mode plan` and `--dangerously-skip-permissions`.

**R2.5** Worktree rows behave identically on antigravity: the base is pinned to an immutable SHA before the job directory exists, fix rounds inherit the tree, and `cleanup` refuses a dirty tree. A fresh worktree path needs no trust step (WORKTREE).
Acceptance: the shared `BackendLifecycle` tests pass for an antigravity subclass.

**R2.6** A model that `agy` rejects at launch produces a job that ends `FAILED`: an unknown id, or an id that has left the catalogue. `agy` exits 1 and writes one `result` event with status `ERROR` and an empty `conversation_id`, with its message and the available models in `result.error` and on stderr (MUNKNOWN). The job keeps both verbatim. It has no session id, so `resume` refuses it with the existing error.

The same holds for a resume round whose model has left the catalogue since its parent ran: the round ends `FAILED` with no session id (R4.1). `resume` never substitutes a model. The recovery is a fresh `submit` with the full packet, on the identity's current config, after `handoff-config.py set` names a model the catalogue still offers. The flow prose says so.
Acceptance: a fake `agy` that prints that `result` line and the stderr message and exits 1 gives exactly that job state, for a fresh job and for a resume round.

**R2.7** Every antigravity job passes `--print-timeout 0s`. `agy`'s own help and changelog give the default as unlimited, while its headless docs page says five minutes. A timed-out run exits 0 with status `SUCCESS` and partial output (TIMEOUTR), so an unpinned default would truncate a job silently. `0s` was probed (DRV_PT0).

### R3. Permission posture

**R3.1** A read-only job passes `--mode plan` and never `--dangerously-skip-permissions`. With both, files were written (RO2, RO2R).

**R3.2** `permission_mode = "default"` and `permission_mode = "allow-all"` both pass `--dangerously-skip-permissions`. The docs say plainly that the posture field changes nothing on Antigravity.

**R3.3** Job `meta` records the requested `permission_posture` and its source, as today, and the effective `permission_mode` as `skip-permissions` or `plan`.

**R3.4** Every antigravity job that is not read-only prints the bypass warning. `warn_permission_bypass` gains `antigravity:skip-permissions` in its whitelist. For antigravity, the remedy line says that Antigravity has no narrower posture Handoff can set, and names `--read-only` for a job that must not write.

**R3.5** Handoff writes nothing under `~/.gemini/`. That covers `antigravity-cli/settings.json`, its `permissions.allow` rules, and its `trustedWorkspaces` list. Files that `agy` itself writes there are not Handoff writes.

### R4. Session and resume

**R4.1** An antigravity job's session id is the top-level `conversation_id` of the `init` event in its own log, which is line one of a created session (P3, KILL). `extract_session_id` gains `conversation_id` in its key tuple. Nothing is pre-assigned, and an antigravity resume does not pre-write the parent's id into the round's `session_id` cache, as the other backends' resumes do. Instead, the round's `meta` records the id it asked for as `resumed_session=<id>`. A round with no `init` in its log (a launch rejection, or a crash before `init`) has no session id, and `resume` refuses it with the existing error.

**R4.2** `resume` continues the parent's session on the parent's backend, worktree, posture, and `read_only`, and the review-round caps apply unchanged.

**R4.3** When a resumed antigravity job's `init` names a different `conversation_id` from its `meta` `resumed_session`, the round started a new conversation without the parent's context. `agy` handles an id it cannot find this way: it warns on stderr, exits 0, and runs under a new id (RUNKNOWN). Then:
- the round's session id is the new conversation's id (R4.1), so a further `resume` continues the round's own work and not the lost parent;
- `status` and `result` print a line saying the round ran without its parent's context;
- `result --json` carries `context_lost: true`. The field is present on every backend: `false` on an antigravity resume whose ids match, and `null` wherever it is not measured (fresh jobs, and every other backend);
- the flow prose tells the driver to resubmit with the full packet rather than resume again.

Acceptance: a fake `agy` that emits an `init` with a different id on resume gives the warning in text, `context_lost: true` in JSON, and the new id as `session_id` in both.

**R4.4** A job killed after `init` can be resumed with its context (KILL, RKILL). A job killed before any event is refused with the existing "no session id" error. A kill between `init` and the first tool call was not probed.

### R5. Monitoring and result

**R5.1** `status` names the last event by its `event` field, plus the `step_type` for a `step_update`. Denials are counted from the `denied_actions` list of the `result` event, and only there. In P0 the denied write was a tool step in state `DONE` with no error, so step states cannot count denials. Before a `result` event exists, the count is unknown, never zero. `status` output:
- a running job with no `result` yet prints no denial line, as a clean job does today, because `status` is polled;
- a finished job (`DONE`, `FAILED`, or `CANCELLED`) whose log has no `result` event prints `permission_denied: unknown (no result event; agy reports denials only when a run ends)`;
- a non-zero count prints the existing line.

**R5.2** `result` on an antigravity job:
- the agent message is the text of the **last `agent_response` step**: the `text_delta` values of every `step_update` with that `step_index`, joined in order. It is never `result.response`, which joins every agent message of the invocation (P3);
- commands are the `tool_info.parameters.CommandLine` of `tool` steps whose `tool_name` is `run_command`, taken from the `ACTIVE` update. `agy` reports no exit code for a command (EXIT7), so none is shown;
- denied tools are named by `denied_actions[].display_name`;
- usage is the sum of the `usage` objects on this log's `step_update` events, and never `result.usage`, which is cumulative over the whole conversation (RSAME). Usage is known only when the log has a `result` event;
- `user_input` and `system_message` steps are skipped;
- `permission_denied` is the length of `denied_actions`. When the log has no `result` event, it is `null` in `--json` output and `unknown` in text output, and `denied_tools` is empty;
- the `errors` list uses the existing error-object shape. A `result` whose status is `ERROR` adds `{"status_code": null, "error_type": "result_error", "message": <result.error, verbatim>}`. A stderr line containing `print timeout after` (TIMEOUTR) adds `{"status_code": null, "error_type": "print_timeout", "message": <that line>}`, because a timed-out run can exit 0 with status `SUCCESS` and nothing else shows it;
- `context_lost` as in R4.3.

Acceptance: text and `--json` output are both checked on three fixtures: a log cut off before its `result` event, a launch rejection (MUNKNOWN's shape), and a lost-session resume (RUNKNOWN's shape).

**R5.3** A non-zero denial count is reported the way it is for claude, copilot, and cursor. `permission_denied` stays advisory, and the job's state still derives from its exit code. On antigravity a run stopped by a denial exits 0 with status `SUCCESS` (P0), so the count is the only signal.

### R6. Setup and smoke

**R6.1** Setup reports antigravity as available exactly when R2.1's resolution succeeds.

**R6.2** The web wizard's antigravity model list comes from `agy --output-format json models` at page load, one option per `command.data.models[]` entry (`id`, `label`). The output flag must come before the subcommand, because after it is rejected (MODELJSON, MJSONGLOBAL). When the CLI is missing, not logged in, times out, or prints something other than that JSON, the page offers no discovered model and names the fix (`agy` sign-in, or `HANDOFF_ANTIGRAVITY_BIN`). No model is ever guessed. `normalize_payload` refuses an antigravity model that the list does not offer, as it does for copilot and cursor.
Acceptance: opening the wizard runs `agy --version`, `agy --help`, and the catalogue command, and no other `agy` command.

**R6.3** In both wizards, an antigravity identity's effort is `model`, set automatically and not editable.

**R6.4** A configured id missing from today's catalogue stays in the form, marked, and is never silently replaced. If discovery failed, the page still shows the failure message and its fix.

**R6.5** Smoke checks an antigravity identity in three steps:
1. the existing dry-run chain;
2. catalogue membership: the configured id must appear in the catalogue, a free call. This catches a typed id from the terminal wizard or `--role-model`;
3. one `agy -p "<sentinel prompt>" --output-format stream-json --model <id>` run with no posture flag. In print mode the default posture denies any tool that needs approval (P0), and the prompt asks only for a reply (DRV_SMOKE).

A pass needs exit 0, `result.status` `SUCCESS`, and the sentinel `HANDOFF_SMOKE_OK` in the last `agent_response` text. A model that `agy` refuses fails with its stderr, verbatim. When the catalogue cannot be read, smoke fails and names the fix. Only a pass writes `verified = true`.

**R6.6** The terminal wizard's backend prompt names all five backends and accepts `antigravity`. It accepts a typed model, so catalogue membership for that path rests on R6.5.

**R6.7** The web wizard shows antigravity like the others:
- a sixth detection tile (the config tile plus five CLIs), with the responsive layout still holding at the widths it supports today;
- a label, "Antigravity";
- an icon from a permissively licensed set, credited in the existing attribution comment, or a neutral glyph if none exists;
- a permission hint saying the posture field changes nothing;
- a sentence in the permission summary (`syncPermissionWarning`) naming every antigravity identity, whatever its posture, as the summary already does for Cursor. Without it, an antigravity identity on `default` would be missing from the summary while its jobs skip every permission check;
- the installed version from `agy --version`.

There is no latest-version lookup or update hint, as for Claude today. Antigravity joins `state["clis"]` directly, as Claude does, and not through the comprehension that indexes `CLI_RELEASES` (`handoff-setup-ui.py:722-732`).

### R7. Evidence

**R7.1** The session receipt goes to **v8**. `antigravity_jobs` and `antigravity_job_durations` are required fields, `receipt_schema_version` is `8`, and `roles_used[].host` accepts `antigravity`.

**R7.2** `make-receipt.py` splits jobs five ways by the `backend=` line in `meta` and takes `--antigravity-jobs` (default `0`). `validate-receipt.py` accepts v8 and rejects v7. `render-cost-receipt.py` reads v8.
Acceptance: a repo with one job per backend produces a receipt with five correct counts, which both `validate-receipt.py` and `render-cost-receipt.py` accept.

**R7.3** The cost receipt folds an antigravity job as follows:
- token counters are the per-invocation sum from R5.2: input, output, and cache-read tokens. Cache writes are unknown, because `agy` reports none;
- reasoning tokens are known, from `thinking_tokens`;
- the USD figure is `None`;
- denials are the length of `denied_actions`;
- the model column shows the slug from `meta`. `init.model` echoes the selector Handoff passed (MBASE), and `agy` emits no display name;
- the cost cell reads "n/a - no cost figure" in the Markdown and the HTML, never "unknown";
- a job with no `result` event has unknown usage, never zero;
- the usage of a parent and its fix round is summed. That is correct here because each log's step usage is per invocation.

Acceptance: a resume fixture whose `result.usage` is cumulative (RSAME's shape) shows only its own steps' tokens, and the chain total equals the parent's plus the round's.

**R7.4** **No antigravity log is read by another backend's parser, and no other backend's log is read by antigravity's.** Every place that branches on backend gains an explicit antigravity branch; the design lists them.
Acceptance: an antigravity job with two denied actions reports 2 in `status`, the cost receipt, and the session view, not 0.

**R7.5** The cost receipt's summary names jobs on the Antigravity meter, shows their summed token counters, and says Antigravity reports tokens but no cost figure. The outside-driver caveat names Antigravity jobs among those counted twice. The denials sentence names claude, copilot, cursor, and antigravity, in the Markdown and in `assets/cost-receipt.html`.

**R7.6** The transcript viewer renders an antigravity log:
- the branch is chosen by `meta.backend`;
- for a dropped log with no metadata, `HV.inferBackend` returns `antigravity` when a parsed line has a top-level `event` key. No other backend's log has one, so this works from line one;
- `step_update` events fold by `step_index`. An `agent_response` becomes an agent row with its deltas joined. A `tool` step becomes one tool row with its parameter (`CommandLine`, `TargetFile`, or `AbsolutePath`), its output, and its outcome (`DONE` or `ERROR` with `tool_info.error.message`);
- `user_input` steps carry no text and are dropped;
- `init`, `system_message`, `error_message`, `command_result`, and `result` become lifecycle rows. The `result` row shows its status, its error, and its denied actions.

**R7.7** The session view's `denials()` gains an antigravity branch. It reports the `denied_actions` display names, and "not reported" when the log has no `result` event.

### R8. Documentation and version

**R8.1** The version is 3.10.0 everywhere it appears (SKILL.md frontmatter, README badges, CHANGELOG, the user guide), bumped together.

**R8.2** SKILL.md's description names all five backends. Four-backend lists are swept from every location in the design's prose inventory, including the user-guide diagrams, which still show three. The eval case `evals/cases/receipt-contract-splits-four-backends.yaml` moves to v8 and five counts. If it is renamed, `evals/eval.yaml` follows.

**R8.3** Four design-of-record docs are updated after implementation to describe what shipped:
- `docs/specs/design_agent-handoff.md`: the "all backends are the same job" invariant, the permission table, the fail-closed table, and the risks;
- `docs/specs/design_agent-identities-and-config.md`: the backend and effort enums;
- `docs/specs/design_agent-handoff-evidence.md`: the backend telemetry table, the normalization table, the receipt contract, the missing-data rules, and the meter descriptions;
- `docs/specs/design_session-visualisation.md`: denial extraction and its verification inventory.

**R8.4** The flow prose states the Antigravity limits where a driver will read them:
- the posture no-op (R3.2);
- read-only rests on plan mode in print mode, and an allow rule in the user's `agy` settings might let a write through (not probed), so check the tree after a read-only antigravity job. If it changed, reject the job's output, save the changes to `.handoff/rejected/<task-id>.patch` and return those paths to their state before the job, as Phase 4 does for a rejected row, and record the anomaly `read-only write`;
- a `print_timeout` error means the output is partial: treat the job as failed, record the anomaly, and resubmit;
- the lost-session warning (R4.3) and what to do about it, and the recovery for a resume round whose model has left the catalogue (R2.6);
- `agy` logs no exit code for a shell command, so a worker's report that a check passed is checked by re-running it.

**R8.5** `bash scripts/check-skill-repo.sh .` adds no new warning lines, and `python3 scripts/run-test-prompts.py` passes.

### R9. Verification

**R9.1** Everything `.github/workflows/checks.yml` runs passes, including the ledger reproducibility check.

**R9.2** The fake-CLI tests cover the wiring:
- the posture flags per posture, and that read-only never carries the skip flag (R3.1);
- the flags that must never appear (R2.4), and `--print-timeout 0s` on every line (R2.7);
- the IDE-launcher trap (R2.1);
- the launch-time model rejection (R2.6), the killed-job fixture (R4.4), and the lost-session warning (R4.3), each in text and `--json` output (R5.2);
- the denial count from `denied_actions`, including a denial whose tool step ended `DONE` (P0's shape) and two actions in one `result`, and the no-fall-through checks (R7.4);
- agent-message selection, and per-invocation usage on a resume fixture (R5.2, R7.3).

These prove wiring, not what a real worker does; R9.3 covers behaviour. The existing shared test surfaces change as well:
- `make_env()` in `tests/test_delegate_role.py` gains a fake `agy`, a decoy IDE-launcher `agy`, and `HANDOFF_ANTIGRAVITY_BIN`;
- `BackendLifecycle.submit_raw()` and `configure_posture()` pass `model` as the effort on antigravity;
- `BackendLifecycle.assert_posture_argv()` gains an explicit antigravity branch before its final `self.fail`;
- `BackendLifecycle.test_model_and_resume_id_are_literal_arguments` (`tests/test_delegate_role.py:495`) swaps the worker for a version-only shim, which the antigravity `--help` check would refuse before quoting is tested. The shim must also answer `--help` for antigravity;
- the shared denial test (`tests/test_delegate_role.py:870-878`) wraps the log in two standalone denial events. Antigravity reports denials only as a list on its terminal `result`, so its subclass supplies one `result` carrying two actions instead;
- the literal source assertions on the cursor effort lock in `tests/test_handoff_setup_ui.py:861` and `:1032` follow the lock when it is keyed by a backend set;
- `test_old_schema_version_fails` (`tests/test_receipt.py:110`) expects the message "must be 7", which becomes "must be 8";
- the v7 receipt fixtures move to v8: `fields()` in `tests/test_receipt.py`, `receipt_text()` and the mixed-backend `CliTests` fixture in `tests/test_cost_receipt.py`, `tests/fixtures/session-view/receipt-20260910T003000Z.md`, and `examples/session-receipt.md`;
- the backend sets in `tests/test_handoff_setup_ui.py` and the backend tuple in `tests/test_handoff_config.py` include antigravity;
- the tests that use `gemini` as their unknown-backend placeholder keep it, because the new backend is named `antigravity`.

**R9.3** A live end-to-end run on a real `agy`, in a scratch repo with an antigravity-backed identity, confirms:
- one expected edit on disk;
- one named repo check that actually ran, with its output in `log.jsonl`;
- `meta` recording `backend=antigravity`;
- a `resume` whose `init` names the parent's conversation id, with its usage counted once, and which runs `pwd` to show that its shell ran in the parent's directory. No probe established that directly: RSAME resumed in the same directory but ran no shell command;
- a `--worktree` job that pins its base and cleans up;
- a `--read-only` job asked to write a file, which leaves the tree unchanged;
- a receipt counting the job under `antigravity_jobs`;
- a cost receipt showing its tokens, reasoning included.

The run records the helper paths it used and the installed revision, because the flow runs an installed copy of the skill.

## Out of scope

- Writing Handoff-owned rules into `agy` settings (`permissions.allow`, `trustedWorkspaces`).
- `--sandbox`, `--mode auto`, and `--mode accept-edits` as postures. Each is a follow-up that needs its own probe.
- The `--effort` flag, and composing a base model name with an effort.
- A latest-version lookup and update hint for `agy` in the wizard.
- An Antigravity preset.
- A cost figure. `agy` reports none, and one must not be made up.
- Consolidating event parsing into one module, and hardening the existing catch-alls that count an unknown backend as codex. Both are follow-ups, recorded in the design.

## Open questions carried into implementation

- **Mid-run authentication expiry and an individual-quota failure.** Not provoked. The capacity failure that did occur exited 3 with status `ERROR`, `result.error`, and an `AGY_ERROR:` line on stderr (RO1). The monitor reads both files, and implementation records the real shapes if one occurs.
- **Allow rules and plan mode.** Whether a `permissions.allow` rule in the user's `agy` settings lets a plan-mode job write. Not probed; R8.4 tells the driver to check the tree.
- **Deny rules.** Whether `agy` has deny rules, and whether they apply under `--dangerously-skip-permissions`. If they do, the bypass warning's remedy can name them.
- **`--mode auto` on an entitled account.** It was unavailable here and fell back to the default posture with a warning (P2, P5).
- **The shell's working directory on resume.** Handoff always resumes in the parent's directory. RSAME did that and recalled its context, but ran no shell command. CWDRESUME resumed from a new directory and its shell followed it. RCWD2, resumed from a second new directory, ran its shell in the first. The cause is unknown, and R9.3's `pwd` check covers Handoff's case.
- **The IDE launcher's real `--help` output.** No IDE was installed for the probes; R2.1's check rests on the two flags the CLI's help prints.
- **A `-p` run while signed out,** and whether the catalogue command needs a sign-in.
