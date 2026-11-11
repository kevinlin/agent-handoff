# Identity set v4: one e2e identity, an operator, and artefact rows

## Context

A review of the five identities asked three things. Which of them go unused? What choices does the driver have when it assigns a row? Can the skill serve work outside a typical software repo? The workloads in mind were a Rust desktop app, 3D animation with Blender, animated motion clips, and video production through paid media APIs (Fal, ElevenLabs).

Measured on v3.12.0, in this repo's own `.handoff/` (82 job directories, 18 saved receipts):

1. **Three of five identities have never run.** `fast_worker` ran 55 jobs and `deep_reasoner` 27. `arbiter`, `e2e_specifier` and `e2e_verifier` ran none, and no receipt names them in `roles_used`.
2. **The e2e pair repeats a core triple in every preset.** `e2e_specifier` has the same triple as `arbiter` or `deep_reasoner` in every preset. `e2e_verifier` matches `fast_worker` in balanced and cost, and differs in quality (codex against claude). `design_agent-identities-and-config.md` already lists "a user who wants one model for both e2e stages configures it twice" as an accepted cost.
3. **`deep_reasoner` is a reviewer in practice.** 22 of its 27 jobs are spec reviews. Four more are read-only reviews of a diff or a draft, a use the runtime prose never describes.
4. **The driver's row choice is binary**: `fast_worker` or `deep_reasoner`. The goal table has an `effort` column whose meaning is never defined. `--worktree` is prescribed only for e2e rows, yet 16 `fast_worker` jobs used it.
5. **The identities are already domain-neutral. The flow is not.**
   - Phase 3 cancels a job after no new log event across two 5-minute ticks. A worker blocked inside one 40-minute render or a long `cargo build` emits nothing until the command returns.
   - Phase 4 review means reading a text diff. Nothing tells the driver to look at a frame or a contact sheet, and a rejected row is saved as a `.patch`.
   - Every packet says "do not touch secrets or `.env` files", and nothing covers a worker that has to call a paid API. Spending money is not on any hard-stop list.
   - A worker inherits the driver's whole environment, API keys included. `clean_claude_env()` strips only `ANTHROPIC_*` and `CLAUDE_CODE_*`, and only for the claude backend. No document says so.
   - Under a workspace-write Codex config, `default` posture has no network, so a media API call fails with nothing in the prose to explain it.
6. **The wizard drops optional identities.** The e2e checkbox in `assets/setup-ui.html` is never pre-checked from the existing config, so an apply from the page removes existing e2e sections.

The research the maintainer supplied (a shared ChatGPT answer on configuring specialised long-running agents) argues for a per-agent profile with three layers: harness, model settings and execution policy. Handoff's identities cover the second and one field of the third. The research also lists ten specialised roles. That list is the role zoo that capability tiers exist to avoid, so this plan takes only two ideas from it: an execution posture of its own for tool-running work, and a recorded budget before any side effect that costs money.

## Decisions already made

Decided with the maintainer on 2026-11-11 and 2026-11-12:

- **The e2e pair merges into one optional identity, `e2e_tester`.** It runs both stages of the acceptance gate as two separate jobs. `e2e_specifier` and `e2e_verifier` are retired names.
- **`arbiter` stays core.** It has not run, and it remains the binding ruling the v3.8.0 review gates rest on.
- **A new optional identity, `operator`**, takes rows whose result comes from running tools with effects outside a text diff.
- **One new reference, `references/artefact-rows.md`**, holds the flow rules for such rows. It loads only when a run has one, so the default software path loads nothing new.
- **Config `schema_version` stays `2`** and gains no field. Spend and runtime caps are per-run authorisations and live in the goal file.
- **Receipt schema stays `8`.** The role enum widens and keeps the retired names, so stored receipts still validate.
- **Setup apply is the migration.** There is no separate migrate command for config.
- **Two old triples are never merged silently.** The wizard fills `e2e_tester` from the old specifier and shows it in the preview diff.
- **The plan spec is committed before the build** and revised at release to record what shipped.

## Design

### Identity set

