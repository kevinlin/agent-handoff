# Layered skill surface, purpose-named references, and evals that measure it

## Context

The skill runs on Claude Code only, on Fable, Opus or Sonnet with a 1M context window. A review of what it loads at runtime looked at four things: whether files load layer by layer, whether each file name says what the file is for, how much text reaches the context, and what the evals under `evals/` prove.

Measured on v3.11.1 with `wc -w`:

1. **The description is over budget.** It is 1,583 characters. The Agent Skills limit is 1,024, and Claude Code's skill listing cut it at character 1,535. The part that was cut is the "do not trigger on the bare word handoff" rule.
2. **`SKILL.md` is not a router.** Its 3,231 words load on every trigger. `/agent-handoff transcript` pays for the receipt contract (about 750 words), the visualise lane-map contract (about 650) and the monitoring rules.
3. **A normal run loads about 12,300 words** (`SKILL.md`, `claude-driven`, `fable5-principles`, `handoff-template`, `goal-template`, `memory-protocol`) and repeats itself. The backend-to-CLI command mapping appears 4 times, the rule against swapping an identity for a cheaper meter 7 times, the receipt rules twice, antigravity monitoring twice, the spec-review-once rule 3 times and the e2e criterion 3 times.
4. **Backend rules sit inline in the flow.** About 1,400 words on copilot, cursor and antigravity load when the config uses codex and claude only.
5. **Conditional protocols interrupt the phases.** Sub-agent routing and both arbiter protocols sit between Phase 1 and Phase 2. Arbitration, e2e and PR delivery are needed on a minority of runs.
6. **Prose narrates what scripts do and why they were built that way.** `SKILL.md` carries the lane-map contract that `handoff-session-ui.py` prints itself (`diagram_prompt()`). The flow lists per-backend flag mappings the driver never types. `setup.md` step 1 spends 430 words on what the wizard page shows. The design specs already record the history and the probes.
7. **File names describe origin, not purpose.** `fable5-principles.md` is named after its source, `claude-driven.md` after a two-direction design that v3.0.0 removed, `darwin-ratchet.md` and `e2e-gauntlet.md` after metaphors.
8. **Some lines are stale or wrong.**
   - `SKILL.md` Cost Receipt says the session receipt is "above". It is below.
   - `goal-to-pr.md` says "or the equivalent in English" and repeats the same English phrases. It tells the driver not to create `.handoff/goal.md` for a small change, which contradicts Phase 1. It names a test-case id and the external `loop-engineering` skill.
   - `memory-protocol.md` and `darwin-ratchet.md` say "Codex" where they mean any worker. The ratchet scores against a "100-point skill score" that nothing defines.
   - `e2e-gauntlet.md` cites `tests/test_delegate_role.py`, which is outside the skill surface.
   - `CLAUDE.md` lists `docs/releases/`, which does not exist, and says "five phases" before listing six.
9. **The evals are weak proxies.** There are 8 cases, all regex on the final message, with no fixtures.
   - `driver-keeps-quality-critical-work` passes a reply that delegates the security-critical work, as long as the words appear.
   - The success regex of `no-fabricated-token-savings` matches nearly any sentence with "no ... sav".
   - Every case runs in an empty workspace, so the renderer cases test only the error path.
   - `eval.yaml` installs `test-prompts.json` and `docs/specs/` beside the skill. `test-prompts.json` holds the expected behaviour for near-identical prompts.
   - No case measures which files load. No model is pinned, though Sonnet is the floor. No case covers `migrate` or the "not for ordinary code review" near-miss.

## Decisions already made

Decided with the maintainer on 2026-11-10:

- **Full layering**, with one file per backend under `references/backends/`.
- **Three legacy steps are dropped.** Each is a behaviour change and is listed in the changelog:
  - the Phase 0 ask to append `DO NOT send optional commentary` to the target repo's `AGENTS.md`, because every packet already ends with that rule;
  - the optional "gstack `/codex` review" at the end of Phase 4;
  - the mem0, claude-mem and Codex rollout layers in the wrap-up memory step. Claude Code auto-memory stays, and so does the lessons-learned line in packets.
