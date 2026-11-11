# Identity set v4: one e2e identity, an operator, and artefact rows

## Context

A review of the five identities asked three things. Which of them go unused? What choices does the driver have when it assigns a row? Can the skill serve work outside a typical software repo? The workloads in mind were a Rust desktop app, 3D animation with Blender, animated motion clips, and video production through paid media APIs (Fal, ElevenLabs).

Measured on v3.12.0, in this repo's own `.handoff/` (82 job directories, 18 saved receipts):

1. **Three of five identities have never run.** `fast_worker` ran 55 jobs and `deep_reasoner` 27. `arbiter`, `e2e_specifier` and `e2e_verifier` ran none, and no receipt names them in `roles_used`.
2. **The e2e pair repeats a core triple in every preset.** `e2e_specifier` matches `arbiter` or `deep_reasoner`, and `e2e_verifier` matches `fast_worker` or sits one effort step away. `design_agent-identities-and-config.md` already lists "a user who wants one model for both e2e stages configures it twice" as an accepted cost.
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

Prose only. The scripts already support each one.

- **`effort` column.** `-` means the identity's configured effort. A value is passed as `--effort` and must be one the backend accepts.
- **Second-read row.** A `deep_reasoner --read-only` row reads a risky diff or a row's artefact evidence and reports findings. Phase 4 acceptance stays with the driver.
- **Worktree.** `--worktree` for parallel rows that touch the same files, on any identity.

### `references/artefact-rows.md`

Target: 600 words or fewer. The driver reads it in Phase 1 for a row that meets any of three conditions: its result is not a text diff, it runs longer than one monitor tick, or it calls a paid service.

- **Routing.** `operator`, or `fast_worker` when it is unset.
- **Acceptance is evidence.** The packet names the output paths and a probe the worker must leave: ffprobe JSON, a frame count, a checksum, a build artefact path. For anything visual it also names keyframes or a contact sheet under `.handoff/evidence/<task-id>/`. The driver opens those images itself and never accepts on the worker's description. What no model can judge (audio, motion feel, taste) is a user sign-off: the row stays in `review` until the user answers, and the answer goes in Notes.
- **Long commands.** The worker starts any command expected to outlast a tick in the background, with output to a named log file, and polls it. The row states an expected duration. While that log or the output directory is still growing, silence in `log.jsonl` is not an anomaly. Past twice the expected duration, or with no growth across two ticks, it is.
- **Paid calls.** Before Phase 2 the driver lists each paid service, what it is called for, and a cap in calls or in money. The user's go-ahead is recorded in a `## Spend` block in the goal file. The packet carries the cap. The worker reads each key from a named environment variable and never prints or writes it. It reports every paid call. Before it retries a call that may have succeeded, it checks for an existing output. A call past the cap is a pause.
- **Environment.** A worker inherits the driver's environment, API keys included.
- **Posture.** A row that needs the network on a backend whose `default` denies it needs `permission_mode = allow-all` on `operator`. That is a config change, and the driver names it.
- **A rejected artefact row.** Its outputs move to `.handoff/rejected/<task-id>/`. Money already spent is reported.
- **No git repository.** Plain rows work. The driver records a file listing before and after each job in place of `git status`. Worktrees and the e2e gate still need git.

Other prose changes:

- `references/goal-file-template.md`: the `## Spend` block, the `effort` rule, an `e2e_tester` example row, and acceptance as "a command to run, a behaviour to observe, or evidence to inspect".
- `references/delegation-flow.md`: the `operator` rule in the split, the three row options, one pointer to `artefact-rows.md` in Phase 1 and one in Phase 3, and the generated-agent list.
- `references/e2e-acceptance.md`: one identity, two jobs.
- `references/arbitration.md`: the Evidence slot reads "the full scoped diff, or the artefact evidence".
- `references/identity-tryout.md`: the optional identities are named, and a configured `operator` gets one micro-task (a background command it polls, plus an outbound HTTPS HEAD reported as reachable or blocked).
- `references/session-receipt.md`: the report lists paid calls against the cap and any user sign-off.
- `references/setup-wizard.md`: the two flags.
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
| Generated `handoff-e2e-specifier.md` and `handoff-e2e-verifier.md` | Removed by apply and by `--uninstall` when they still match `.handoff/.generated-manifest` |
| `--uninstall --remove-config` | Clears retired sections in both header forms |
| A stored receipt that names an old role | Still validates |
| A retired section in the global file | The page is project scope only, so the notice names the file and the `--scope global` command |
| A managed routing block that names a retired agent | Stays until setup runs with `--routing-block` or `--remove-routing-block`. The upgrade note says so |

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
- **Byte walks.** A bare five-identity file loses one blank line before `[e2e_specifier]` on its first `set`, because that line belongs to the `[arbiter]` chunk. A second `set` is identical. This is the same one-time normalisation `[review]` gets.
- **Name tables to extend together**: `PRESETS`, `ROUTING_LINES`, `AGENT_TEXT` (`handoff-setup.py`), `IDENTITY_META` (`handoff-setup-ui.py`), the case at `delegate-job.sh:586`, `ROLES` in `validate-receipt.py:41`, and the enum in `assets/receipt-schema.json:146-153`.

