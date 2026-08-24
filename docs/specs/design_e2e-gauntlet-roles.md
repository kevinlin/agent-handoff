# Add e2e_specifier and e2e_verifier identities

Revised after an independent design review. Its attack points 2 and 3 and all four P2 findings are accepted and folded in below; attack point 1 is declined, with reasons in [Rejected review findings](#rejected-review-findings).

## Context

Handoff has three identities — `deep_reasoner`, `fast_worker`, `arbiter` — each a `backend + model + effort` triple. All three are *capability tiers*: they say how much brain and which meter, never what work to do.

Robert C. Martin's Agent Gauntlet Pipeline — a five-stage agent pipeline, described in his 2026-08-19 conversation with Matt Pocock — has two stages Handoff has no equivalent for:

- **Specifier** — turns a spec document into a Gherkin acceptance test and an executable QA procedure written from a human operator's perspective.
- **QA agent** — executes that procedure against the implemented feature and returns a deterministic pass/fail.

Handoff's Phase 4 review gate reads the diff against written acceptance criteria. That catches "the diff doesn't do what the brief said". It does not catch "the feature doesn't work when a person uses it". The two new identities close that gap.

**Scope: two of the Gauntlet's five stages, not the Gauntlet.** The Cleaner (CRAP-score cleanup) and Hardener (mutation testing) stages have no equivalent here and none is proposed. Handoff's diff review and fastest-relevant-check are not substitutes for either. A reader should not come away thinking this feature closes the whole gap.

Outcome: `e2e_specifier` and `e2e_verifier` exist as first-class identities, configurable per backend/model/effort like the other three, opt-in at setup, and skipped when the change isn't user-observable.

Decisions already made:

- **Full identities, optional tier.** Both join `IDENTITIES`, but a core/optional split keeps them out of the required-configuration gate. An existing config with three identities stays complete.
- **Script owns the worktree, driver merges.** `delegate-codex.sh` gains `--worktree`; merge stays with the driver, where the existing hard-stop rule lives.
- **Harness-only fixes.** The verifier repairs launch and runner scaffolding, never behavioral content and never product code. A product failure is a finding the driver routes back to the original implementer.
- **Repo-native artifacts.** Gherkin and the executable spec go into the repo's own test tree; paths returned in the job result and recorded by the driver.
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
- Phase 1: when the criterion fires but the identities are unconfigured, the driver **says so once** — "this change is user-observable; `/agent-handoff config --with-e2e` would add acceptance coverage" — then continues without them. Silent skipping would let setup state quietly override the orchestrator's judgment, which the requirement puts with the driver.

### Job packets are self-contained

A Git worktree receives **tracked content only**. `.handoff/` is gitignored, so `.handoff/goal.md` and `.handoff/config.toml` do not exist inside a fresh worktree. Verified empirically: `git worktree add` on a repo with an ignored `.handoff/` produces a checkout containing neither file.

Consequences, all binding:

- **No e2e job reads `.handoff/`.** The frozen goal excerpt, the user-supplied spec, acceptance criteria, and artifact paths are written into the prompt packet. This is what the existing delegation packet already does; the e2e packets follow it rather than inventing a shared-file channel.
- **Routing resolves against the main repo.** `delegate-codex.sh --repo` stays the main repo in every invocation; the worktree is a derived cwd, never a `--repo` value. Passing a worktree as `--repo` would silently fall through to the global config and run the wrong model.
- **No job edits `.handoff/goal.md`.** Workers return artifact paths in their result; the driver writes them with `goal-sync.py`, which is what that script's hash-checked write exists for.
- An uncommitted user-supplied spec must be inlined into the packet or committed before the worktree is cut. The driver picks; it cannot be left implicit.

### Worktree protocol

One protocol, two mechanisms. The protocol is driver-owned and documented once in `references/e2e-gauntlet.md`:

1. Resolve routing against the main repo.
2. Cut the worktree from an **immutable commit SHA**, never a branch name. A branch name can advance between cutting and verifying, and then the verdict names a commit nobody tested.
3. The worker commits on its worktree branch. It does not merge, push, or remove the worktree.
4. The driver reviews, integrates, and cleans up.

Codex mechanism — `delegate-codex.sh submit --worktree <branch> --base <commit-ish>`:

- `git rev-parse` resolves `--base` to a SHA up front; `git worktree add <repo>/.handoff/worktrees/<jobId> -b <branch> <sha>`
- `meta` records `worktree=`, `branch=`, `base_commit=` (the resolved SHA)
- `resume` reads `worktree=` from `meta` — `codex exec resume` takes cwd from the shell and accepts no `-C`, so this is required for a fix round to land in the right tree
- new idempotent `cleanup <jobId>` removes the worktree; `cancel` calls it. `cleanup` refuses a dirty worktree and reports it rather than discarding uncommitted work.

Claude mechanism — the driver runs the same two Git commands, passes the worktree path to the subagent, and records `base_commit` in the goal row. The Task tool's `isolation: "worktree"` is **not** used: it provides no base pinning, no metadata, and no cleanup contract, so it cannot satisfy the protocol.

**Parity is not claimed for v1.** The Codex path gets the full lifecycle test (create, record, resume-into, cleanup, dirty-refusal). The Claude path follows the same written protocol but is driver-executed and covered only by the flow prose and a test-prompt case. The risk section says so rather than the design implying both are equally hardened.

### Flow placement

Phase 1 gains one criterion: **add e2e rows when the change alters user-observable behaviour at a real interface (UI, API surface, mobile screen).** Skip for internal refactors, docs, config, and pure library work.

```text
frozen goal excerpt + spec, base = <sha0>
  |-- T_spec  role/identity e2e_specifier   worktree off <sha0>   parallel
  `-- T_impl  identity fast_worker          branch/worktree as usual

driver reviews T_spec acceptance strength against goal criteria
driver integrates T_spec + T_impl onto the feature branch -> <sha1>
driver records sha256 of the reviewed .feature files

T_verify  identity e2e_verifier  depends T_spec,T_impl  base <sha1>

PASS    -> driver reviews verifier diff and evidence -> merge decision
FAIL    -> original implementer fixes product -> new combined sha -> rerun
BLOCKED -> driver resolves the prerequisite; never treated as PASS
```

Three properties this ordering buys:

- **Nothing merges to `main` mid-flight.** Acceptance artifacts and implementation meet on the feature branch. Merging tests to `main` ahead of the implementation would leave `main` red; `main` is reached only after the verifier passes and the driver completes final review, which keeps merge inside the existing hard-stop rule.
- **`T_verify` depends on both rows**, not just the implementation — it needs the tests in its tree. The `depends` column carries both ids.
- **The verifier tests an exact commit.** `<sha1>` is pinned before submission and echoed back in the verdict.

The **review-the-tests-first** step is load-bearing. A generated acceptance test weaker than the goal's criteria will green-light a broken feature, and the verifier's verdict inherits that weakness. This is also what keeps the pair off the maker-is-also-judge failure the [Darwin ratchet](references/darwin-ratchet.md) warns about: the specifier writes the gate, the verifier runs it, the driver judges the gate itself, and product fixes go back to the original implementer.

Ordering needs one new `depends` column in the goal-file task table. The `/loop` monitor reads that file to decide what to submit next and has nowhere today to learn that `T_verify` waits on two rows.

### Specifier contract

Input: the frozen goal excerpt and spec, inline in the packet. Output, committed on its worktree branch:

- a Gherkin `.feature` file in the repo's existing convention, **every scenario carrying a stable ID** (`CHK-01`) — the join key the verdict reports against
- an executable spec in the repo's detected e2e stack — Cypress/Playwright for web, Maestro for mobile, the repo's own HTTP test idiom for API scenarios — with each test tagged by scenario ID
- a human-operator QA procedure in markdown, next to the executable spec

**This assignment deliberately differs from the source pipeline**, where the Specifier writes Gherkin plus a human procedure and the QA agent converts that procedure into automation. Here the specifier writes the automation too, and the verifier is read-only for behavioral test content. The reason is separation of concerns for the gate: whoever writes the assertions should not be the one who decides whether they passed. The source's split puts automation-writing and verdict-giving in the same agent; this one does not.

The specifier keeps **scaffolding separate from specs** wherever the stack allows — launch config, base URL, waits, fixtures, and custom commands in the stack's config/support files rather than inline in spec files. This is what makes the verifier's permitted repairs land outside the files carrying behavioral meaning, so a re-review stays small.

The specifier **detects** the stack from the repo (`package.json`, `.maestro/`, existing test dirs) and states its choice in its report. No config knob. If the repo has no e2e stack at all, it proposes one and stops rather than installing a framework unasked — adding a dependency is the driver's call.

### Verifier contract

**Frozen:** Gherkin scenario text and IDs, expected values in assertions, and the identity of the thing an assertion is about. **Repairable:** launch and runner scaffolding — start commands, base URL, waits, fixtures, selector resolution that does not change what is being asserted. **Forbidden:** product code.

A semantic edit does not get disclosed and accepted; it **invalidates the run**. The verifier reports it and stops, the driver re-reviews the changed acceptance semantics, records a new artifact hash, and the run restarts from a clean state.

**Target:** the packet names an approved local or ephemeral test target. A remote, production, destructive, or billable target requires explicit user authorization recorded like any other hard stop. The verifier does not discover its target by guessing.

**Verdict artifact.** The verifier writes `.handoff/e2e/<jobId>/verdict.json`; the driver runs `scripts/validate-verdict.py` before acting on it. This follows the repo's established pattern — receipt fields are generated and re-validated, never hand-typed — because the verdict is the input to a merge decision, and a prose block lets an agent type `result: PASS` after a non-zero exit.

```json
{
  "result": "PASS",
  "base_commit": "<sha1>",
  "tested_commit": "<sha>",
  "scenarios_sha256": "<sha256 over the reviewed .feature files, path-ordered>",
  "target": "http://localhost:5173 (local dev server, started by the runner)",
  "command": "npx cypress run --spec cypress/e2e/checkout.cy.ts",
  "exit_code": 0,
  "scenarios": {"total": 7, "passed": 7, "ids": ["CHK-01", "..."]},
  "harness_edits": [],
  "findings": [],
  "evidence": ["cypress/videos/checkout.mp4"]
}
```

The validator's value is the cross-field rules, not the shape:

- `PASS` requires `exit_code == 0`, `scenarios.passed == scenarios.total`, `total == len(ids)`, and empty `findings`.
- `FAIL` requires at least one finding and either a non-zero `exit_code` or `passed < total`.
- `BLOCKED` requires `passed == 0` and a non-empty `findings` naming the blocker. `command` and `exit_code` may be null — that is the point of the verdict.
- `scenarios_sha256` must equal the hash the driver recorded at review time. A weakened scenario changes the hash and the run is rejected before its verdict is read.

**The hash lock covers the `.feature` files only.** Executable spec files legitimately contain repairable scaffolding, so they cannot be hashed whole. For those, the validator requires `harness_edits` to name every changed file, and the driver diffs exactly those files. Mechanical lock on the behavioral contract, bounded human review on the rest — stated plainly rather than implying the hash covers everything.

### Delegation packets

The shared packet template's constraint line reads "Do not commit, push, deploy, publish, or touch secrets or `.env` files." Both e2e packets **replace** that line — appending a contradicting instruction would leave the worker choosing between two rules:

> Commit your work on this worktree branch. Do not merge, push, rebase, remove the worktree, or touch secrets or `.env` files.

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

- [scripts/delegate-codex.sh](scripts/delegate-codex.sh) — `--role` case list; `--worktree` / `--base` on `submit` with `--base` resolved to a SHA and stored as `base_commit`; worktree recorded in `meta` and honoured by `resume`; new idempotent `cleanup <jobId>` that refuses a dirty tree; `cancel` calls it; usage text.
- **New** `scripts/validate-verdict.py` and `docs/verdict-schema.json` — structural validation plus the four cross-field rules above. No generator: unlike the receipt, every field comes from the run the verifier just performed, so validation is what closes the gap.

**Receipt**

- [docs/receipt-schema.json](docs/receipt-schema.json) — `roles_used.role` enum gains both values; description notes the widening. `$id` and `receipt_schema_version` stay at 4.

**Flow prose** (product surface — CI gates it)

- **New** [references/e2e-gauntlet.md](references/e2e-gauntlet.md) — both delegation packets with the replaced constraint line, the worktree protocol for both backends, the artifact contract, the frozen/repairable/forbidden split, the verdict artifact, and the review-the-tests-first gate.
- [references/claude-driven.md](references/claude-driven.md) — Phase 1 criterion and the unconfigured-but-applicable notice; Phase 2 `--worktree` usage and packet self-containment; Phase 3 two-row dependency; Phase 4 integration-branch sequence, verdict validation, merge ownership, and `cleanup`.
- [references/goal-template.md](references/goal-template.md) — `depends` column; identity descriptions for both; a line saying e2e rows are optional.
- [references/handoff-template.md](references/handoff-template.md) — one line noting that the e2e packets replace the "Do not commit" constraint, so the two templates don't read as contradictory.
- [SKILL.md](SKILL.md) — Configuration section (five identities, two optional); Output Contract note that `roles_used` may carry e2e roles; frontmatter `version: 3.1.0` → `3.2.0`.
- [README.md](README.md) — version badge, identity table, File Map lines for `references/e2e-gauntlet.md` and `docs/verdict-schema.json`.
- [CHANGELOG.md](CHANGELOG.md) — `## v3.2.0` entry.

**Gates**

- [scripts/check-skill-repo.sh](scripts/check-skill-repo.sh) — `check_file` for `references/e2e-gauntlet.md` and `docs/verdict-schema.json`.
- [test-prompts.json](test-prompts.json) — e2e rows added for a user-observable change; skipped for an internal refactor; the unconfigured-but-applicable notice; verifier never edits product code; verifier never merges its own worktree; a semantic test edit invalidates the run rather than being disclosed and accepted.
- Tests — `test_handoff_config.py` (optional round-trip, write ordering, resolve omits unconfigured, three-identity file unchanged); `test_handoff_setup.py` (missing optional is not a failure, `--with-e2e` on/off, agent generation per backend, smoke skips unconfigured); `test_delegate_role.py` (both roles resolve; `--worktree` resolves base to a SHA, creates, records; `resume` uses it; `cleanup` is idempotent and refuses a dirty tree); `test_validate_verdict.py` (each cross-field rule, hash mismatch rejection); `test_handoff_setup_ui.py` (meta present, payload accepts a disabled add-on).

## Rejected review findings

**Attack point 1 — collapse the two roles into task labels over the existing three identities.** Declined. This is the fork settled in brainstorming: the alternative was presented with its full cost ("~6 files of engine change plus tests") against the task-role option's zero-config-churn, and full identities were chosen for per-role backend/model/effort pinning. The review adds no fact that was not on the table when that call was made.

The review's strongest sub-point — that setup state should not decide whether E2E happens — is accepted, and handled above: when the Phase 1 criterion fires with the identities unconfigured, the driver says so instead of skipping silently.

Two costs of this choice are real and stay real: `role` carries two meanings (capability routing in config, pipeline responsibility in the flow), and a user wanting one model for both stages configures it twice. Both are accepted knowingly.

## Risks

- **Forward compatibility.** `validate_config` raises on an unknown identity, so a five-identity config fails closed on a pre-3.2 engine. Acceptable: the skill and its config version together, and failing closed is the correct direction. Same shape as the receipt enum widening.
- **Setup engine breadth.** Thirteen loops, several of which currently assume `desired[identity]` always exists. The `--with-e2e` flag keeps the change mechanical, but this is where a regression would hide. Existing tests must pass unchanged before any new test is added.
- **Backend parity.** The Codex worktree lifecycle is script-owned and tested; the Claude path is driver-executed prose following the same protocol. They are not equally hardened in v1, and the flow doc should not read as if they are.
- **The hash lock is partial.** Behavioral meaning that lives inside executable spec files rather than `.feature` files is guarded by bounded human review, not by the hash. Specifier discipline about separating scaffolding from specs is what keeps that review small; if that discipline slips, the review grows and gets skipped.
- **Cost.** Two extra jobs per user-observable change. The Phase 1 criterion is the control; if it fires too often the criterion tightens, not the identities.
