# Changelog

## v4.0.0 (2026-11-12)

### The identity set

- **breaking**: `e2e_specifier` and `e2e_verifier` are retired. One optional identity, `e2e_tester`, writes the acceptance gate in one job and runs it in a second, fresh job. In this repo's own evidence (82 job directories, 18 receipts) neither old identity had run, and in every preset the specifier repeated a core identity's backend, model and effort.
- feat: a new optional identity, `operator`, takes rows whose result comes from running tools and is not a text diff: a long build, a headless render, a media or model API call, an app launched for a visual check. `--with-operator` configures it. It has its own `permission_mode`, so a row that needs the network does not widen `fast_worker`'s posture. When it is not configured, the driver says so once and gives the row to `fast_worker`.
- `deep_reasoner`, `fast_worker` and `arbiter` are unchanged and stay core. A config with those three is still complete.
- Presets: `e2e_tester` takes the old specifier column. `operator` is codex at `high` in balanced and quality, and at `medium` in cost. A preset `operator` runs under `default`; `allow-all` stays an explicit config change.
- Config schema stays `2` and gains no field. Receipt schema stays `8`: the role enum gains `e2e_tester` and `operator` and keeps the two retired names, so a stored receipt still validates.

### Migrating a 3.x project

An existing config keeps working. Run `/agent-handoff config` once to migrate it.

| Situation | What happens |
|---|---|
| A config has `[e2e_specifier]` or `[e2e_verifier]`, bare or in the pre-3.9.2 header form | It loads, and the core identities work. The two sections are ignored and keep their bytes. `resolve`, `get`, `validate`, `set`, `set-review`, `--status` and `--smoke` print one notice on stderr that names the file and the sections |
| `/agent-handoff config`, or `handoff-setup.py --apply` | The e2e toggle is on. `e2e_tester` is filled from the old specifier, or from the verifier when that is the only one. The preview shows the old sections removed and the new one added. Apply backs the file up, and `--rollback` restores it. `e2e_tester` starts `verified = false` |
| A preset (`balanced`, `quality`, `cost`) on the command line | The preset value wins, as it does for the core identities, and a note shows the old triple |
| The global config | The page works on the project file. For the global file run `handoff-setup.py --scope global --preview`, then `--apply` |
| `delegate-job.sh submit --role e2e_specifier` or `e2e_verifier` | Refused. The message names `e2e_tester` |
| A fix round on a job that ran as a retired identity | `resume` still works |
| A goal file written before 4.0.0 | On resume the driver reads both old names as `e2e_tester`. A row with no job yet is rewritten and submitted as `e2e_tester`. A row that has a job keeps it |
| Generated `handoff-e2e-specifier.md` and `handoff-e2e-verifier.md` | Removed by `--uninstall`, and by an apply that writes agents, when they still match the manifest. A file you edited is left alone and reported |
| A managed routing block that names a retired agent | Refreshed by the same apply, inside its own markers |
| `--uninstall --remove-config` | Clears the retired sections too |
| A retired section that is defined twice or does not validate | Setup refuses the migration and names the header. Fix or delete the section by hand |
| A v3.12 engine reading a v4 file | It ignores `[e2e_tester]` and `[operator]`, and `validate` passes. `--role e2e_tester` and `--role operator` are refused, so a job fails closed and validation does not |

- fix: the wizard keeps an optional identity the config already has. Its e2e checkbox used to start unchecked whatever the config held, so an apply from the page removed existing e2e sections. Each optional identity now has its own toggle, pre-checked from the config.
- fix: the wizard rebuilds its state after an install. It used to serve the snapshot taken when it started, so a reload after adding an identity showed it absent.
- fix: `handoff-setup.py --apply` prints the plan's notes. It used to print only the file actions and the backup.
- feat: `--with-e2e` and `--with-operator` default to what the config has. An explicit `--no-with-e2e` or `--no-with-operator` removes the identity.

### Rows that are not a text diff

- feat: `references/artefact-rows.md` holds the rules for a row whose result is not a text diff, whose command outlasts a monitor tick, or that calls a paid service. It loads only when a run has such a row, and on a resume when the goal file has evidence rows or a `## Spend` block. A plain software run loads nothing new.
- feat: acceptance is evidence. The packet names a new output directory and what the worker must leave under `.handoff/evidence/<task-id>/`: a probe of each output, the exit status of each long command, and keyframes or a contact sheet for anything visual. The driver reads those files and opens the images itself. A worker never overwrites an existing file.
- feat: what no model can judge, such as how a voice-over sounds, is the user's sign-off. The row stays in `review` until the user answers, and an arbiter's approval never stands in for it.
- feat: a long command runs in the background, with its exit status written to a file, and the worker checks on it at least every two minutes. Those checks are events in the job's own log, so the stall rule in Phase 3 is unchanged and a healthy 40-minute render never looks stalled. A row states an expected duration and a runtime cap.
- feat: no paid call is made until the user has agreed to a cap. `SKILL.md` states this among the rules that always hold. The goal file gains a `## Spend` block with the user's go-ahead and one allocation per paid row. The worker logs each call to `spend.jsonl` before it is made and adds the provider's id and the outcome after. A call with no recorded outcome stops further spending until the driver has settled it against the provider's record.
- feat: a paid-call packet replaces the "touch no secrets" line with one named environment variable, one service, and the files that may be uploaded. Every other secret stays off limits.
- docs: a worker inherits the driving session's environment, API keys included. A claude-backed worker runs without `ANTHROPIC_*` and `CLAUDE_CODE_*`. This was true before and was not written down.
- feat: the goal table's `effort` column has a meaning: `-` is the identity's configured effort, and a value is passed to `submit` as `--effort`.
- feat: `/agent-handoff tryout` gives a configured `operator` a fourth task: a background command it checks on, and a line that says whether the network is reachable.
- The skill's description says "project work" where it said "coding work".

### What was run, and what was not

