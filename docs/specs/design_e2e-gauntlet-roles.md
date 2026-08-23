# Add e2e_specifier and e2e_verifier identities

## Context

Handoff has three identities — `deep_reasoner`, `fast_worker`, `arbiter` — each a `backend + model + effort` triple. All three are *capability tiers*: they say how much brain and which meter, never what work to do.

Robert C. Martin's Agent Gauntlet Pipeline — a five-stage agent pipeline, described in his 2026-08-19 conversation with Matt Pocock — adds two stages Handoff has no equivalent for:

- **Specifier** — turns a spec document into a Gherkin acceptance test and an executable QA procedure written from a human operator's perspective.
- **QA agent** — executes that procedure against the implemented feature and returns a deterministic pass/fail.

Handoff's Phase 4 review gate reads the diff against written acceptance criteria. That catches "the diff doesn't do what the brief said". It does not catch "the feature doesn't work when a person uses it". The two new identities close that gap.

Outcome: `e2e_specifier` and `e2e_verifier` exist as first-class identities, configurable per backend/model/effort like the other three, opt-in at setup, and skipped silently when unconfigured or when the change isn't user-observable.

Decisions already made:

- **Full identities, optional tier.** Both join `IDENTITIES`, but a core/optional split keeps them out of the required-configuration gate. An existing config with three identities stays complete.
- **Script owns the worktree, driver merges.** `delegate-codex.sh` gains `--worktree`; merge stays with the driver, where the existing hard-stop rule lives.
- **Harness-only fixes.** The verifier repairs its own scaffolding, never product code. A product failure is a finding the driver routes back to `fast_worker`.
- **Repo-native artifacts.** Gherkin and the executable spec go into the repo's own test tree; paths recorded in the goal-file row.
- **Driver judgment invokes them**, against one written criterion in Phase 1. No new trigger phrases.
- **No receipt schema bump.** Widening the `roles_used.role` enum is additive; every existing v4 receipt still validates.

## Design

### Identity layer

`IDENTITIES` grows to five, **appended** so `emit_host_sections` (which orders by `IDENTITIES`) still writes existing three-identity files byte-identically:

```python
CORE_IDENTITIES = ("deep_reasoner", "fast_worker", "arbiter")
OPTIONAL_IDENTITIES = ("e2e_specifier", "e2e_verifier")
IDENTITIES = CORE_IDENTITIES + OPTIONAL_IDENTITIES
```

`schema_version` stays `2` — adding identities doesn't change the document shape. `resolve` already emits only configured identities, so absence needs no new code.

### Optionality

The whole notion of optional collapses to one flag rather than a parallel code path. `PRESETS` **does** gain rows for both new identities, so the wizard can offer sensible defaults, and a single `--with-e2e` boolean (default off) decides whether they're written at all. This keeps `_preset_matrices` and the UI's preset-comparison check working unchanged, which a "presets stay three rows" design would have broken.

| preset | e2e_specifier | e2e_verifier |
|---|---|---|
| balanced | codex / detected / xhigh | codex / detected / high |
| quality | claude / opus / high | codex / detected / high |
| cost | codex / detected / high | codex / detected / medium |

Both default to the Codex meter in two of three presets: the specifier does bounded translation work and the verifier does execution, neither of which needs to burn API spend.

Optional means, precisely:

- `--with-e2e` off (default) → the two sections are absent from `config.toml`; nothing else changes.
- The `smoke` gate and the "identities are not configured" error compute over `CORE_IDENTITIES`.
- `--status` still prints all five, showing `<unset>` for unconfigured ones. That listing is how a user discovers the add-on exists.
- `delegate-codex.sh --role e2e_specifier` with nothing configured hits the existing fail-closed error pointing at `/agent-handoff config`. No new error path.
- Phase 1 skips e2e rows silently when unconfigured. No nagging.

### Worktree primitive

`delegate-codex.sh submit --worktree <branch> [--base <ref>]`:

- `git worktree add <repo>/.handoff/worktrees/<jobId> -b <branch> <base>`; `--base` defaults to the repo's current `HEAD`
- the job runs with the worktree as cwd; `meta` records `worktree=`, `branch=`, `base=`
- `resume` reads `worktree=` from `meta` — `codex exec resume` takes cwd from the shell and accepts no `-C`, so this is required for a fix round to land in the right tree
- new idempotent `cleanup <jobId>` removes the worktree; `cancel` calls it