| Identity | Tier | What it takes | Change |
|---|---|---|---|
| `deep_reasoner` | core | ambiguous work where a wrong first premise is expensive; reads every plan | a read-only second-read row becomes a documented option |
| `fast_worker` | core | specification-complete work the acceptance criteria alone can verify | task list reworded so it is not code-only |
| `arbiter` | core | blind second solver; the ruling at a review gate's cap | none |
| `e2e_tester` | optional | writes the acceptance gate from a frozen spec, and in a second job runs it against a pinned commit and returns a validated verdict | new; replaces the pair |
| `operator` | optional | rows whose result comes from running tools: a long build, a headless render, a media or model API call, an app launch for a visual check | new |

- `IDENTITIES` order is `deep_reasoner, fast_worker, arbiter, e2e_tester, operator`. Optional names are appended, so a three-identity file still round-trips byte-identically.
- `RETIRED_IDENTITIES = ("e2e_specifier", "e2e_verifier")`.
- Presets. `e2e_tester` takes the old specifier column. `operator`: balanced codex/detected/high, quality codex/detected/high, cost codex/detected/medium.
- A preset tuple has no permission slot, so a preset `operator` runs under `default`. `allow-all` stays an explicit config change, as `SKILL.md` promises.
- When `operator` is not configured, the driver says so once (`/agent-handoff config --with-operator`) and gives the row to `fast_worker`.
- The two e2e stages must be separate jobs. The verify job is a fresh `submit`, never a `resume` of the spec job, so the verifier does not carry the specifier's session.

### Row options for the driver

Prose only. The scripts already support both.

- **`effort` column.** `-` means the identity's configured effort. A value is passed as `--effort` and must be one the backend accepts.
- **Second-read row.** A `deep_reasoner --read-only` row reads a risky diff or a row's artefact evidence and reports findings. Phase 4 acceptance stays with the driver.

### `references/artefact-rows.md`

Target: 700 words or fewer. Its rules apply to a row whatever identity runs it. A row is an artefact row when its result is not a text diff, when one of its commands outlasts a monitor tick, or when it calls a paid service.

- **When it loads.** In Phase 1 when the plan has such a row. On a resume, when a row's acceptance names `.handoff/evidence/` or the `## Spend` block is not `none`. Phase 4 names it again for the review of such a row.
- **Identity.** Capability decides first: the acceptance gate is `e2e_tester`, and an ambiguous diagnosis is `deep_reasoner`, however long they run. A row whose work is running tools goes to `operator`, or to `fast_worker` when `operator` is unset.
- **Outputs.** The packet names an output directory that does not exist yet, and the driver checks that before it submits. The worker writes only there and under `.handoff/evidence/<task-id>/`, which must also be new. It never overwrites an existing file. Replacing an existing deliverable is the driver's step after acceptance. This is what makes a rejected row recoverable without git.
- **Acceptance is evidence.** The packet names a probe the worker must leave: ffprobe JSON, a frame count, a checksum, a build artefact path, and the exit status of each long command. For anything visual it also names keyframes or a contact sheet. The driver opens those images itself and never accepts on the worker's description.
- **User sign-off.** What no model can judge (audio, motion feel, taste) is a user sign-off. The row stays in `review` until the user answers, and the answer goes in Notes. An arbiter approval never stands in for it.
- **Long commands.** The worker starts a command expected to outlast a tick in the background, with its output in a log file and its exit status written to a file when it ends. It then checks the process at least every two minutes and reports elapsed time. Each check is an event in the job's own log, so Phase 3's stall rule applies unchanged. The row states an expected duration and a runtime cap. At the cap the worker stops the command, reports, and the driver decides.
- **Paid calls.** Before Phase 2 the driver lists each paid service, what it is called for, and a total cap in calls or in money. The user's go-ahead is recorded in the goal file's `## Spend` block.
  - The driver owns the cap. Each paid row gets its own allocation in that block before it is submitted, and the allocations never add up to more than the total. A fix round gets what its row has left.
  - Before each call the worker appends one line to `.handoff/evidence/<task-id>/spend.jsonl` with the service and the purpose. After it, the worker adds the provider's request or job id and the outcome.
  - A call with no recorded outcome is unknown. The worker stops spending and reports it. The driver settles it against the provider's own record before any further paid call, and never resubmits a paid row that failed or stalled until it has read that file.
  - The paid-call packet replaces the "touch no secrets" line. It names the one environment variable to read, the service it may be sent to, and the files that may be uploaded. Every other secret and every `.env` file stay off limits, and the key is never printed or written.