- A real run in a scratch project, with `operator` on codex under `default`: a 20-second 1280x720 clip and its contact sheet, an 11-minute real-time capture, and a `cargo build --release`. The driver accepted each row on its probe files, its own checksums and the opened contact sheet. The worker logged 13 checks during the capture, the longest gap 69 seconds. The receipt validates and names `operator`.
- That run found two things, both fixed here. The Codex sandbox denies a process probe, so the long-command rule accepts the exit-status file as the check. And naming `operator` in the trigger description made a Codex worker load this skill for its own packet, so the description does not list it.
- **Provisional:** no paid API was called. The paid-call rules are covered by test prompts and one eval case only. Treat them as unproven until a real run has spent against a cap.
- **Not included:** worktrees for ordinary rows. Only an e2e packet lets a worker commit, so an ordinary worktree row has no completion contract yet.
- **Withdrawn:** a documented `deep_reasoner --read-only` second-read row. It went in, the eval run showed sessions routing a security review through it, and it came out again.

### Evals

Sonnet 5.5, two iterations per case, v3.12.0 measured before any edit and v4.0.0 on the released tree. A score is passes out of the trials that did not error.

| Case | v3.12.0 | v4.0.0 | Note |
|---|---|---|---|
| `transcript-renders-not-delegates` | 2 of 2 | 2 of 2 | |
| `cost-receipt-renders-not-delegates` | 2 of 2 | 2 of 2 | |
| `migrate-plans-then-applies` | 2 of 2 | 2 of 2 | |
| `flow-loads-by-layer` | 2 of 2 | 2 of 2 | also checks that `artefact-rows.md` is not read |
| `resume-loads-recorded-backend` | 2 of 2 | 2 of 2 | |
| `tryout-loads-backends-first` | 2 of 2 | 2 of 2 | |
| `unrelated-handoff-doc-no-trigger` | 1 of 1 | 2 of 2 | one v3.12.0 session errored |
| `code-review-no-trigger` | 2 of 2 | 2 of 2 | |
| `no-fabricated-token-savings` | 2 of 2 | 2 of 2 | |
| `driver-keeps-quality-critical-work` | 1 of 2 | 0 of 2 | six more trials on each version: 0 of 6 and 0 of 6 |
| `identity-not-swapped-for-meter` | 0 of 2 | 0 of 2 | |
| `review-cap-escalates-to-arbiter` | 2 of 2 | 2 of 2 | |
| `e2e-added-for-user-observable-change` | 1 of 2 | 2 of 2 | judge changed: two `e2e_tester` rows in place of the old pair |
| `receipt-contract-splits-five-backends` | 2 of 2 | 2 of 2 | |
| `operator-row-for-tool-run` | | 2 of 2 | new |
| `artefact-review-reads-evidence` | | 2 of 2 | new |
| `paid-call-needs-recorded-cap` | | 0 of 2 | new; 1 of 2 after its prompt was reworded |

- On the 14 cases both versions share, v3.12.0 passed 23 of 27 measured sessions and v4.0.0 passed 24 of 28.
- `driver-keeps-quality-critical-work` reads as a regression at two trials and is not one at six: it fails on both versions. One session hands the signing code to `fast_worker`. Others keep signing and sign-off and route the security review through `deep_reasoner` before judging it, which the flow does not forbid and the judge counts as delegated. This release does not fix it.
- `identity-not-swapped-for-meter` still fails on both versions.
- `paid-call-needs-recorded-cap` failed twice in the full run because neither session loaded the skill: the prompt asked what the driver needed, the fixture has no video and no key, and both sessions reported those and stopped. With the prompt asking for the split, one of two sessions passed and the other asked for a cap without naming the `## Spend` block.
- The judge changed between the two runs in one more way: it now follows a file read through `cd` and through a loop variable. The first v4.0.0 run failed `resume-loads-recorded-backend` and `artefact-review-reads-evidence` once each on reads it had missed. This was a known false verdict in v3.12.0.
- 17 cases (was 14). `judge.py --self-test` checks 590 assertions.

### Found and left alone

- The web wizard cannot open on a schema v1 config, though the v1 error sends the user to it. `references/setup-wizard.md` now names the terminal path for that file.
- `validate-verdict.py` accepts a PASS whose `command` is null.
- `handoff-config.py resolve --override` cannot be reached from `delegate-job.sh`.

## v3.12.0 (2026-11-11)

### The skill loads by layer

- **breaking**: `SKILL.md` is now a router. It holds the rules that always apply, where the scripts are, a command table and a reference map, in 990 words (was 3,231). Each command loads one reference, and that reference names further files at the step that needs them. `docs/skill-structure.md` has the map, and `docs/skill-structure.svg` draws it.
- **breaking**: the references are renamed for what they are for. A link to an old name breaks.

  | v3.11.1 | v3.12.0 |
  |---|---|
  | `references/claude-driven.md` | `references/delegation-flow.md` |
  | `references/handoff-template.md` | `references/packet-templates.md` |
  | `references/goal-template.md` | `references/goal-file-template.md` |
  | `references/goal-to-pr.md` | `references/pr-delivery.md` |
  | `references/memory-protocol.md` | `references/wrap-up-memory.md` |
  | `references/darwin-ratchet.md` | `references/skill-change-gate.md` |
  | `references/e2e-gauntlet.md` | `references/e2e-acceptance.md` |
  | `references/setup.md` | `references/setup-wizard.md` |
  | `references/tryout.md` | `references/identity-tryout.md` |
  | `references/fable5-principles.md` | removed; its packet rules are in `packet-templates.md`, its effort and channel rules in `delegation-flow.md` |
  | `docs/skill-file-structure.svg` | `docs/skill-structure.svg` |

- feat: new references hold what used to sit in `SKILL.md` or in the flow. `session-evidence.md` has the transcript, cost-receipt, visualise and migrate commands. `session-receipt.md` has the receipt contract and the reporting list. `arbitration.md` has both arbiter protocols and the Arbitration Packet. The Goal Packet moved to `pr-delivery.md`.
- feat: backend rules moved to `references/backends/<backend>.md`, one file per CLI: its preflight check, what the permission modes mean, read-only limits and failure signs. The flow reads only the files for backends in use. A resumed run also reads the file for each existing job's recorded backend, and a tryout reads them before it submits.
- Measured with `wc -w`: a delegated run on codex and claude loads 6,040 words (was 12,311), `/agent-handoff transcript` 1,829 (was 3,231), `/agent-handoff config` 2,561 (was 4,778). The whole surface is 13,310 words (was 18,035).
- fix: the description is 1,018 characters. It was 1,583, over the 1,024 limit, and Claude Code's skill listing cut off its last sentence, the rule against triggering on the bare word "handoff". It now also triggers on a request that names a Handoff identity.
- Config schema stays `2`. Receipt schema stays `8`. No script changes behaviour: two error messages and one docstring name a renamed reference.