`cleanup` refuses a dirty worktree and reports it rather than discarding uncommitted agent work. `.handoff/` is gitignored, so the worktree lives outside the tracked tree.

A `claude`-backend e2e identity gets the same isolation through the Task tool's existing `isolation: "worktree"`. No second mechanism.

### Flow placement

Phase 1 gains one criterion: **add e2e rows when the change alters user-observable behaviour at a real interface (UI, API surface, mobile screen) and the identities are configured.** Skip for internal refactors, docs, config, and pure library work.

Ordering, which the driver enforces:

```
T_spec   (e2e_specifier)  worktree off main       -- parallel with T_impl
T_impl   (fast_worker)    branch/worktree as usual
         driver reviews T_spec against goal acceptance, then merges
T_verify (e2e_verifier)   worktree off the branch carrying tests + implementation
         PASS -> done.  FAIL -> fix round on T_impl.  BLOCKED -> driver resolves launch.
```

`T_verify` cannot run before `T_spec` is merged, because the tests have to exist in its tree.

The **review-the-tests-first** step is load-bearing. A generated acceptance test that is weaker than the goal's criteria will green-light a broken feature, and the verifier's verdict inherits that weakness. The driver reads the specifier's tests against `.handoff/goal.md`'s acceptance column before the verdict counts for anything. This is also what keeps the pair off the maker-is-also-judge failure the [Darwin ratchet](references/darwin-ratchet.md) warns about: the specifier writes the gate, the verifier runs it, the driver judges the gate itself, and product fixes go back to a third identity.

Ordering needs one new `depends` column in the goal-file task table. The `/loop` monitor reads that file to decide what to submit next and has nowhere today to learn that `T_verify` waits on `T_impl`.

### Specifier contract

Input: `.handoff/goal.md` plus the spec the user supplied. Output, committed in its worktree:

- a Gherkin `.feature` file in the repo's existing convention
- an executable spec in the repo's detected e2e stack — Cypress/Playwright for web, Maestro for mobile, the repo's own HTTP test idiom for API scenarios
- a human-operator QA procedure in markdown, next to the executable spec

The specifier **detects** the stack from the repo (`package.json`, `.maestro/`, existing test dirs) and states its choice in its report. No config knob. If the repo has no e2e stack at all, it proposes one and stops rather than installing a framework unasked — adding a dependency is the driver's call.

Artifact paths go into its goal-file row, which is the contract the verifier reads.

### Verifier contract

Writes allowed: the test tree and `.handoff/`. Writes forbidden: product code. It reports a fixed block:

```text
[e2e verdict]
result: PASS | FAIL | BLOCKED
stack: cypress | playwright | maestro | api | other:<name>
launch: <how the system under test was started>
baseline: <verbatim result of the first run, before any harness repair>
scenarios: <n> passed / <n> total
harness_edits: <none, or the files changed and why>
findings: <none, or one line per product failure with evidence>
```

`baseline` and `harness_edits` together make a weakened test visible: a run that started red and turned green after an assertion edit reads as exactly that, instead of hiding behind a green result.

`BLOCKED` is a distinct verdict for "the system under test could not be started". Cypress and Maestro need a running app, and an agent under pressure to produce pass/fail will otherwise round "couldn't launch" into something. `BLOCKED` is never `PASS`, and the driver resolves the launch problem rather than the verifier guessing at it.

## Changes

**Config engine**

- [scripts/handoff-config.py](scripts/handoff-config.py) — `CORE_IDENTITIES` / `OPTIONAL_IDENTITIES` / `IDENTITIES`; `set --role` choices follow automatically.

**Setup engine** — the bulk of the work is auditing thirteen loops over `IDENTITIES`. Loops over a *mapping* become "identities present in that mapping, ordered by `IDENTITIES`"; loops that genuinely mean "all identities" stay.