- **The new structure has its own doc**, `docs/skill-structure.md`, written once the rewrite is done.
- **The diagram is drawn again as `docs/skill-structure.svg`** with the `baoyu-diagram` skill, by the driver: light theme only, 12px minimum font size. `docs/skill-file-structure.svg` is removed.
- **`fast_worker` runs the eval validation**: `skill-up validate` and the old-versus-new runs, as a delegated job. The eval engine is pinned to Sonnet 5.5 at default effort, because skill-up 0.11.0 has no effort setting for its `claude_code` engine.

## Design

### Target structure

`SKILL.md` lists every reference once, with when to read it, so no file is more than one hop from the entry point. The flow names the same file again at the phase that needs it.

```
SKILL.md                      L1  router: rules that always hold, $HANDOFF_DIR, command table, reference map   ~600 words (was 3,231)
references/
  delegation-flow.md          L2  /agent-handoff, resume: phases 0-5                    ~2,300 (was 5,051)
  setup-wizard.md             L2  /agent-handoff config                                 ~500 (was 1,547)
  identity-tryout.md          L2  /agent-handoff tryout                                 ~450 (was 663)
  session-evidence.md         L2  transcript, cost-receipt, visualise, migrate          ~650 (from SKILL.md)
  skill-change-gate.md        L2  changing this skill, or applying it to a substantial task   ~300
  goal-file-template.md       L3  Phase 1                                               ~550 (was 967)
  packet-templates.md         L3  Phase 1-2: packet rules, delegation + spec-review packets   ~800 (was 2,731 across two files)
  backends/<backend>.md       L3  Phase 0: only the backends the config uses            ~100-450 each
  session-receipt.md          L3  Phase 5, tryout close: the receipt contract           ~500 (from SKILL.md + Phase 5)
  wrap-up-memory.md           L3  Phase 5                                               ~200
  arbitration.md              L3  a gate hits its cap, or a contested call; holds its packet   ~700
  e2e-acceptance.md           L3  user-observable change                                ~1,400 (was 2,172)
  pr-delivery.md              L3  full protocol only; holds the Goal Packet             ~1,000
```

Expected load, confirmed with `wc -w` after the rewrite: a codex and claude run about 5,500 words (was 12,300); `/agent-handoff transcript` about 1,600 (was 3,231).

### Rename map

| v3.11.1 | v3.12.0 |
|---|---|
| `references/claude-driven.md` | `references/delegation-flow.md` |
| `references/fable5-principles.md` | dissolved: packet rules into `references/packet-templates.md`, channel and effort rules into `references/delegation-flow.md` |
| `references/handoff-template.md` | `references/packet-templates.md` (the Arbitration Packet moves to `arbitration.md`, the Goal Packet to `pr-delivery.md`) |
| `references/goal-template.md` | `references/goal-file-template.md` |
| `references/goal-to-pr.md` | `references/pr-delivery.md` |
| `references/memory-protocol.md` | `references/wrap-up-memory.md` |
| `references/darwin-ratchet.md` | `references/skill-change-gate.md` |
| `references/e2e-gauntlet.md` | `references/e2e-acceptance.md` |
| `references/setup.md` | `references/setup-wizard.md` |
| `references/tryout.md` | `references/identity-tryout.md` |
| `docs/skill-file-structure.svg` | `docs/skill-structure.svg` |
| new | `references/session-evidence.md`, `references/session-receipt.md`, `references/arbitration.md`, `references/backends/{codex,claude,copilot,cursor,antigravity}.md`, `docs/skill-structure.md` |

### Rules for the rewrite

- Each rule has one home. Other files point to it.
- Keep what the driver must do or decide, with its one-line reason. Cut what a script does unprompted and already says in its own message, history, and design rationale that `docs/specs/` already records.
- Nothing is dropped silently. A rule inventory of the v3.11.1 surface is built first. Each entry ends as kept, with its new file, or as dropped, listed in the changelog.
- No workflow rule changes except the three dropped steps. Receipt schema stays 8, config schema stays 2.
- Each `backends/<backend>.md` holds that backend's preflight check, permission posture (default, allow-all, read-only), monitoring anomalies and resume quirks.
- A rule needs a loader at every cold entry point, not only a home. The flow loads the backend files in an "on every entry" step that a resumed run also takes: one file per backend the config uses, plus the backend recorded in the `meta` of every job the goal file still refers to, which can differ from today's config. Tryout loads them before it submits. Setup names them for an identity set by hand. A resumed run with a row in `arbitration` or `rejected` loads `arbitration.md`. The full protocol loads `pr-delivery.md` in Phase 1, before its plan-only stage, not at delivery.
- Scripts are unchanged apart from two error strings that name a renamed reference (`delegate-job.sh`, `handoff-setup.py`) and one docstring (`goal-sync.py`).
- The skill surface never sends the agent into `docs/` or `tests/`. `SKILL.md` and `references/` name no path under either; `scripts/` names nothing under `docs/specs/`. An external URL and the gate's own required-file list are not pointers. `docs/skill-structure.md` points at the surface.