### Rules that changed

- **breaking**: Phase 0 no longer asks to append `DO NOT send optional commentary` to the target repo's `AGENTS.md`. Every packet already ends with that rule.
- **breaking**: Phase 4 no longer offers the optional gstack `/codex` review of the final diff.
- **breaking**: the wrap-up memory step keeps Claude Code auto-memory only. The claude-mem, mem0 and Codex rollout layers are gone. Packets still end with the lessons-learned line.
- removed: the full protocol's "do not create `.handoff/goal.md` for a one-off small change". It contradicted Phase 1, which always writes the goal file, and sat in a file the default path never loaded.
- removed: prose that repeated what a script prints or enforces by itself: the lane-map drawing contract (`handoff-session-ui.py` prints it), per-backend command lines and flag mappings, the setup page's detection detail, the verdict's cross-field rules (`validate-verdict.py` reports them), and version history. `docs/specs/` keeps the design record.
- fix: setup and tryout stamp the session start before they run. Both were told to close with a receipt, and neither stamped a start for it to measure from.
- fix: under the full protocol, Stage 1 writes a goal file holding only the Goal line, the Checkpoint Rule and the `## Spec Review` block. A review that fails or is interrupted before Stage 2 now leaves its state on disk.
- fix: on a resume, only an arbitration that ended in `no verdict` waits for the user. An arbiter job still running, or still owed its one retry, is finished.
- fix: `max` is no longer described as codex-only. `delegate-job.sh` accepts it on claude and copilot too; only `ultra` is codex-only.
- fix: a receipt's `roles_used` writes the claude backend as `host: claude_code`, as the schema requires.
- fix: the tryout says it covers the three core identities, and writes `verified` to the config file that defines the identity.
- docs: a user who asks for an identity swap is asking for a config change. The driver names it and offers it, and does not re-route the task.

### The gate holds the structure

- feat: `check-skill-repo.sh` has four new checks. The description is at most 1,024 characters. The `SKILL.md` body is at most 900 words. Every file under `references/` is named in `SKILL.md` and in `docs/skill-structure.md`, and every named file exists. `SKILL.md` and `references/` name no path under `docs/` or `tests/`, and no script names a file under `docs/specs/`.
- The receipt-contract check reads `references/session-receipt.md`.

### Evals

- feat: 14 cases (was 8), each with a fixture written through `context.files`. One script, `evals/fixtures/scripts/judge.py`, judges 13 of them from the workspace and the session's tool trace: the rendered page holds the fixture's own text, no job directory was added, the plan ran before `--apply`, and which skill files the session read. `python3 evals/fixtures/scripts/judge.py --self-test` checks the judge itself.
- feat: new cases: `migrate-plans-then-applies`, `flow-loads-by-layer`, `resume-loads-recorded-backend`, `tryout-loads-backends-first`, `code-review-no-trigger`, `e2e-added-for-user-observable-change`.
- fix: the old judges matched vocabulary. `driver-keeps-quality-critical-work` passed a reply that delegated the signing logic, and `transcript-renders-not-delegates` failed a correct run that never typed the script's name.
- fix: `eval.yaml` installs only `SKILL.md`, `references/`, `scripts/` and the page templates. It used to install `test-prompts.json`, which holds the expected behaviour for near-identical prompts. The engine is pinned to Sonnet 5.5, the supported floor.
- feat: `evals/run-evals.sh` runs the suite from a clean environment, so the nested `claude` sessions do not inherit the caller's `ANTHROPIC_*` and `CLAUDE_CODE_*` variables.
- note: no case uses `agent_judge`. On skill-up 0.11.0 it errored on every case here.
- Measured on Sonnet 5.5, two iterations per case: v3.11.1 passed 15 of 26 measured sessions, v3.12.0 passed 23 of 28. No case passed on v3.11.1 and failed on v3.12.0. Three went from failing to passing: `flow-loads-by-layer`, `tryout-loads-backends-first` (its prompt changed between the two runs) and `e2e-added-for-user-observable-change` (one measured iteration on v3.11.1). Two v3.11.1 sessions hit the account's rate limit and are left out.
- Still failing on v3.12.0: `identity-not-swapped-for-meter` (2 of 2; it also fails on v3.11.1), `driver-keeps-quality-critical-work` (2 of 2; one is a judge false positive) and `resume-loads-recorded-backend` (1 of 2). The judge has three known false verdicts: a file read through a shell loop variable, a test row whose note says "implements", and a keep phrase outside its vocabulary.
- note: a personal install at `~/.claude/skills/agent-handoff` outranks the copy the suite installs. On a machine that has one, the suite measures that install.

## v3.11.1 (2026-10-31)

### `/agent-handoff migrate`

- feat: `/agent-handoff migrate [<repo>]` runs `scripts/migrate-sessions.py` from the skill. The driver runs the plan first and shows its summary and every `rewrite`, `skip` and `leave` line. It then applies the plan and runs the printed `re-render:` commands. When anything was skipped or left behind, it reports the files and stops. The script itself is unchanged.
- docs: the skill's description lists the new trigger. README's upgrade note, the user guide's command table and the test prompts name the sub-command.
- Config schema stays `2`. Receipt schema stays `8`.

## v3.11.0 (2026-10-31)

### One folder per session, and the session id comes from the environment

