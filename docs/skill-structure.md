# Skill structure

What the agent-handoff skill loads into context, and when. Introduced in v3.12.0. The runtime contract is the files themselves; this page is the map for whoever maintains them.

![Skill structure: SKILL.md routes to one L2 reference per command, which names L3 references at the step that needs them](skill-structure.svg)

## Layers

- **L1, `SKILL.md`.** Loads on every trigger. It holds the rules that always apply, where the scripts are, the command table and the reference map. Nothing else.
- **L2, one reference per command.** `SKILL.md`'s command table names exactly one.
- **L3, read at the step that names it.** An L2 file sends the driver to an L3 file at the phase or condition that needs it. `SKILL.md` lists each one with when to read it, so no file is more than one hop from the entry point.
- **`scripts/` run through Bash and `assets/` are read by scripts.** Neither enters the context.

| Layer | File | Loaded when |
|---|---|---|
| L1 | `SKILL.md` | every trigger |
| L2 | `references/delegation-flow.md` | `/agent-handoff`, `/agent-handoff resume`, any delegation request |
| L2 | `references/setup-wizard.md` | `/agent-handoff config`, or a flow needs an unconfigured identity |
| L2 | `references/identity-tryout.md` | `/agent-handoff tryout` |
| L2 | `references/session-evidence.md` | `transcript`, `cost-receipt`, `visualise`, `migrate` |
| L2 | `references/skill-change-gate.md` | improving the workflow, or applying it to a substantial task |
| L3 | `references/backends/<backend>.md` | on every entry to the flow and before a tryout, for each backend in use; on a resume also for each existing job's recorded backend |
| L3 | `references/goal-file-template.md` | Phase 1, writing `.handoff/goal.md` |
| L3 | `references/packet-templates.md` | Phase 1 and 2, writing a delegation or spec-review packet |
| L3 | `references/arbitration.md` | a review gate at its cap, a contested call, or a resumed run with a row in `arbitration` or `rejected` |
| L3 | `references/e2e-acceptance.md` | the change alters user-observable behaviour |
| L3 | `references/pr-delivery.md` | the user asks for the full protocol |
| L3 | `references/session-receipt.md` | Phase 5, and the close of a setup or a tryout |
| L3 | `references/wrap-up-memory.md` | Phase 5 |

## What a request loads

Measured with `wc -w` at v3.12.0, against v3.11.1.

| Request | v3.11.1 | v3.12.0 |
|---|---|---|
| A delegated run on codex and claude | 12,311 words | 6,040 words |
| `/agent-handoff transcript` | 3,231 words | 1,829 words |
| `/agent-handoff config` | 4,778 words | 2,561 words |

The delegated run counts `SKILL.md`, the flow, two backend files, the goal-file template, the packet templates, the receipt and the wrap-up memory step. A run that reaches arbitration, e2e or PR delivery adds that one file. The whole surface, every file together, went from 18,035 words to 13,310.

## Rules for changing the surface

- **One home per rule.** State a rule in one file. Other files point to it. A rule restated in two places goes stale in one of them.
- **Every rule needs a loader.** A rule in an L3 file is only followed if some instruction sends the driver there at the moment it applies. Check every cold entry: a fresh run, a resume, setup, tryout and each evidence command.
- **A new reference is listed in `SKILL.md` and in the table above.** `scripts/check-skill-repo.sh` fails when a file under `references/` is named in neither, or when a named file is missing.
- **Name a file for what it is for,** not for where its content came from.
- **Say what the driver must do or decide.** Leave out what a script does unprompted and prints itself. Design history and rationale belong in `docs/specs/`.
- **The surface never points into `docs/` or `tests/`.** Those are for people.
- **Budgets the gate enforces:** the description is at most 1,024 characters, and the `SKILL.md` body at most 900 words.
- Changing a workflow rule, as opposed to where it lives, goes through `references/skill-change-gate.md`.

## Checking a change

- `bash scripts/check-skill-repo.sh .` covers the budgets, reachability and the pointer rule.
- `evals/run-evals.sh` runs the eval suite. Its script judges read which files a session loaded, so a request that starts loading a file it should not shows up as a failed case. A personal install at `~/.claude/skills/agent-handoff` outranks the copy the suite installs, so on a machine that has one, that install is what gets measured.