- **Environment.** A worker inherits the driver's environment, API keys included. The one exception is a claude-backed identity, which runs without `ANTHROPIC_*` and `CLAUDE_CODE_*`, so a row that needs one of those cannot run there.
- **Posture.** A row that needs the network on a backend whose `default` denies it needs `permission_mode = allow-all` on the identity that runs it. That is a config change the driver names. Configuring `operator` keeps that posture off `fast_worker`.
- **A rejected artefact row.** Its output directory moves to `.handoff/rejected/<task-id>/`. An effect outside the machine (an upload, a publish, money spent) cannot be undone: it needs the user's authorisation before the row runs and is reported when the row is rejected.
- **No git repository.** Plain rows work. The driver records a file listing with sizes and modification times before and after each job, in place of `git status`. The e2e gate still needs git.

Other prose changes:

- `references/goal-file-template.md`: the `## Spend` block, the `effort` rule, an `e2e_tester` example row, and acceptance as "a command to run, a behaviour to observe, or evidence to inspect".
- `references/delegation-flow.md`: the `operator` rule in the split, the two row options, the load rule for `artefact-rows.md` on entry and in Phases 1 and 4, and the generated-agent list.
- `references/packet-templates.md`: one line saying that a paid-call packet replaces the secrets line as `artefact-rows.md` describes.
- `references/e2e-acceptance.md`: one identity, two jobs, and the rule for a goal file from before 4.0.0 (below).
- `references/arbitration.md`: the Evidence slot reads "the full scoped diff, or the artefact evidence", and an approval never stands in for a user sign-off.
- `references/identity-tryout.md`: the optional identities are named, and a configured `operator` gets one micro-task (a background command it checks on, plus an outbound HTTPS HEAD reported as reachable or blocked).
- `references/session-receipt.md`: the report lists paid calls against the cap and any user sign-off.
- `references/setup-wizard.md`: the two flags, and the terminal route for a schema v1 file, which the page cannot open.
- `SKILL.md`: the identity names, the reference row, and a description that no longer says "coding work". The description is at 1,018 of 1,024 characters and the router body at 824 of 900 words, so both edits have to be measured.

### Migration

| Situation | Behaviour |
|---|---|
| A config has `[e2e_specifier]` or `[e2e_verifier]`, bare or in the pre-3.9.2 header form | It loads and the core identities work. The retired sections are not identities, and their bytes are kept. One stderr notice names the file, the sections and the fix |
| `set`, `set-review`, smoke's `verified` write | Retired sections keep their bytes |
| `/agent-handoff config` (setup apply) | The e2e toggle is on. `e2e_tester` is filled from `[e2e_tester]` if present, else `[e2e_specifier]`, else `[e2e_verifier]`. The preview shows the old sections removed and the new one added. Apply backs up under `.handoff/backups/`, and `--rollback` restores. `verified` starts `false` |
| CLI preset mode | The preset value wins, as it does for the core identities, and a plan note shows the old triple |
| `delegate-job.sh submit --role e2e_specifier` | Refused. The message names `e2e_tester` |
| `resume` of a job whose parent `meta` says `role=e2e_specifier` | Works, so an in-flight fix round finishes |
| Generated `handoff-e2e-specifier.md` and `handoff-e2e-verifier.md` | Removed by `--uninstall`, and by an apply that writes agents (the default), when they still match `.handoff/.generated-manifest`. Under `--no-write-agents` they stay and a plan note says so |
| `--uninstall --remove-config` | Clears retired sections in both header forms |
| A stored receipt that names an old role | Still validates |
| A retired section in the global file | The page is project scope only, so the notice names the file and the `--scope global` command |
| A managed routing block that names a retired agent | An apply that removes a retired agent also refreshes the block inside its own markers, and the preview shows that diff. A block the user edited is refused as it is today |
| A file with retired sections and an existing `[e2e_tester]` (a v3.12 engine wrote into a v4 file) | `[e2e_tester]` wins with its `verified` state. Apply drops the retired sections and emits one section |
| A retired section that is defined twice, or whose values do not validate | Read commands still keep its bytes. Setup refuses the migration and names the header |
| Mixed bare and legacy headers, or only one retired section | Same as above: kept on read, migrated by apply |
| An optional identity defined in the global file, with the project toggle off | The toggle removes the section from the selected scope only. The inherited identity still resolves, and `--status` shows it |
| A goal file whose rows name a retired identity | On resume the driver reads the name as `e2e_tester`. A row with no job yet gets its identity cell rewritten and is submitted as `e2e_tester`. A row that has a job keeps it, and its fix rounds resume on that job. Rows that are `done`, `rejected` or in `arbitration` are left as history |