- **breaking**: all of a session's evidence moves into `.handoff/sessions/<date>_<session_id>/`. It replaces the four folders `receipts/`, `receipts/diagram/`, `cost-receipts/` and `transcripts/` under `.handoff/`. The files are `receipt-<stamp>.md`, `cost-receipt-<stamp>.{md,html}`, `transcript-<jobId>.html` and `diagram/session-timeline-<stamp>.svg`. The folder name is a date and the receipt's `claude_session`, as in `20261008_1c9ced0c-a654-4734-a856-d98a6aa1f7de`, so a plain listing sorts by day. The date is the UTC day the session's folder was first written and does not change afterwards, so a session running past midnight stays in one folder. A `none` receipt goes to one `sessions/none/` folder with no date. The run stamp stays in every file name, because one session can hold several runs. A transcript goes to the session of the newest receipt that lists its job in a `*_job_durations` field; with no such receipt it goes to the current `CLAUDE_CODE_SESSION_ID`, and to `none` outside Claude Code. `jobs/`, `goal.md`, `session-start`, `rejected/`, `e2e/`, `worktrees/`, `probes/` and `backups/` do not move.
- **breaking**: nothing reads `receipts/`, `cost-receipts/` or `transcripts/` any more. Where a reader finds nothing under `sessions/` and one of those folders exists, its error names the migration command. `scripts/migrate-sessions.py --repo <repo>` prints the plan and writes nothing; add `--apply` to carry it out. It never overwrites a file. A legacy receipt that reads `claude_session: none` is rewritten to a session id only when its `saved: <path>` line matches exactly one Claude Code session under `~/.claude/projects`. Otherwise it is filed under `sessions/none/` as it is. For each cost receipt rendered before such a rewrite, it prints the command that renders it again.
- fix: `make-receipt.py` reads `claude_session` from `CLAUDE_CODE_SESSION_ID`, which Claude Code sets in every Bash tool subprocess, and `--claude-session` is optional. The runtime prose used to say "`claude_session` is the current session" and gave the driver no way to read it. In the maintainer's local data, 7 of 53 saved receipts read `none`, and Claude Code drove every one of those runs.
- fix: `make-receipt.py` refuses what used to be recorded silently. With the flag omitted and the variable unset, it asks for an id, or `none` for a run no Claude Code session drove. `--claude-session none` while the variable is set is refused, and the message says to omit the flag. An explicit id still wins, which is how a past run's receipt is regenerated with `--ended-at`. It also refuses an id that is not a safe folder name. Every writer, this one included, refuses a symlink at `.handoff`, at `sessions`, at the session folder, at `diagram`, or at the file it writes.
- note: `claude_session` is now `none` or `[A-Za-z0-9][A-Za-z0-9._-]*` in full. `validate-receipt.py` and `assets/receipt-schema.json` enforce it. Receipt schema stays `8`, because every stored receipt already conforms.
- note: the diagram is now `diagram/session-timeline-<stamp>.svg` in the session folder. It replaces `receipt-<stamp>_handoff-session-timeline.svg`, and the session page no longer looks for the old names. The migration renames them.
- docs: the skill, the references, README, the user guide, its two diagrams, the file-reference diagram, the test prompts and the cost-receipt eval case use the new paths. README has an upgrade note with the migration command. Implemented plans under `docs/specs/` and `examples/v3.6.1-conversation-cost-receipt.{md,html}` keep the old paths, because they record what ran at the time.
- Config schema stays `2`. Receipt schema stays `8`.

## v3.10.1 (2026-10-26)

### `delegate-codex.sh` is now `delegate-job.sh`

- **breaking**: `scripts/delegate-codex.sh` is renamed `scripts/delegate-job.sh`. It has run jobs on more than codex since v3.5.0, and on five backends since v3.10.0. Its subcommands, flags, output and job state under `.handoff/jobs/` are unchanged. A job submitted under the old name reads and resumes with the new one. No copy stays at the old path, so a caller of `delegate-codex.sh` gets "No such file".
- refactor: the script's header, its usage text and `CLAUDE.md` no longer say the name is historical. The `CODEX_BIN`, `CODEX_BIN_SOURCE` and `CODEX_VERSION` variables keep their names. So do the `codex_bin`, `codex_bin_source` and `codex_version` meta keys, because `resume` reads `codex_bin` from every parent job. A comment now says this.
- fix: nine comments in the script had drifted from the code, and now match it. Three examples follow. The usage text listed `explicit` as a `permission_posture_source`, which nothing writes. It also said every Copilot binary check fails closed, but a fix round with a stale recorded path runs discovery again. And the worktree comment in `resume` named only codex and claude; all five backends take the fix round's cwd from the shell.
- docs: the skill, the references, README, `CLAUDE.md`, the design docs and the test prompts use the new name. So do the no-trigger eval, CI, the SkillSpector baseline paths and the file-reference diagram. Implemented plans under `docs/specs/` and the research notes keep the old name, because they record what ran at the time.
- Config schema stays `2`. Receipt schema stays `8`.

## v3.10.0 (2026-10-15)

### Antigravity CLI is the fifth backend