### Evals

`evals/eval.yaml`:

- Install the runtime surface only: `include` `SKILL.md`, `references/**`, `scripts/**`, `assets/*.html`, `assets/*.json`, `LICENSE`; `exclude` the three CI-only scripts. That keeps `test-prompts.json` and `docs/` out of the workspace.
- Pin the engine to Sonnet 5.5, the supported floor. Keep `transcript` as a report artifact.
- `environment: none` runs on the host. It does not stop a browser launch or a real worker job. So the renderer prompts ask for the page path instead of a browser, the decision-only prompts say not to submit, and every script judge checks that no job directory was added. The runner also sets `BROWSER=true`.

Cases (14; an id is kept where its prompt is kept):

| Case | What it must prove | Judge |
|---|---|---|
| `transcript-renders-not-delegates` | fixture job; the rendered page holds the fixture's own message; no job started; the flow doc was not read | script |
| `cost-receipt-renders-not-delegates` | fixture receipt and job; md and html written beside the receipt and holding the fixture jobId; job state unchanged | script |
| `migrate-plans-then-applies` (new) | fixture legacy receipt; the plan ran before `--apply`; the receipt moved byte for byte into its dated session folder | script |
| `flow-loads-by-layer` (new) | split-only prompt with a codex and claude config; the flow was read; setup, evidence, PR delivery and the other three backend files were not | script |
| `resume-loads-recorded-backend` (new) | goal file with a running row on a cursor job, a `rejected` row, and a config that no longer uses cursor; the answer shows the rejected row first and reopens nothing; the cursor rules were loaded | script |
| `tryout-loads-backends-first` (new) | "what will run, do not submit yet"; the tryout doc and the configured backends' rules were loaded; no job started | script |
| `unrelated-handoff-doc-no-trigger` | the document was written; no skill file was read | script |
| `code-review-no-trigger` (new) | a small code fixture is reviewed; no skill file was read | script |
| `driver-keeps-quality-critical-work` | the split itself keeps signing, the security review and sign-off with the driver, and delegates mechanical work | script |
| `identity-not-swapped-for-meter` | the migration is not assigned to `deep_reasoner`; the config change is named; nothing is written or submitted | script |
| `review-cap-escalates-to-arbiter` | no acceptance before a ruling; the ruling binds and is recorded | script |
| `no-fabricated-token-savings` | no invented figure, including "avoided" and "reduced" wording; a quoted denial is not a failure | script |
| `receipt-contract-splits-five-backends` | five counts, the generator, schema 8, the start marker, and where `roles_used` and the counts come from | rule_based |
| `e2e-added-for-user-observable-change` | with the e2e pair configured: both rows, the verifier depending on both; nothing submitted | script |

- skill-up copies only the judge script it is given, so a shared helper cannot be imported. One self-contained `evals/fixtures/scripts/judge.py` holds every script check and picks its case from a marker file the case's `context.files` writes.
- The read set comes from the transcript's tool calls: `Read`, and `Bash` reads through `cat`, `sed`, `head` and the like. Old and new file names both map to the same roles, so the suite runs on v3.11.1 and v3.12.0. A transcript with no tool events is an unsupported measurement: the judge fails with that message and the case is reported as not measured, never as a pass.
- No case uses `agent_judge`. On skill-up 0.11.0 it failed on every case here (`judge context material path escapes context dir`), so the four cases planned for it check the answer's own table and sentences in the script judge instead.
- On v3.11.1 the two loader cases can fail, because that surface loads backend rules only in the flow. That is what they are for.
- Not covered: `visualise` and `config` start a server that blocks, so they stay in `test-prompts.json` only. CI does not run the eval suite.

## Spec review outcome

`deep_reasoner` (codex, read-only) returned 6 blocking and 2 advisory findings on 2026-11-10. All eight were accepted, and none was declined:

1. Backend rules need a loader on resume and tryout, and a resumed job's recorded backend can differ from the config. See the loader rule above.
2. The change gate also applies to a substantial task today. Its applicability is kept, and so is its stop-on-low-signal rule; only the undefined score goes.
3. A cursor worker passes `ANTHROPIC_*` and `CLAUDE_CODE_*` through, so nested `claude` sessions in the eval runs would inherit them. The eval runs and the miniloop start from `clean_claude_env`.
4. The eval acceptance let its central check disappear. Now every case must pass on v3.12.0, an infrastructure error is reported apart from a behaviour failure, and a missing tool trace is never a pass. Two loader cases were added.
5. The pointer gate would have failed on its own required-file list. It is scoped as written above.
6. Checks ran before the final prose. The prose pass now comes before the preservation review, the eval runs and the security scan. The miniloop must show which install it loaded.
7. (advisory) A shared judge helper cannot be imported. One self-contained judge script.
8. (advisory) Two design docs say the evidence commands live in `SKILL.md`, and the user guide names the references in brace shorthand. The sweep changes those statements, not only file names.

The review also found that `fable5-principles.md` calls `max` codex-only, while `delegate-job.sh` accepts it on claude and copilot. Only `ultra` is codex-only, and the new surface says so.

One fact the review could not see: `~/.claude/skills/agent-handoff` on the maintainer's machine is a symlink to this repo, and a personal skill outranks a workspace copy of the same name. An eval run after the rewrite lands would load the new surface whatever the workspace holds. The v3.11.1 baseline therefore runs before the rewrite enters the working tree.

## Build notes

- The rule-preservation review (T5, `deep_reasoner`, three passes) found 10 blocking and 15 advisory issues in the first drafts, then 3 blocking in the fixes, then none. Kept out on purpose and listed in the changelog: the "no goal file for a one-off small change" sentence, which contradicted Phase 1 and sat in a file the default path never loaded.
- The reporting list moved to `references/session-receipt.md`, because a run, a setup and a tryout all load that file when they close.
- Under the full protocol, Stage 1 writes a goal file holding only the Goal line, the Checkpoint Rule and the `## Spec Review` block, so a review that fails or is interrupted before Stage 2 leaves its state on disk.
- The baseline run hit the account's 5-hour limit on two sessions. Those two results are infrastructure errors and are reported as such.

## Results