Version skew. A v3.12 engine treats `[e2e_tester]` and `[operator]` as unknown sections: `validate` passes and `resolve` says nothing. `--role e2e_tester` and `--role operator` are refused as invalid roles, so jobs fail closed and validation does not. With `schema_version` staying `2` there is no gate to hang a refusal on. The version-skew table in the design doc records this.

### Engine mechanics

A read-only stress test walked each item against the code, with the v4 tuple patched in memory.

- **Bare retired sections need only the tuple change.** `_identity_name` returns None for a name outside `IDENTITIES` (`handoff-config.py:158-160`), so the chunk is unowned.
- **Legacy-form retired headers are refused today** as `unsupported identity` (`:314-315`), which would kill every `resolve`. The fix is one branch in `_identity_name` (`:156-157`): return None when the stripped name is exactly a retired name. That function feeds `parse_config`, `update_host` and `_needs_identity_rewrite`, so all three agree. A misspelt legacy header is still refused.
- **`[[e2e_specifier]]` must stay refused.** Add the retired names to the `interpreted` tuple in `split_sections` (`:175-182`).
- **Setup must drop retired chunks.** `update_host` keeps unowned chunks, so it gains `drop_retired=False`. Setup apply (`handoff-setup.py:898`, `:903`) and `--remove-config` (`:1621`) pass True. Smoke (`:1568`) does not.
- **Toggles are tri-state.** `set_defaults(with_e2e=False)` (`:1729`) cannot tell "not given" from `--no-with-e2e`. The default becomes None, resolved in `build_plan` to on when the file has the identity or a retired section. Without this a second flagless apply drops `e2e_tester`.
- **`build_plan` reads the file before `choose_identities`** (today `:878` runs before `:880-895`).
- **The fill only supplies a missing value.** In CLI custom mode `build_plan` appends `e2e_tester=<value>` to copies of the four `args.role_*` lists for each field no flag names. It must copy `args`, because `apply_plan` runs `build_plan` twice on the same object and a second append raises "repeats identity".
- **In the wizard**, `build_state` merges the fill into `current` before the `_ensure_model_option` loop (`handoff-setup-ui.py:754`). Otherwise a filled Copilot, Cursor or Antigravity model outside the catalogue is refused.
- **Retired agent files.** `_agent_paths` iterates `IDENTITIES + RETIRED_IDENTITIES` (`handoff-setup.py:808-813`), and the existing removal branch does the rest.
- **The notice.** `warn_retired(*paths)` in `handoff-config.py`, called once in its `main()` and once in `handoff-setup.py main()` for `--status` and `--smoke`. Preview and apply use `plan.notes` on stdout. The page prints apply's stderr into the result box, so a stderr notice would tell a user to migrate just after migrating. `delegate-job.sh` captures only stdout from `resolve`, so the notice passes through without a shell change.
- **Reading a retired section.** Once retired chunks are unowned, `parse_config` no longer returns their values. A new `read_retired(text)` in `handoff-config.py` parses them deliberately, validates them like any identity, and refuses a duplicate. Only setup calls it.
- **Apply prints the plan notes.** `apply_plan` prints file actions and the backup today and never the notes (`handoff-setup.py:1315-1341`), so it gains that print.
- **The wizard state goes stale.** `SetupController` builds `initial_state` once (`handoff-setup-ui.py:1002`) and serves that snapshot for every later state request. After an install that adds `operator`, a reload would show it absent and the next apply would remove it. The controller rebuilds its state after a successful apply and after smoke.
- **The routing block.** `build_plan` touches it only under `--routing-block` or `--remove-routing-block` (`:969`). It gains one more case: a structurally valid managed block that names a retired agent, in a plan that removes that agent.
- **Byte walks.** A bare five-identity file loses one blank line before `[e2e_specifier]` on its first `set`, because that line belongs to the `[arbiter]` chunk. A second `set` is identical. This is the same one-time normalisation `[review]` gets.
- **Name tables to extend together**: `PRESETS`, `ROUTING_LINES`, `AGENT_TEXT` (`handoff-setup.py`), `IDENTITY_META` (`handoff-setup-ui.py`), the case at `delegate-job.sh:586`, `ROLES` in `validate-receipt.py:41`, and the enum in `assets/receipt-schema.json:146-153`.