- feat: `antigravity` is a fifth `backend` value. An antigravity-backed identity runs `agy -p --output-format stream-json` as a durable job with the same jobId, job directory, monitor loop, bounded `resume`, worktree lifecycle, and receipt evidence as the other four. The binary is `agy`, never `antigravity`, which is the IDE launcher; `HANDOFF_ANTIGRAVITY_BIN` overrides it. Handoff accepts a binary only when its `--help` lists both `--conversation` and `--dangerously-skip-permissions`.
- feat: an Antigravity model is a whole `agy` catalogue id; effort is always `model`. Setup and submit refuse other efforts, an empty model, and `model = "auto"`, and the refusal names `handoff-config.py set`.
- feat: the web wizard reads `agy --output-format json models` on page load and has an Antigravity tile that says LISTED, NO MODELS or MISSING. The terminal wizard takes a typed id. Smoke checks the id against the catalogue, then runs one real `agy -p` with no posture flag and requires status `SUCCESS` and the `HANDOFF_SMOKE_OK` sentinel. There is no latest-version lookup, and no preset names an antigravity identity.
- **breaking**: receipt schema v8 adds `antigravity_jobs` and `antigravity_job_durations`. A v7 receipt no longer validates; regenerate it with `make-receipt.py`, passing `--antigravity-jobs`.
- feat: the cost receipt shows Antigravity token counters summed from each step's own usage, never from the conversation-wide `result.usage`. Reasoning comes from `thinking_tokens`, cache writes read `unknown`, and the cost reads "n/a - no cost figure". Its card is "Ran on the Antigravity meter".
- feat: the transcript viewer renders Antigravity logs: one row per step, with a step's text chunks joined and each tool call paired with its outcome. The session view reports the result's denied actions.
- feat: `status` and `result` take denials only from the `result` event's `denied_actions`. A finished job whose log has no `result` event prints `permission_denied: unknown`, and `result --json` gives `null`. A run stopped by a denial still exits 0 with `SUCCESS`, so the count is the only signal.
- feat: `result` takes the agent message from the last `agent_response` step and lists commands from `run_command` steps. `agy` logs no exit code for a shell command, so a worker's report that a check passed is checked by re-running it.
- feat: a resume passes `--conversation <id>` with the parent's model and posture again, and its `meta` records `resumed_session=<id>`. When `agy` cannot find the conversation it exits 0 and starts a new one; `status` and `result` then report `context_lost: true`, and the fix round is resubmitted with the full packet.
- feat: a model rejected at launch gives a FAILED job with one `result` event of status `ERROR` and no session id. `resume` refuses it and never substitutes a model. Recovery is `handoff-config.py set` naming a catalogue model, then a fresh `submit` with the full packet.
- feat: every job passes `--print-timeout 0s`. A `print_timeout` line on stderr appears under `errors` in `result --json`, and the driver treats that job as failed.
- note: on Antigravity, `default` and `allow-all` both pass `--dangerously-skip-permissions`, so the posture field changes nothing and Handoff knows of no rule that narrows it. Handoff warns for each writing job and writes nothing under `~/.gemini/`.
- note: read-only passes `--mode plan` and never the skip flag, because a probed worker given both wrote files. It therefore rests on plan mode in print mode, and an allow rule in the user's `agy` settings might let a write through (not probed). The driver checks the working tree after a read-only job; on a change it rejects the output, saves it to `.handoff/rejected/<task-id>.patch`, restores the paths, and records the anomaly `read-only write`.
- docs: the skill, README, user guide, four diagrams, promo page, references and design-of-record docs now describe five backends and receipt v8. The eval case `receipt-contract-splits-four-backends` is renamed `receipt-contract-splits-five-backends`, parses again, and requires schema v8. `test-prompts.json` gains six Antigravity cases.

## v3.9.5 (2026-10-13)

### `always_on_host_rules` is removed

- **breaking**: the `routing.always_on_host_rules` config key is gone. Nothing read it. Every new config got `false`, and the managed routing block has always been switched by `--routing-block` and `--remove-routing-block`. `handoff-config.py get routing.always_on_host_rules` and the same `resolve` key now answer `key not found`, and `resolve` output has no `routing` table.
- feat: a new config has no `[routing]` section. `[review]` follows the identity sections directly.
- note: an existing `[routing]` section in a schema v2 file is now an unknown section. It loads, a write keeps its bytes, and it can be deleted by hand. The engine no longer checks it, so a non-boolean value, a second `[routing]` section or a `[[routing]]` header is no longer refused.
- note: the managed routing block and its two flags are unchanged.
- Config schema stays `2`.

## v3.9.4 (2026-10-11)

### The setup wizard shows each CLI's icon

- feat: each identity's `Runs on` dropdown has the selected CLI's icon to its right: Claude Code, Codex, GitHub Copilot or Cursor. The icon changes with the selection. The Claude Code, Codex, GitHub Copilot and Cursor tiles in the environment strip show the same icons before their names. Claude, GitHub Copilot and Cursor marks come from simple-icons (CC0), and the Codex mark from lobe-icons (MIT). They are inline SVG, because the page's Content-Security-Policy loads no images, and screen readers skip them, since the dropdown already names the CLI.
- refactor: the wizard page moved out of `scripts/handoff-setup-ui.py` into `assets/setup-ui.html`, the way the session view keeps `assets/session-view.html`.`check-skill-repo.sh` requires the new file.
- fix: paragraphs in the Identities pane fill the pane's width instead of stopping at 68 characters. Adjust the size of the model and effort columns for better fit.

## v3.9.3 (2026-10-10)

### The setup wizard says when a CLI is missing or out of date

- feat: the Codex, GitHub Copilot and Cursor tiles say UPDATE when the installed CLI is older than the newest release, and MISSING when it is not installed. Codex and Copilot releases come from the npm registry (`@openai/codex`, `@github/copilot`). Cursor has no registry entry, so the page reads the build its install script names, at `https://cursor.com/install`. The requests send no credentials, time out after 3 seconds and run while the local probes run.
- feat: versions compare by their leading dotted number, so `codex-cli 0.147.0` is older than `0.162.1`. When either side can't be read, the status is `unknown` and the tile makes no claim. It still shows FOUND or LISTED then, so neither word means up to date.
- feat: Copilot and Cursor tiles show MISSING first, then NO MODELS when the model list failed, then UPDATE, else LISTED.
- feat: a missing or outdated CLI gets a `How to install` or `How to update` button, even under NO MODELS. Its popover gives the command, such as `npm install -g @github/copilot` or `cursor-agent update`, plus the detected binary path for an outdated CLI, a note when a `HANDOFF_*_BIN` override is set, and a link to the official install guide.
- fix: the wizard no longer fails to start when a CLI prints nothing for `--version`.
- note: opening the wizard now runs `copilot --version` and `cursor-agent --version`. Neither starts a session or costs a premium request. This revises v3.9.1's "runs no new command". The Copilot model catalogue read still spawns no `copilot` process.

## v3.9.2 (2026-10-09)

### Identity sections drop the dual-host prefix