- Words loaded, measured with `wc -w`: a delegated run on codex and claude 6,040 (was 12,311); `/agent-handoff transcript` 1,829 (was 3,231); `/agent-handoff config` 2,561 (was 4,778). The whole surface is 13,310 words (was 18,035). `SKILL.md` is 990 words with a 1,018-character description.
- Evals on Sonnet 5.5, two iterations per case: v3.11.1 passed 15 of 26 measured sessions, v3.12.0 passed 23 of 28. No case passed on v3.11.1 and failed on v3.12.0. The first run on the new surface did show one: the receipt case failed because the five field names and "schema 8" had been cut from the prose. Both went back in.
- Still failing on v3.12.0: `identity-not-swapped-for-meter` (2 of 2, as on v3.11.1: one session never loaded the skill, one loaded it and still followed the user's request), `driver-keeps-quality-critical-work` (2 of 2, one a judge false positive) and `resume-loads-recorded-backend` (1 of 2: the rejected row was not shown first).
- A live run in a scratch repo, driven by `claude -p` on Sonnet 5.5, went from preflight to a valid receipt in 13 tool calls: one codex spec review, one cursor row accepted on the first pass. It read the flow, the two backend files in use, the goal-file template, the packet templates, the receipt and the wrap-up memory step, and no other reference.
- SkillSpector 2.11.2 on clean copies of HEAD and of the working tree, against the baseline: 137 and 138 open findings, 23 suppressed in both, so the baseline was already stale before this change. In `SKILL.md` and `references/` the open findings went from 12 to 4. The 9 new ones are in the eval judge's self-test. The baseline file is unchanged.

## Tasks

A Handoff run. `deep_reasoner` is codex, `fast_worker` is cursor `claude-sonnet-5-5-xhigh`, `arbiter` is copilot.

1. **Spec review** (`deep_reasoner`, read-only). Done; see above.
2. **T1 rule inventory** (driver). Every normative rule in v3.11.1 `SKILL.md` and `references/`, one line each with its source.
3. **T2 new surface, drafted outside the repo** (driver, kept inline because it is the contract). `SKILL.md` as the router with a description of 1,024 characters or fewer and `version: 3.12.0`, and the L2 and L3 files per the tree. The de-slop pass on the new prose is part of this task. Each inventory entry is ticked as its rule lands.
4. **T4 evals** (`fast_worker`). Cases, fixtures and the judge script per the Evals section, written against the v3.11.1 tree. `skill-up validate` passes, and each script check fails on a wrong workspace and passes on a right one.
5. **T8a baseline run** (`fast_worker`, after T4). The suite on v3.11.1 with Sonnet 5.5, from a clean environment, into `.skillgantry-workspace/`.
6. **T5 rule-preservation review** (`deep_reasoner`, read-only, after T2). Given the inventory and the drafts: which rule is missing, weakened, duplicated or contradicted, and which has no loader.
7. **T2 lands** (driver, after T8a and T5). `git mv` the references and copy the drafts in.
8. **T3 pointer sweep and gate** (`fast_worker`, after T2 lands).
   - Update names in `README.md`, `CLAUDE.md`, `docs/specs/design_*.md`, `docs/user-guide/agent-handoff.html` (its brace shorthand and its header version included), `test-prompts.json`, the three script strings and the one test that asserts on them. Change the design docs' statements that the evidence commands live in `SKILL.md`. Implemented `plan_*.md` files keep the old names, as in v3.10.1 and v3.11.0.
   - `scripts/check-skill-repo.sh`: the new required-file list, with `docs/skill-structure.md` and `docs/skill-structure.svg`; the receipt-contract greps move to `references/session-receipt.md`; four new checks: the description is at most 1,024 characters, the `SKILL.md` body is at most 900 words, every `references/` file is named in `SKILL.md` and in `docs/skill-structure.md` and every named file exists, and the pointer rule above.
   - Fix the two stale `CLAUDE.md` lines. Its "One flow" paragraph points at `docs/skill-structure.md` for the layer map.
9. **T6 structure doc** (driver). `docs/skill-structure.md`: the layer table, which files each command loads with measured word counts, the rules for adding a reference, and the diagram. README links to it.
10. **T7 structure diagram** (driver). Remove `docs/skill-file-structure.svg`; draw `docs/skill-structure.svg` with `baoyu-diagram` by layer. Light theme only, no dark variant and no PNG, 12px minimum font size.
11. **T8b run on v3.12.0** (`fast_worker`, after T3). The same suite on the working tree, same model and environment. It reports pass or fail, tokens and duration per case and variant, with infrastructure errors apart, and changes no case, judge or skill file. The driver reads both `result.json` files before accepting the table.
12. **T9 verification** (driver). See below.
13. **T10 release** (driver). The README badge, the user guide header and the new diagram carry 3.12.0; the `CHANGELOG.md` entry; the receipt. No skill-surface file changes in this task.

## Verification

1. `python3 -m unittest discover -s tests`, `node --test tests/test_transcript_viewer.mjs`, `bash scripts/check-skill-repo.sh .`, `python3 scripts/run-test-prompts.py`, `bash install.sh --dry-run`, and the receipt roundtrip and ledger check from `CLAUDE.md`.
2. `wc -w` before and after for three load paths (normal run, transcript, config), reported in the changelog and in `docs/skill-structure.md`.
3. The inventory has zero unaccounted rules, confirmed by T5.
4. The eval table: every case passes on v3.12.0, and no shared case passes on v3.11.1 and fails on v3.12.0. Tokens per case sit beside the pass marks.
5. `skillspector scan` against `.skillspector-baseline.yaml`; review every changed finding, in files that moved and in files that were only edited, then refresh the entries.
6. `docs/skill-structure.svg` opened in a browser: light theme, no text under 12px, every `references/` file present.
7. One live miniloop on the new surface, kept with the driver: a small real Handoff task through `claude -p` in a scratch repo, started from `clean_claude_env`. Its transcript must show the skill path and version it loaded, and is read for a missed layer or a lost rule.

## Not doing

- `scripts/run-test-prompts.py` validates `test-prompts.json` and runs nothing. A rename would touch CI only.
- `install.sh` ships `docs/`, `tests/` and media into the skills directory. Nothing loads them, so they cost no context.
- `make-receipt.py` takes the five job counts as flags and also measures them. The flags could go.
- The 54 cases in `test-prompts.json` are checked for shape only. The evals are the cases that run.