### Evals

- `evals/fixtures/scripts/judge.py`: `DELEGATED` gains the new names. The e2e case finds two rows with identity `e2e_tester`, the later one depending on the earlier one and on an implementation row. The self-tests change with it.
- `e2e-added-for-user-observable-change.yaml`: fixture config and `must_contain` use the new name.
- New cases: `operator-row-for-tool-run`, `paid-call-needs-recorded-cap`, `artefact-review-opens-evidence`.
- `test-prompts.json`: the three e2e cases are fixed, and each changed gate dimension gets two prompts.

How the two versions are compared:

- Both runs use Sonnet 5.5 and two iterations per case. The baseline ran before any edit to the skill surface, because the suite measures the symlinked working tree. Its transcripts and reports are kept.
- A case's score is its passes out of the trials that did not error. An infrastructure error is rerun once and otherwise left out of both numbers.
- Thirteen cases keep their fixture and judge, and are compared directly. A regression is a lower score on one of them.
- `e2e-added-for-user-observable-change` changes its fixture and its judge with the identity names. It measures the same behaviour on each side (two gate rows, the second depending on the first and on an implementation row), and the report marks it "judge changed".
- The three new cases have no baseline and are reported for v4.0.0 only.

## Tasks

Branch `feat/v4-identity-set`. The work runs through `/agent-handoff`. Nothing is pushed, tagged or merged without the maintainer asking.

1. Baseline evals on the untouched v3.12.0 tree.
2. This plan spec and its index row, committed on their own.
3. Receipt enum: widen `ROLES` and the schema enum, and add a test that the two agree.
4. The identity switch, as one commit, because the suite is red at any point between its parts:
   - `handoff-config.py`: the tuples, `RETIRED_IDENTITIES`, the `_identity_name` branch, `interpreted`, and `identities_for` taking the selected optional names.
   - `handoff-setup.py`: the name tables, `--with-operator` and `--no-with-operator`, and an operator question in `interactive()`.
   - The page toggles, and the `delegate-job.sh` case and usage.
   - The tests that pin the current set.
5. The retired-section notice.
6. Setup as migration: `read_retired`, `drop_retired`, the `build_plan` changes, `_agent_paths`, the routing block refresh, and notes printed by apply. Before this row is delegated, the driver replays the `set` and `set-review` sequences on scratch copies with the real CLI.
7. Wizard state: the fill in `build_state`, toggles pre-checked from the existing config, and state rebuilt after apply and smoke.
8. Flow prose, one change-gate dimension per commit, kept by the driver.
9. Evals, the judge and the test prompts.
10. A real miniloop in a scratch folder with `operator` configured: an ffmpeg-generated clip with a contact sheet and ffprobe evidence, a command padded past two monitor ticks, and a small `cargo build`. No paid API is called.
11. Design-of-record docs, README, PRODUCT, the user guide and its diagrams, the version bump to 4.0.0, the changelog, and this spec revised to record what shipped.

## Verification

- `python3 -m unittest discover -s tests` after each of tasks 3 to 7.
- `bash scripts/check-skill-repo.sh .` after every edit to `SKILL.md` or `references/`.
- `python3 scripts/run-test-prompts.py` and `python3 evals/fixtures/scripts/judge.py --self-test`.
- Write sequences with the real CLI on scratch copies:
  - A three-identity v3.12 file, then `set-review` with its current values: no byte changes.
  - A five-identity file, bare and then legacy form, then `set --role fast_worker --effort <value>` twice: each retired chunk is byte-identical after both writes, and the second write changes nothing.
- Migration by hand on a scratch repo with a v3.12 five-identity config:
  - `resolve` prints the notice once on stderr, and its stdout holds the three core identities with their original backend, model and effort.
  - `submit --role e2e_specifier --dry-run` is refused and names `e2e_tester`.
  - `--preview` then `--apply` give the diff described above.
  - A second apply changes no byte.
  - `--rollback` restores the original bytes of the config, the agent files and the manifest.