- feat: identity sections are bare top-level tables: `[deep_reasoner]`, `[fast_worker]`, `[arbiter]`, `[e2e_specifier]` and `[e2e_verifier]`. `handoff-config.py get` and `resolve` keys drop the prefix too, so the key is `deep_reasoner.backend`. Fields, field order, `[review]` and `[routing]` are unchanged.
- feat: a file with `[hosts.claude_code.identities.<identity>]` headers loads with the same values, and reading never rewrites it. The next write re-emits every identity section bare, in the place where the first one stood. The writes are `set`, `set-review`, setup apply, smoke's verification write and `--uninstall --remove-config`. Every other section keeps its bytes, including stale `hosts.codex.*` blocks.
- **breaking**: the old key `hosts.claude_code.identities.deep_reasoner.backend` no longer resolves, and no alias exists. A file that defines one identity twice is refused whichever header form each section uses, and the error names both headers. Delete one of the two sections by hand.
- note: a misspelt bare header such as `[deep_reasonr]` is an unknown section, so `validate` does not catch it. The file keeps it untouched, and resolution takes that identity from the next layer if one defines it. A misspelt legacy header is still refused.
- note: an engine older than 3.9.2 reads only the prefixed headers, so the identities in a file with bare sections are invisible to it. If no other layer configures the role, `delegate-codex.sh` refuses it. If the global config still has a legacy-form identity, the old engine runs the global values, and `config_source` still says `project`. That case does not fail closed.
- Config schema stays `2`.

## v3.9.1 (2026-10-09)

### The setup wizard uses the session view's design

- feat: the web wizard now follows `DESIGN.md`, like the session view. It has one serif heading, monospace capital labels, and flat cards with 1px borders. Cobalt marks focus and selection, and coral marks warnings. The hero map, the noise texture, the glow and the shadows are removed. The first work-mode tile is visible without scrolling at 1440×900 and 390×844.
- feat: the environment strip shows all four backends, and each status pill holds a word. Claude Code and Codex show FOUND or MISSING from their version check. GitHub Copilot and Cursor show LISTED or NO MODELS from the model lists the page already reads, so opening the wizard runs no new command.
- feat: the preview lists the files that the engine plans to write, with the state of each: WRITE, DELETE, UNCHANGED or REFUSED. One row per identity follows. A failed preview has the title "Preview failed" and shows the refusal. The page ignores a preview response for a selection that changed while the request ran.
- feat: an identity with Allow all shows a coral "Allow all" pill. A hint under each permission switch says what the setting permits on that backend. The install panel names each identity that runs with Allow all. It also names each identity on Cursor, because Cursor runs writing jobs in force mode with either setting.
- feat: a Cursor model is typed into a field with a `<datalist>` of the `cursor-agent models` slugs. A slug that is not in the list blocks the preview. Model options on all backends show the slug first.
- feat: a Custom tile joins the three presets. When a preset replaces custom settings, the status line offers Undo.
- fix: a review-pass field shows an inline error when its value is not a whole number of 1 or more. The error links to the field through `aria-invalid` and `aria-describedby`. Focus moves to the preview heading when the preview renders, and to the result heading after install.
- fix: the identity count follows the e2e toggle. Before, the page always said "three roles".
- fix: the permission switch keeps one size when it flips, so the row no longer moves. The disabled Install button says why it is disabled. Only the capital labels and the status pills use 11px text.

## v3.9.0 (2026-09-30)

### Cursor CLI is the fourth backend

- feat: `cursor` is a fourth `backend` value. A cursor-backed identity runs `cursor-agent -p --output-format stream-json` as a durable job with the same jobId, job directory, monitor loop, bounded `resume`, worktree lifecycle, and receipt evidence as the other three.
- feat: a Cursor model is a whole catalogue slug, such as `claude-opus-5-5-high` or `composer-2.5`; effort is always `model`. Setup and submit refuse other efforts and `model = "auto"`.
- feat: the web wizard reads `cursor-agent models` on page load. The terminal wizard takes a typed slug. Smoke checks the slug against the catalogue before one paid read-only run, since Cursor may silently use a different variant for a base name it does not list.
- **breaking**: receipt schema v7 adds `cursor_jobs` and `cursor_job_durations`. A v6 receipt no longer validates; regenerate it with `make-receipt.py`.
- feat: the cost receipt shows Cursor token counters, no cost figure, and the model that ran from the job's `init` event when available.
- feat: the transcript viewer renders Cursor logs, drops `thinking` events, and pairs tool calls with their outcomes.
- note: on Cursor, `default` and `allow-all` both run in Cursor's force mode. Deny rules in the user's or project's `.cursor/cli.json` are the only narrowing; Handoff does not write there and warns for each writing job. Read-only uses `--mode plan`.

### The Cursor card shows its tokens

- fix: the cost receipt's "Ran on the Cursor meter" card shows the cursor jobs' summed token counters. Before, it showed only the job count, and the tokens appeared only in the job table. There is still no cost figure, and reasoning reads `unknown` because Cursor emits no reasoning count.
- feat: the Codex, Copilot and Cursor cards set their counters in small monospace type. Only the outside-driver figure and the driver session use large serif numbers. The outside-driver figure uses them for a partial total too, with the measured-job count underneath.
- feat: "Ran outside the driver session" moves below the Copilot and Cursor cards, directly above the caveat card. The Markdown summary uses the same order.
- fix: the caveat says Codex and Cursor jobs are each counted in their own figure and again in the outside-driver figure. Before, it named only Codex.
- fix: the caveat card is renamed from "Reading the two figures" to "Reading the figures", since the summary can now show three token figures. Its two paragraphs use the full card width. Before, each wrapped onto three short lines.

## v3.8.1 (2026-09-12)

### Copilot models are picked, not typed

- feat: the setup wizard reads the models the authenticated account is entitled to and offers them in a dropdown, matching the Codex and Claude experience. Each model carries its accepted reasoning efforts. Opening the wizard costs no premium request.
- **breaking**: apply no longer validates Copilot model–effort pairs against the CLI before writing. Validation still runs on smoke. The three backends are now symmetric.
- feat: the wizard refuses an unrecognised model before building a preview. No catalogue means no Copilot model offered — never a typed fallback. A preserved Copilot identity stays selectable offline.

### Wrap up leaves the receipt on disk

- feat: wrap up writes the session receipt to disk by default; `--no-save` opts out. Previously whether a run persisted its receipt depended on an undocumented flag.
- fix: a claim that the receipt was re-validated after writing was dropped from docs — validation is one pass before the write.

## v3.8.0 (2026-09-11)

### Two review gates, one shape