- [scripts/handoff-setup.py](scripts/handoff-setup.py):
  - `PRESETS` gains both rows per preset; new `--with-e2e` / `--no-with-e2e` (default off)
  - `choose_identities` (custom mode, preset mode) — build `desired` from core plus e2e when enabled; the custom-mode error text stops saying "all three identities"
  - `preserve_verification`, `_cli_unavailable`, the `sources` population, `print_plan` — iterate `desired`
  - `smoke` — the `missing` gate over `CORE_IDENTITIES`; the smoke loop and the verified-write-back over `configured`
  - `--status` — keeps all five, `<unset>` for unconfigured
  - interactive custom mode — prompts for core, then one yes/no for the add-on
  - `render_agent` — two new branches; `ROUTING_POLICY` gains a line per configured e2e identity
- [scripts/handoff-setup-ui.py](scripts/handoff-setup-ui.py) — two `IDENTITY_META` entries; an "E2E acceptance testing (optional)" block with the enable toggle; `_payload` requires settings only for identities the payload enables; `engine_arguments` passes `--with-e2e`; `syncHeroMap` gains two targets.

**Runtime**

- [scripts/delegate-codex.sh](scripts/delegate-codex.sh) — `--role` case list; `--worktree` / `--base` on `submit`; worktree recorded in `meta` and honoured by `resume`; new `cleanup <jobId>`; `cancel` calls it; usage text.

**Receipt**

- [docs/receipt-schema.json](docs/receipt-schema.json) — `roles_used.role` enum gains both values; description notes the widening. `$id` and `receipt_schema_version` stay at 4.

**Flow prose** (product surface — CI gates it)

- **New** [references/e2e-gauntlet.md](references/e2e-gauntlet.md) — both delegation packets, the worktree protocol, the artifact contract, the harness-only rule, the verdict block, and the review-the-tests-first gate.
- [references/claude-driven.md](references/claude-driven.md) — Phase 1 criterion and the ordering above; Phase 2 `--worktree` usage; Phase 3 dependency handling; Phase 4 verifier gate, merge ownership, and `cleanup`.
- [references/goal-template.md](references/goal-template.md) — `depends` column; identity descriptions for both; a line saying e2e rows are optional.
- [SKILL.md](SKILL.md) — Configuration section (five identities, two optional); Output Contract note that `roles_used` may carry e2e roles; frontmatter `version: 3.1.0` → `3.2.0`.
- [README.md](README.md) — version badge, identity table, File Map line for `references/e2e-gauntlet.md`.
- [CHANGELOG.md](CHANGELOG.md) — `## v3.2.0` entry.

**Gates**

- [scripts/check-skill-repo.sh](scripts/check-skill-repo.sh) — `check_file "references/e2e-gauntlet.md"`.
- [test-prompts.json](test-prompts.json) — four cases: e2e rows added for a user-observable change; skipped for an internal refactor; verifier never edits product code; verifier never merges its own worktree.
- Tests — `test_handoff_config.py` (optional round-trip, write ordering, resolve omits unconfigured, three-identity file unchanged); `test_handoff_setup.py` (missing optional is not a failure, `--with-e2e` on/off, agent generation per backend, smoke skips unconfigured); `test_delegate_role.py` (both roles resolve, `--worktree` creates and records, `resume` uses it, `cleanup` is idempotent and refuses a dirty tree); `test_handoff_setup_ui.py` (meta present, payload accepts a disabled add-on).

## Risks

- **Forward compatibility.** `validate_config` raises on an unknown identity, so a five-identity config fails closed on a pre-3.2 engine. Acceptable: the skill and its config version together, and failing closed is the correct direction. Same shape as the receipt enum widening.
- **Setup engine breadth.** Thirteen loops, several of which currently assume `desired[identity]` always exists. The `--with-e2e` flag keeps the change mechanical, but this is where a regression would hide. Existing tests must pass unchanged before any new test is added.
- **Generated tests are only as good as the review.** The design puts a human-reviewable gate in front of the verdict rather than trusting the pair. If that review gets skipped in practice, the feature reports false confidence — worse than not having it. Watch this dimension specifically per the Darwin ratchet.
- **Cost.** Two extra jobs per user-observable change. The Phase 1 criterion is the control; if it fires too often the criterion tightens, not the identities.