### Evals

- `evals/fixtures/scripts/judge.py`: `DELEGATED` gains the new names. The e2e case finds two rows with identity `e2e_tester`, the later one depending on the earlier one and on an implementation row. The self-tests change with it.
- `e2e-added-for-user-observable-change.yaml`: fixture config and `must_contain` use the new name.
- New cases: `operator-row-for-tool-run`, `paid-call-needs-recorded-cap`, `artefact-review-opens-evidence`.
- `test-prompts.json`: the three e2e cases are fixed, and each changed gate dimension gets two prompts.
- The comparison is v3.12.0 against v4.0.0, two iterations per case on Sonnet 5.5. The baseline runs before any edit to the skill surface, because the suite measures the symlinked working tree.

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
6. Setup as migration: `drop_retired`, the `build_plan` changes, `_agent_paths`. Before this row is delegated, the driver replays four write sequences on a scratch copy: a three-identity file with `set-review`; a five-identity file with `set`, then apply; the same in legacy-header form; apply twice.
7. Wizard state: the fill in `build_state` and toggles pre-checked from the existing config.
8. Flow prose, one change-gate dimension per commit, kept by the driver.
9. Evals, the judge and the test prompts.
10. A real miniloop in a scratch folder with `operator` configured: an ffmpeg-generated clip with a contact sheet and ffprobe evidence, a command padded past two monitor ticks, and a small `cargo build`. No paid API is called.
11. Design-of-record docs, README, PRODUCT, the user guide and its diagrams, the version bump to 4.0.0, the changelog, and this spec revised to record what shipped.

## Verification

- `python3 -m unittest discover -s tests` after each of tasks 3 to 7.
- `bash scripts/check-skill-repo.sh .` after every edit to `SKILL.md` or `references/`.
- `python3 scripts/run-test-prompts.py` and `python3 evals/fixtures/scripts/judge.py --self-test`.
- Migration by hand on a scratch repo with a v3.12 five-identity config:
  - `resolve` prints the notice and the core identities.
  - `submit --role e2e_specifier --dry-run` is refused.
  - `--preview` then `--apply` give the diff described above.
  - A second apply changes no byte.
  - `--rollback` restores the original bytes.
- The web wizard on that scratch repo: the e2e toggle is pre-checked, `e2e_tester` is filled, preview and install succeed.
- Evals: both variants, side by side per case. The change is kept only if no case regresses.
- The miniloop ends with a valid receipt that names `operator` in `roles_used`.
- The ledger and receipt roundtrip commands in `CLAUDE.md`.

## Not doing

- **User-defined identities.** Validation, receipts, the wizard, the tryout and the evals all assume a fixed set, and a typo would become an identity. Add them when a real project needs a tier the five cannot express.
- **A cheaper scan tier.** Two of 82 jobs were read-only scans. `--effort` covers it for now.
- **Spend and runtime caps as config fields.**
- **A verdict schema that is not Gherkin-shaped.** Wait for the first non-software run that wants a scripted gate.
- **A paid API call in the validation run.** The paid-call rule is validated by prompts and evals only, and the changelog says so.

Found during the review and left alone:

- The web wizard cannot open on a schema v1 config, though the v1 error sends the user to it.
- `validate-verdict.py` accepts a PASS whose `command` is null.
- `handoff-config.py resolve --override` is unreachable from `delegate-job.sh`.
