# E2E acceptance

One optional identity, `e2e_tester`, adds acceptance coverage to a run. It writes the gate in one job and runs it in a second, and the driver judges the gate itself. This file calls the first job the specifier and the second the verifier. The Phase 4 review catches a diff that does not do what the brief said. It does not catch a feature that fails when a person uses it. These two jobs cover that gap, and only that: there is no cleanup stage and no mutation-testing stage here.

## When to use

Phase 1 of the flow decides whether a run gets e2e rows: it does when the change alters user-observable behaviour at a real interface. `e2e_tester` is off by default, and `/agent-handoff config --with-e2e` configures it. An unconfigured identity passed to `delegate-job.sh --role` is refused like any other.

A goal file written before 4.0.0 names `e2e_specifier` and `e2e_verifier`. Read both as `e2e_tester`. A row with no job yet gets its identity cell rewritten before you submit it. A row that already has a job keeps it, and its fix rounds `resume` that job. Leave rows in `done`, `rejected` or `arbitration` as they are.

## Order

```text
frozen goal excerpt + spec, base = <sha0>
  |-- T_spec  identity e2e_tester      worktree off <sha0>   parallel
  `-- T_impl  identity fast_worker     branch/worktree as usual

driver reviews T_spec acceptance strength against goal criteria
driver integrates T_spec + T_impl onto the feature branch -> <sha1>
driver records sha256 of the reviewed .feature files

T_verify  identity e2e_tester  depends T_spec,T_impl  base <sha1>

PASS    -> driver reviews verifier diff and evidence -> merge decision
FAIL    -> original implementer fixes product (a review round on its chain) -> new combined sha -> rerun
BLOCKED -> driver resolves the prerequisite -> fresh verifier submit on the same sha; never treated as PASS
```

- Nothing merges to `main` mid-flight. The tests and the implementation meet on the feature branch, because tests merged ahead of the implementation would leave `main` red. `main` is reached only after a PASS and the driver's final review, and a merge is still a hard stop.
- The verifier row lists both the specifier row and the implementation row in `depends`. It needs the tests in its tree, not only the feature.
- The verifier tests an exact commit. `<sha1>` is pinned before submission and comes back in the verdict.
- The verifier is a fresh `submit`, never a `resume` of the specifier job. The run that executes the gate must not carry the session that wrote it.

## Worktrees

E2E rows run in a worktree, on any backend:

```bash
bash "$HANDOFF_DIR/scripts/delegate-job.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label <task-id> \
  --role e2e_tester --worktree "e2e/<task-id>" --base "$SHA0"
```

1. `--repo` is always the main repo. A worktree is a working directory derived from it, never a `--repo` value: passing one falls through to the global config and runs the wrong model.
2. Cut from an immutable commit SHA, never a branch name. A branch can advance between cutting the worktree and reading the verdict, and then the verdict names a commit nobody tested. `--base` defaults to `HEAD`.
3. The worker commits on its worktree branch. It does not merge, push, rebase or remove the worktree.
4. The driver reviews, integrates and cleans up.

The worktree lands at `<repo>/.handoff/worktrees/<jobId>`, and a fix round (`resume`) lands in the same tree on the same CLI. Once a worktree is merged or abandoned, run `delegate-job.sh cleanup <jobId> --repo "$REPO"`. It refuses a worktree with uncommitted changes and reports it instead of discarding work.

Do not run `git worktree add` by hand for these rows, and do not use the Task tool's `isolation: "worktree"`: neither pins the base, records the metadata or has a cleanup contract.

## The packets carry everything

A worktree holds tracked content only. `.handoff/` is gitignored, so `.handoff/goal.md` and `.handoff/config.toml` do not exist inside it.

- No e2e job reads `.handoff/`. Put the frozen goal excerpt, the spec, the acceptance criteria and the artifact paths in the packet.
- No job writes `.handoff/goal.md`. Workers return artifact paths in their result, and the driver writes them with `goal-sync.py`.
- A user-supplied spec that is not committed must be inlined in the packet or committed before the worktree is cut. Pick one explicitly.

Both packets follow the Delegation Packet in `references/packet-templates.md`, with its "Do not commit" line replaced.

### Specifier packet

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

The specifier detects the stack from the repo (`package.json`, `.maestro/`, existing test directories) and states its choice. Keeping scaffolding out of the spec files means the verifier's permitted repairs land outside the files that carry behavioural meaning, so the driver's re-review stays small.

### Verifier packet

The verifier runs the gate. It does not make it pass.

- **Frozen:** Gherkin scenario text and IDs, expected values in assertions, and what each assertion is about.
- **Repairable:** launch and runner scaffolding, such as start commands, the base URL, waits, fixtures, and selector resolution that does not change what is asserted.
- **Forbidden:** product code.

A semantic edit voids the run. The verifier reports it and stops. The driver re-reviews the changed acceptance semantics, records a new hash, and the run restarts from a clean state.

The packet names an approved local or ephemeral test target. A remote, production, destructive or billable target needs explicit user authorization, recorded like any other hard stop. The verifier never finds its target by guessing.

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
  `assets/verdict-schema.json`, filled from the run you actually performed.
- `scenarios.ids` lists the scenario IDs you executed.
- `harness_edits` names every file you repaired.

## Constraints
- Repair launch and runner scaffolding only. Never change scenario text,
  scenario IDs, expected values, or what an assertion is about.
- Never change product code. A product failure is a finding you report,
  not a bug you fix.
- If a scenario needs a semantic change to run, report it and stop. Do not
  make the change; it voids the run.
- Use the repository's lockfile-installed test runner. Do not download one.
- Commit your work on this worktree branch. Do not merge, push, rebase,
  remove the worktree, or touch secrets or `.env` files.

## Output
- DO NOT send optional commentary. Answer only what was asked.
- Report the verdict path and the result.
```