- Tests cover each row of the migration table, the four combinations of the two toggles (flag omitted, flag negated, terminal default, wizard payload), and a wizard reload after an install.
- The web wizard on that scratch repo: the e2e toggle is pre-checked, `e2e_tester` is filled, preview and install succeed, and a reload still shows what was installed.
- Evals: both variants, side by side per case, by the comparison rule above.
- The miniloop is accepted on its evidence, not on its receipt alone: the ffprobe output, the exit-status file of each long command, the job log showing a check at least every two minutes, and the contact sheet the driver opened. Its receipt validates and names `operator`.
- No retired identity name appears in `SKILL.md`, `references/`, `README.md`, `PRODUCT.md`, `CLAUDE.md`, the user guide or its diagrams, except in the migration rule in `e2e-acceptance.md` and in upgrade notes. The names stay in the changelog, the specs, `RETIRED_IDENTITIES`, the refusal message, the receipt enum and the tests.
- The ledger and receipt roundtrip commands in `CLAUDE.md`.

## Not doing

- **User-defined identities.** Validation, receipts, the wizard, the tryout and the evals all assume a fixed set, and a typo would become an identity. Add them when a real project needs a tier the five cannot express.
- **Worktrees for ordinary rows.** Only an e2e packet lets a worker commit, so an ordinary worktree row would end with uncommitted changes, no integration step and a cleanup that refuses it. It needs its own packet and integration contract first.
- **A cheaper scan tier.** Two of 82 jobs were read-only scans. `--effort` covers it for now.
- **Spend and runtime caps as config fields.**
- **A verdict schema that is not Gherkin-shaped.** Wait for the first non-software run that wants a scripted gate.
- **A paid API call in the validation run.** The paid-call rule is validated by prompts and evals only, and the changelog says so.

Found during the review and left alone:

- The web wizard cannot open on a schema v1 config, though the v1 error sends the user to it.
- `validate-verdict.py` accepts a PASS whose `command` is null.
- `handoff-config.py resolve --override` is unreachable from `delegate-job.sh`.

## Spec review outcome

`deep_reasoner` (codex, `gpt-6.1-sol`, `xhigh`) read the plan read-only on 2026-11-12, job `job-2026-11-12T00-10-31-34181-spec-review`. It returned 10 blocking and 3 advisory findings. All were accepted and none declined, so the gate closed at consensus in one round.

| # | Finding | Resolution |
|---|---|---|
| 1 | The spend cap did not survive parallel jobs or an uncertain retry | The driver owns the cap and allocates per row. The worker logs intent before each call and the provider's id after. An unknown outcome stops spending |
| 2 | Paid-call packets contradicted "touch no secrets", and the environment claim was wrong for claude | A paid-call packet names one variable, one service and the files that may leave the machine. The claude exception is stated |
| 3 | A goal file from before 4.0.0 had no transition | The resume rule in the migration table |
| 4 | Pre-checked toggles did not cover a reload after apply | The controller rebuilds its state after apply and smoke |
| 5 | The routing block could point at deleted agents, and "removed by apply" was wrong under `--no-write-agents` | The block is refreshed with the removal. The table states the `--no-write-agents` case |
| 6 | A quiet healthy command still met the cancel condition | The worker checks the process at least every two minutes, so the job log itself stays live and the stall rule is unchanged. A runtime cap is separate from the expected duration |
| 7 | Four artefact rules could not be followed together | Rules apply by row, capability picks the identity, the reference loads on resume and in Phase 4, and an arbiter approval never replaces a user sign-off |
| 8 | A file listing and a move could not protect existing outputs | Outputs go to a directory that did not exist. Nothing is overwritten. Effects outside the machine need authorisation |
| 9 | Ordinary worktree rows had no completion contract | Removed from this release and listed under Not doing |
| 10 | The eval comparison changed its own instrument | The comparison rule under Evals |
| 11 (advisory) | Schema v1 has no documented route, since the page cannot open on it | One line in `setup-wizard.md` names the terminal route |
| 12 (advisory) | More migration transitions needed outcomes and tests | Six rows added to the migration table, `read_retired` named, tests per row |
| 13 (advisory) | Two claims about the repository were wrong | The quality preset claim is corrected, and apply prints the plan notes |

The review also asked for sharper acceptance criteria. The Verification section now states exact write sequences, field-level assertions for `resolve`, the evidence the miniloop is accepted on, and the file set for the retired-name check.
