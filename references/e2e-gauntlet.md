# E2E Acceptance Roles

Two optional identities, `e2e_specifier` and `e2e_verifier`, add acceptance coverage to a Handoff run: one writes the gate, the other runs it, and the driver judges the gate itself.

Phase 4's review gate reads the diff against written acceptance criteria. That catches "the diff doesn't do what the brief said". It does not catch "the feature doesn't work when a person uses it". These two roles close that gap.

**Scope.** This covers two stages of a longer acceptance pipeline, not a whole one. There is no cleanup stage and no mutation-testing stage here, and Handoff's diff review and fastest-relevant-check are not substitutes for either.

## When to use

Add e2e rows in Phase 1 when the change **alters user-observable behaviour at a real interface** — a UI, an API surface, a mobile screen. Skip them for internal refactors, docs, config, and pure library work.

Both identities are optional and off by default; `/agent-handoff config --with-e2e` configures them.

When the criterion fires but the identities are unconfigured, **say so once**:

> this change is user-observable; `/agent-handoff config --with-e2e` would add acceptance coverage

then continue without them. Setup state does not get to quietly override the driver's judgment. It does not block the run either.

An unconfigured identity passed to `delegate-codex.sh --role` hits the existing fail-closed error pointing at `/agent-handoff config`. There is no separate error path for these two, and no separate one per backend.

## Worktree protocol

Driver-owned, four rules, identical on all three backends:

1. **Resolve routing against the main repo.** `--repo` is always the main repo. A worktree is a derived working directory, never a `--repo` value — passing one falls through to the global config and runs the wrong model.
2. **Cut from an immutable commit SHA**, never a branch name. A branch can advance between cutting the worktree and reading the verdict, and then the verdict names a commit nobody tested.
3. **The worker commits on its worktree branch.** It does not merge, push, rebase, or remove the worktree.
4. **The driver reviews, integrates, and cleans up.**

### The command, on any backend

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label <task-id> \
  --role e2e_specifier --worktree "e2e/<task-id>" --base "$SHA0"
```

`--base` (default `HEAD`) is resolved to a SHA before the job directory exists, so an invalid base fails clean. The worktree lands at `<repo>/.handoff/worktrees/<jobId>`, and `meta` records `worktree=`, `branch=`, and `base_commit=`.

A fix round inherits the tree: `resume` reads `worktree=` and `backend=` from the parent's `meta`, so it lands in the same tree on the same CLI. None of `codex exec resume`, `claude --resume`, and `copilot --resume` takes a `-C`; all three get their cwd from the generated `run.sh`.

Once a worktree is merged or abandoned:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" cleanup <jobId> --repo "$REPO"
```

`cleanup` is idempotent and refuses a worktree holding uncommitted changes, reporting it rather than discarding work. `cancel` calls it and keeps the tree for inspection if it refuses.

**One code path serves all three backends.** The worktree protocol is pure Git and gains nothing from a per-backend implementation, so there is none: `tests/test_delegate_role.py` runs the whole lifecycle — create, record, immutable SHA pinning, invalid base refused before the job dir exists, worktree as cwd, idempotent `cleanup`, dirty-tree refusal, `resume` landing in the parent worktree — from one base class against codex, claude, and copilot. Do not hand-run `git worktree add` for a claude-backed or copilot-backed row; that path is gone.

The Task tool's `isolation: "worktree"` is still **not** used: it provides no base pinning, no metadata, and no cleanup contract, so it cannot satisfy this protocol.

## Packet self-containment

A Git worktree receives **tracked content only**. `.handoff/` is gitignored, so `.handoff/goal.md` and `.handoff/config.toml` do not exist inside a fresh worktree.

Binding consequences:

- **No e2e job reads `.handoff/`.** The frozen goal excerpt, the spec, the acceptance criteria, and the artifact paths go into the prompt packet, exactly as the existing delegation packet already works.
- **No job writes `.handoff/goal.md`.** Workers return artifact paths in their result; the driver writes them with `goal-sync.py`, which is what that script's hash-checked write is for.
- An uncommitted user-supplied spec must be inlined into the packet or committed before the worktree is cut. The driver picks; it cannot be left implicit.

## Specifier packet

Follows the delegation packet in `references/handoff-template.md`, with the constraint line **replaced**:

```markdown
# Handoff Delegation

## Context
I'm working on [the larger task] for [who it's for]. They need
[what the output enables]. The specification is inline below because this
job runs in a Git worktree, which carries no untracked files.

## Specification
[The frozen goal excerpt and the user-supplied spec, verbatim.]

## Task
Write the acceptance gate for this specification: Gherkin scenarios plus
executable tests in this repo's own e2e stack.

## Acceptance
- A Gherkin `.feature` file in this repo's existing convention, every
  scenario carrying a stable ID (`CHK-01`).
- Executable tests in the stack you detected, each tagged by scenario ID.
- A human-operator QA procedure in markdown, next to the executable spec.
- Launch scaffolding — start commands, base URL, waits, fixtures, custom
  commands — lives in the stack's config/support files, not inline in spec
  files.
- Your report names the stack you detected and why.

## Constraints
- Only touch: [the repo's test tree]. Do not touch product code.
- Commit your work on this worktree branch. Do not merge, push, rebase,
  remove the worktree, or touch secrets or `.env` files.
- If this repo has no e2e stack at all, propose one and stop. Do not
  install a framework — adding a dependency is the driver's call.
- Pause only for a destructive or irreversible action, a real scope
  change, or something only the user can provide.

## Output
- DO NOT send optional commentary. Answer only what was asked.
- Report the paths of every file you created.
- End with at most 3 lines of lessons learned.
```

The specifier **detects** the stack from the repo (`package.json`, `.maestro/`, existing test directories) and states its choice. There is no config knob for it.

Keeping scaffolding out of spec files is what makes the verifier's permitted repairs land outside the files carrying behavioural meaning, so a re-review stays small.

**This assignment differs from the source pipeline it borrows from**, where the specifier writes Gherkin plus a human procedure and the QA agent converts that procedure into automation. Here the specifier writes the automation too, and the verifier is read-only for behavioural test content. Whoever writes the assertions should not be the one who decides whether they passed.

## Verifier packet

Same replaced constraint line. The split it must respect:

- **Frozen** — Gherkin scenario text and IDs, expected values in assertions, and the identity of the thing an assertion is about.
- **Repairable** — launch and runner scaffolding: start commands, base URL, waits, fixtures, selector resolution that does not change what is being asserted.
- **Forbidden** — product code.

A semantic edit does not get disclosed and accepted; it **invalidates the run**. The verifier reports it and stops. The driver re-reviews the changed acceptance semantics, records a new artifact hash, and the run restarts from a clean state.

**Target.** The packet names an approved local or ephemeral test target. A remote, production, destructive, or billable target requires explicit user authorization, recorded like any other hard stop. The verifier does not discover its target by guessing.

```markdown
# Handoff Delegation

## Context
The acceptance gate for [feature] has been reviewed and is committed at
[commit]. Your job is to run it and report what happened, not to make it
pass.

## Task
Execute the reviewed acceptance tests against the pinned commit and write
the verdict artifact.

## Target
[The approved local or ephemeral target, e.g. a dev server on localhost
started by the runner.]

## Acceptance
- Write `.handoff/e2e/<jobId>/verdict.json` with the fields in
  `docs/verdict-schema.json`, filled from the run you actually performed.
- `scenarios.ids` lists the scenario IDs you executed.
- `harness_edits` names every file you repaired.

## Constraints
- Repair launch and runner scaffolding only. Never change scenario text,
  scenario IDs, expected values, or what an assertion is about.
- Never change product code. A product failure is a finding you report,
  not a bug you fix.
- If a scenario needs a semantic change to run, report it and stop. Do not
  make the change; it voids the run.
- Commit your work on this worktree branch. Do not merge, push, rebase,
  remove the worktree, or touch secrets or `.env` files.

## Output
- DO NOT send optional commentary. Answer only what was asked.
- Report the verdict path and the result.
```