## Phase 4 sequence

Run these six steps in order, before any fix round.

1. **Review the specifier's scenarios** against the acceptance column in `.handoff/goal.md`. This step is easy to skip, and the verdict is worth nothing without it: a generated test weaker than the goal's criteria passes a broken feature, and the verdict inherits that weakness.
2. **Record the hash of what you reviewed**, at that moment:

   ```bash
   REVIEWED_SHA=$(find features -name '*.feature' | sort | xargs cat | shasum -a 256 | cut -d' ' -f1)
   ```

   The hash covers the `.feature` files only. Executable spec files hold repairable scaffolding, so they cannot be hashed whole. For those, the verdict's `harness_edits` names every file the verifier changed, and the driver diffs exactly those files.
3. **Integrate** the specifier branch and the implementation branch onto the feature branch, as one combined commit.
4. **Submit the verifier** pinned to that commit (`--base <sha1>`). It writes `.handoff/e2e/<jobId>/verdict.json`:

   ```json
   {
     "result": "PASS",
     "base_commit": "<sha1>",
     "tested_commit": "<sha>",
     "scenarios_sha256": "<sha256 over the reviewed .feature files, path-ordered>",
     "target": "http://localhost:5173 (local dev server, started by the runner)",
     "command": "./node_modules/.bin/cypress run --spec cypress/e2e/checkout.cy.ts",
     "exit_code": 0,
     "scenarios": {"total": 7, "passed": 7, "ids": ["CHK-01", "..."]},
     "harness_edits": [],
     "findings": [],
     "evidence": ["cypress/videos/checkout.mp4"]
   }
   ```

   Validate it before acting on it. A typed `result: PASS` after a non-zero exit is exactly what the validator refuses:

   ```bash
   python3 "$HANDOFF_DIR/scripts/validate-verdict.py" \
     "$REPO/.handoff/e2e/$JOB_ID/verdict.json" \
     --expect-scenarios-sha256 "$REVIEWED_SHA"
   ```

5. **Act on the result.**
   - **PASS** goes to the merge decision, after the driver reviews the verifier's diff and evidence.
   - **FAIL** is a product finding. It goes to the original implementer, never to the verifier, and counts as a review round on the implementation row's chain. That row's `done` was provisional: it goes back to `rework-<n>`, the verifier row goes back to `pending`, and every other row that depends on it is held. A dependent already running may finish, but its result is not accepted until the row is `done` again.
   - **BLOCKED** proved nothing. The driver resolves the prerequisite and submits a fresh verifier job pinned to the same commit. That is a `submit`, never a `resume`, so it is not a round. Never read BLOCKED as PASS.
6. **Clean up** each worktree once it is merged or abandoned.

When the implementation cap runs out with a FAIL still open, the dispute goes to the arbiter (`references/arbitration.md`). An approval there cannot replace a PASS.