- **breaking**: spec review runs on every plan. The previous opt-in toggle is retired; old configs still load and the toggle is removed on next write.
- feat: a `[review]` config section with two round caps — spec (default 1) and implementation (default 3). A round is one review pass; the original job counts as pass one. Configurable via CLI, setup engine, or the wizard.
- feat: resume enforces the round cap. The count is derived from the job chain, not from naming conventions.
- feat: at the cap without consensus, the full dispute goes to the arbiter, whose ruling binds. An approval can overrule the driver but never replaces an e2e PASS. A rejection is final. A failed arbiter job gets one resubmit.
- feat: spec review findings are tagged blocking or advisory; only a declined blocking finding escalates. A resumed session finishes an open review chain instead of misreading it as done.
- Every ruling is recorded in the goal file and in the receipt's anomalies line.

## v3.7.2 (2026-09-10)

- feat: add `/agent-handoff visualise` (also `visualize`) — a single loopback page with a narrative SVG overview, transcript, and cost-receipt in authenticated same-origin frames.
- A diagram with no lane map now reports that so the driver can offer to fix it in place.
- Validate lane maps and reconcile declared windows with measured job evidence; surface per-lane failures, axis gaps, and missing-diagram fallbacks.
- Gate the page on its assets: a missing timeline diagram exits with the target path and a generation prompt rather than opening an empty tab.
- The generation prompt now carries presentation requirements: light theme, 12 px minimum font, clock labels in local timezone, one sub-timeline per identity.
- Add receipt-associated goal facts, joined permission denials, measured anomalies, and a transcript Git-ignore banner.

## v3.7.1 (2026-09-10)

- fix: the transcript and cost-receipt renderers crashed when `--repo` was relative. Fixed and tested.
- fix: receipt generation now checks that declared job counts match measured durations, catching a silent mismatch that could omit a job from the cost report.
- feat: `--ended-at <ISO8601>` pins the receipt time so a past run's receipt can be regenerated without duration growing to today.
- feat: per-identity `permission_mode` (`default` or `allow-all`) across config, delegation, and setup. Claude default uses dontAsk with the worker tool set; Codex and Copilot defaults stay unchanged.
- Resume preserves parent read-only state and posture. Requested and effective mode are recorded in job metadata.

## v3.7.0 (2026-09-09)

### A third execution backend: GitHub Copilot CLI

- feat: `backend = "copilot"` is a fully supported execution backend with the same job lifecycle, monitoring, fix rounds, worktree support, and receipt evidence as Codex and Claude.
- feat: the copilot binary is identified by its `--version` output to avoid confusion with AWS Copilot CLI. An override env var is available.
- feat: a copilot job's session id is assigned at submit, so a job that dies mid-run can still be resumed.
- fix: `--read-only` now correctly uses `--mode plan` rather than combining it with `--allow-all-tools`, which silently failed — the agent reported success while executing nothing.
- feat: delegated copilot jobs run with `--allow-all-tools`. Denial events are counted, and any non-zero count marks the job as failed.
- feat: every generated copilot command blocks remote export and memory. No `--share`, `--yolo`, or `--allow-all` is ever generated.
- feat: the effort enum is Copilot's own (`none` through `max`). Rejection is surfaced, not silently downgraded. `model = "auto"` is refused for configured identities.
- feat: the setup wizard renders Copilot's model as a text field. Opening the wizard costs nothing; apply and smoke validate against the real CLI.
- **breaking**: receipt schema v6 adds `copilot_jobs` and `copilot_job_durations`. A v5 receipt fails validation — regenerate it.
- feat: the cost receipt reads Copilot's usage and reports premium requests and nano-AIU — never a currency figure. A rejected run writes zeroed usage; a killed one writes none.
- feat: the transcript viewer parses copilot logs. Ephemeral events are dropped; tool events fold by call id.

Config schema stays `2`.

## v3.6.1 (2026-09-09)

### Read what a finished run consumed

- feat: `/agent-handoff cost-receipt` reads a saved receipt and its job logs, then writes a markdown and HTML cost page. Omitting the selector picks the newest receipt.
- feat: every number is measured. Codex jobs contribute token counters (subscription, no cost figure); Claude jobs contribute the CLI's reported cost. No saving or avoided-cost figure appears.
- feat: a missing measurement prints `unknown`; a measured zero prints `0`.
- feat: the driver row is scoped to the run's interval. A session hosting two runs yields two rows.
- fix: a job with no exit code is FAILED, never RUNNING.

## v3.6.0 (2026-09-08)

### Read a delegated job instead of grepping its log

- feat: `/agent-handoff transcript` renders a job's log as a self-contained HTML page. The selector is optional; ambiguous matches list candidates instead of guessing.
- feat: the viewer reads both Codex and Claude log formats. A failed edit renders as an error; an unrecognised event keeps its source. Dropping a log file onto the page renders it with no script needed.
- fix: log content is treated as untrusted input. External links are restricted to safe schemes.

## v3.5.1 (2026-09-08)

### A delegated Claude worker can run its own checks

- fix: a Claude-backed job now runs with bypass permissions. The previous mode denied nearly everything — every shell command outside the explicit allowlist was refused, including the repo's own checks.
- feat: job status and result now report permission denials. A denied tool call used to leave the exit code unchanged, making a blocked check look like a passed one. Any non-zero denial count now marks the job as failed.
- feat: submit warns when a Claude worker starts with checks bypassed. An override env var restores prompting.
- fix: the permission mode override is validated before use, preventing shell injection.

## v3.5.0 (2026-09-08)

### Breaking: a claude-backed identity is a real delegated job

- feat: a Claude-backed identity now becomes a real background job with the same lifecycle, monitoring, fix rounds, worktree, and receipt evidence as Codex.
- fix: the flow used to re-decide the vendor per run, swapping a Claude identity's work onto Codex for a cheaper meter — discarding the split decision's capability judgment.
- feat: delegation moves execution off the driver's meter **and** off its context window. The rule: never swap an identity to change vendors — change the config.
- feat: ad-hoc submit accepts `--backend codex|claude`. One that contradicts a named role is refused. Efforts are validated per CLI.
- fix: resume preserves the parent job's backend and role across fix rounds.
- fix: a delegated Claude worker strips inherited credentials so it authenticates independently.
- **breaking**: receipt schema v5 adds `cc_jobs` and `cc_job_durations`. `codex_jobs` narrows to codex-only. A v4 receipt fails validation — regenerate it.