## Verdict artifact

The verifier writes `.handoff/e2e/<jobId>/verdict.json`:

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

The driver validates it before acting on it:

```bash
python3 "$HANDOFF_DIR/scripts/validate-verdict.py" \
  "$REPO/.handoff/e2e/$JOB_ID/verdict.json" \
  --expect-scenarios-sha256 "$REVIEWED_SHA"
```

This follows the repo's established pattern of generating and re-validating receipt fields rather than hand-typing them. The verdict is the input to a merge decision, and a prose block lets an agent type `result: PASS` after a non-zero exit.

The rules that matter are the cross-field ones:

- `PASS` requires `exit_code == 0`, `scenarios.passed == scenarios.total`, at least one executed scenario, `total == len(ids)`, and empty `findings`.
- `FAIL` requires at least one finding and either a non-zero `exit_code` or `passed < total`.
- `BLOCKED` requires `passed == 0` and a finding naming the blocker. `command` and `exit_code` may be null — that is the point of the verdict.
- `scenarios_sha256` must equal the hash the driver recorded at review time.

## Review the tests first

Load-bearing, and easy to skip. A generated acceptance test weaker than the goal's criteria will green-light a broken feature, and the verifier's verdict inherits that weakness.

Before the verdict counts for anything, the driver reads the specifier's scenarios against the acceptance column in `.handoff/goal.md`, and records the hash at that moment:

```bash
REVIEWED_SHA=$(find features -name '*.feature' | sort | xargs cat | shasum -a 256 | cut -d' ' -f1)
```

This is also what keeps the pair off the maker-is-also-judge failure `references/darwin-ratchet.md` warns about: the specifier writes the gate, the verifier runs it, the driver judges the gate itself, and product fixes go back to the original implementer.

**The hash lock covers the `.feature` files only.** Executable spec files legitimately contain repairable scaffolding, so they cannot be hashed whole. For those, the validator requires `harness_edits` to name every changed file, and the driver diffs exactly those files. Mechanical lock on the behavioural contract, bounded human review on the rest.

If specifier discipline about separating scaffolding from specs slips, that review grows. A review that grows gets skipped.

## Ordering and integration

```text
frozen goal excerpt + spec, base = <sha0>
  |-- T_spec  identity e2e_specifier   worktree off <sha0>   parallel
  `-- T_impl  identity fast_worker     branch/worktree as usual

driver reviews T_spec acceptance strength against goal criteria
driver integrates T_spec + T_impl onto the feature branch -> <sha1>
driver records sha256 of the reviewed .feature files

T_verify  identity e2e_verifier  depends T_spec,T_impl  base <sha1>

PASS    -> driver reviews verifier diff and evidence -> merge decision
FAIL    -> original implementer fixes product -> new combined sha -> rerun
BLOCKED -> driver resolves the prerequisite; never treated as PASS
```

Three properties this ordering buys:

- **Nothing merges to `main` mid-flight.** Acceptance artifacts and implementation meet on the feature branch. Merging tests to `main` ahead of the implementation would leave `main` red. `main` is reached only after the verifier passes *and* the driver completes final review, which keeps merge inside the existing hard-stop rule.
- **`T_verify` depends on both rows**, not just the implementation — it needs the tests in its tree. The goal file's `depends` column carries both ids.
- **The verifier tests an exact commit.** `<sha1>` is pinned before submission and echoed back in the verdict.

A FAIL goes to the original implementer, never to the verifier: the verifier does not fix product code. A BLOCKED is resolved by the driver and is never read as a PASS.