Config schema stays `2`.

## v3.4.0 (2026-09-07)

### A second pair of eyes on the plan

- feat: `deep_reasoner` gains a spec review toggle. When on, the plan is reviewed by deep_reasoner independently before reaching the user — the first time the plan gets a check from a different agent than its author.
- feat: the review fires at most once per run and resets automatically between runs.
- feat: configurable via CLI, override, and the setup UI. Refused on identities other than `deep_reasoner`.

Config and receipt schemas unchanged. Absent means off.

## v3.3.0 (2026-09-07)

### The Codex effort dial reaches the top of the GPT-5.6 range

- fix: `max` and `ultra` are now configurable Codex efforts. The wizard previously dropped them silently, and delegation refused configs that carried them.
- docs: `ultra` subdivides the job through automatic task delegation rather than only thinking longer.

No change needed for newly released models — model names have never been hardcoded.

## v3.2.0 (2026-08-24)

### Optional e2e acceptance roles

- feat: two optional identities, `e2e_specifier` and `e2e_verifier`, behind `--with-e2e` (default off). The specifier writes Gherkin scenarios and executable tests; the verifier runs them and reports a verdict. A three-identity config stays complete.
- feat: jobs can run in their own Git worktree, pinned to an immutable base SHA. Resume inherits the parent's worktree; cleanup refuses if uncommitted changes remain.
- feat: a verdict is checked against exit code, scenario counts, findings list, and a scenario hash — not trusted as written.
- feat: the task table gains a `depends` column so the monitor can hold a row until prerequisites are done.

## v3.1.0 (2026-08-23)

### Breaking: receipts record how long the run took

- feat: preflight stamps a start marker, and the receipt measures wall clock against it. Without the marker, emission is refused.
- feat: per-job durations break the run down by delegated job.
- refactor!: receipt schema v3 → v4. A v3 receipt fails validation — regenerate it.

## v3.0.0 (2026-08-22)

### Breaking: renamed to agent-handoff

The skill was called `partner-skill` through v2.x. Every name changed at once with no compatibility shim.

- refactor!: skill name `partner-skill` → `agent-handoff`. Triggers: `/agent-handoff`, `/agent-handoff config`, `/agent-handoff tryout`, `/agent-handoff resume`, plus natural-language variants.
- refactor!: per-repo state `.partner/` → `.handoff/`; global config `~/.config/partner/` → `~/.config/handoff/`. Nothing reads the old locations.
- refactor!: all scripts, modules, and env vars renamed from `partner-*`/`PARTNER_*` to `handoff-*`/`HANDOFF_*`.
- refactor!: receipt header and schema `$id` renamed. Field set unchanged; schema version stays `3`.
- docs: archived entries keep the Partner name — that was the name when written.

### Breaking: one flow, one host

- refactor!: the Codex-driven flow (Direction A) is removed. The skill is now a single flow: Claude Code plans and splits, workers execute delegated jobs, Claude full-reviews before accepting.
- refactor!: the dual-host configuration layer is collapsed. Existing configs keep loading; stale host blocks round-trip untouched.
- refactor!: receipt schema v3 drops `direction`, `monitoring_level`, `claude_session_reused`, and `host`.
- refactor!: the bundled `idea-king` companion skill is removed. Its adversarial gate survives inline in the flow.
- fix: v1 config from either host namespace now seeds the setup wizard.

## v2.0.1 (2026-07-29)

### Bounded Claude planning

- feat: config-only Claude planner with a validated evidence packet, safe mode, wall and no-event timeouts, and an enforced API budget.
- feat: persist event envelopes, checkpoints, model/session info, cost metadata, plans, and recovery instructions.
- fix: distinguish authentication, upstream idle, local idle, wall timeout, budget stop, tool-use violation, and protocol error.
- fix: refuse unverified roles, malformed packets, silent truncation, incomplete output, model mismatches, output overwrite races, and automatic model fallback.
- fix: bound partial-line, visible-output, event-log, and process-group termination paths; extend secret redaction.

## v2.0.0 (2026-07-29)

### Setup, identity routing, and safety

- fix: strip host-injected environment markers before spawning the Claude CLI, preserving first-party OAuth.
- fix: restore auto mode for non-git repo targets.
- feat: rebuild the setup UI as a kinetic local routing console with live role mapping, responsive layout, and reduced-motion support.
- feat: localhost setup UI with a guided decision rail, model matrix, diff preview, confirmation, and smoke test.
- feat: identity matrix — three cross-vendor identities (`deep_reasoner` / `fast_worker` / `arbiter`), each with backend/model/effort; schema v2 with fail-closed v1 migration.
- feat: arbiter blind-solve protocol + first-run tryout; task table uses identity instead of owner.

### Protocol, receipts, and verification

- feat: hash-checked goal read/write, preventing silent lost updates.
- feat: Plan→Goal→PR→Verification protocol.
- feat: receipt extended with host/scope/config_source/roles_used, schema v2.
- feat: Sub Agent three-level routing.
- feat: setup wizard engine + first-run setup.
- refactor: split SKILL.md into core + references with host detection.
- feat: TOML-subset config engine (schema v1).
- feat: delegation with role injection from config.

## v1.4.2 (2026-07-05)

- feat: idea-king absorbs adversarial-review, grilling, and packet hygiene.

## v1.4.1 (2026-07-04)

- feat: execution-channel routing + evidence-backed prompting principles.
- fix: harden bidirectional delegation utilities.

## v1.4.0 (2026-07-03)

- feat: bidirectional Partner + idea-king + frontier prompting & memory protocol.

## v1.3.0 (2026-07-02)

- feat: showcase redesign with green palette, GSAP animation, and GIF.
- fix: replace curly quotes with straight ASCII quotes in img tags.

## Earlier (2026-06-24 – 2026-07-01)

- Showcase and release polish: reproducible cost ledger, README parity release gate, README language split.
- Public readiness: Partner session receipt, same-session Claude strategy, release preparation.
- Origin: initial Claude Codex relay skill, renamed to Partner.
