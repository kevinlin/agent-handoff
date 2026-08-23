# Design Review: E2E Gauntlet Roles

Reviewed inputs:

- [Design spec](design_e2e-gauntlet-roles.md)
- `Agent Gauntlet Pipeline.md`, the supplied concept note
- The current identity, delegation, goal, review, and receipt contracts

## Verdict

**Needs attention.** The two optional stages are useful, but the current design should not be implemented as written. It turns task roles into routing identities, loses required state at the worktree boundary, and does not make the verifier's result deterministic or resistant to test weakening.

The smaller design is to keep `deep_reasoner`, `fast_worker`, and `arbiter` as the only configurable identities. `e2e_specifier` and `e2e_verifier` should be functional roles expressed by delegation packets and goal rows. Both can normally run with `fast_worker`; the driver can select `deep_reasoner` when the specification is genuinely ambiguous.

## Irreducible Facts

- An identity is a routing choice, `backend + model + effort`. The repository says tasks select a capability, not a job title ([config schema](../config-schema.md#L5), [goal template](../../references/goal-template.md#L40)). This is a chosen architecture, but changing it has broad setup and compatibility cost.
- The two new jobs have different responsibilities and must start with clean context. This follows the source article's context-hygiene argument.
- `.handoff/` is runtime state and is ignored by Git ([.gitignore](../../.gitignore#L25)). A new Git worktree receives tracked content, not the main worktree's `.handoff/goal.md` or project config.
- A verifier result is trustworthy only when it identifies the exact code revision, exact test contract, target environment, command, and process result that produced it.
- Product repair and final acceptance remain driver-controlled. Letting the verifier edit product code would make it both judge and maker.

## Attack Points

### 1. P1, evidence: the design collapses functional roles into capability identities

The spec starts by saying identities "never [say] what work to do," then adds two identities named for work types ([design lines 5 and 14](design_e2e-gauntlet-roles.md#L5)). That forces task-specific concepts through configuration, presets, setup CLI, setup UI, generated agents, smoke tests, receipt enums, and thirteen identity loops ([design lines 125-162](design_e2e-gauntlet-roles.md#L125)). It also makes setup state decide whether E2E happens: an unconfigured role is skipped silently ([design lines 51-57](design_e2e-gauntlet-roles.md#L51)), although the requirement says the orchestrator decides per feature.

This creates two independent meanings for `role`: capability routing in config and receipt code, and pipeline responsibility in the new workflow. A user who wants the same model for implementation and E2E should not need three copies of the same routing values.

**Falsification experiment:** write the two proposed delegation packets and run both through the existing `fast_worker` identity. If any required behavior cannot be expressed in the packet, goal row, or acceptance criteria, name that behavior. Separate model configuration by job title is not itself a functional requirement.

**Required change:** keep the three identities. Add `e2e_specifier` and `e2e_verifier` as task roles or task labels, with an independent `identity` selection. Do not change config schema, presets, setup UI, generated agents, smoke logic, or the receipt role enum for this feature.

### 2. P1, evidence: the worktree handoff cannot see its declared inputs and has no safe integration point

The specifier contract reads `.handoff/goal.md` ([design line 94](design_e2e-gauntlet-roles.md#L94)), while the proposed worktree lives under an ignored `.handoff/` directory ([design lines 61-68](design_e2e-gauntlet-roles.md#L61)). The project goal and config are ignored runtime files, so they will not appear inside the new checkout. An uncommitted user-supplied spec has the same problem. The main flow currently passes a self-contained prompt file to each job ([delegation flow](../../references/claude-driven.md#L45)); the new design does not say how ignored inputs cross the boundary.

The merge sequence is also ambiguous. It can merge acceptance tests to `main` before implementation, leave `main` red, or start verification from a moving branch name rather than the reviewed combined commit ([design lines 76-90](design_e2e-gauntlet-roles.md#L76)). `--base` defaulting to the caller's current `HEAD` is not enough evidence that the verifier tested what will be merged.

**Falsification experiment:** create a goal file and an uncommitted spec, add the proposed worktree, and verify that both inputs and the resolved project config are available from that worktree. Then advance the named base branch before verifier submission and check which commit is actually tested.

**Required change:**

1. Resolve routing before creating the worktree.
2. Freeze the goal, supplied spec, artifact paths, and acceptance criteria into the job packet or a copied job artifact. Do not ask the worker to edit the driver's `.handoff/goal.md`; the driver records returned paths with `goal-sync.py`.
3. Record immutable `base_commit` and `tested_commit` SHAs in job metadata.
4. Merge implementation and reviewed acceptance artifacts into one feature or integration branch, not `main`.
5. Start the verifier from that exact combined commit. Merge to `main` only after the verifier passes and the driver completes final review.
6. Make `T_verify` depend on both `T_spec` and `T_impl`, not only the implementation row.

### 3. P1, evidence: a reviewed test can be weakened after review and still report PASS

The driver reviews the specifier's tests before verification ([design lines 86-88](design_e2e-gauntlet-roles.md#L86)). The verifier may then edit the entire test tree, including assertions, and the design treats `baseline` plus a prose edit list as sufficient disclosure ([design lines 104-121](design_e2e-gauntlet-roles.md#L104)). No later gate re-reviews the changed test semantics. A verifier can therefore turn a real failure into PASS by weakening an assertion after the only test review.

The fixed text block is also not a deterministic contract. Nothing validates its fields or proves that the reported scenario count, exit status, commit, and target environment agree with execution evidence.

**Falsification experiment:** give the verifier a failing assertion, let it change the expected value, and check whether the current flow accepts its PASS without a second driver review. Separately, return a syntactically valid PASS block after a non-zero test command and check whether any tool rejects it.

**Required change:**

- Freeze the reviewed Gherkin scenarios and behavioral assertions. The verifier may repair launch or runner scaffolding, but it may not change scenario meaning, expected outcomes, selectors used as the subject of an assertion, or product code.
- Any semantic test edit invalidates the run. Return to driver review, record a new artifact hash, and rerun from a clean state.
- Generate and validate a small machine-readable verdict artifact. At minimum record `result`, `base_commit`, `tested_commit`, artifact hashes, target environment, command, exit code, scenario IDs and counts, evidence paths, harness edits, and findings.
- Define PASS as a zero exit code with every reviewed scenario executed. Define FAIL as an executed scenario failure. Define BLOCKED as no valid execution because prerequisites, launch, credentials, environment, or tooling were unavailable.
- Require an approved local or test target. A remote, production, destructive, or billable test needs explicit authorization. Do not let the verifier discover the target by guessing.

## Additional Findings

### P2, evidence: the artifact ownership differs from the source pipeline

The source note assigns Gherkin plus a human QA procedure to the Specifier, then assigns conversion of that procedure into machine-executable automation to the QA agent. The design instead makes the Specifier write Gherkin, executable automation, and the human procedure, while the verifier mainly runs and repairs them ([design lines 92-106](design_e2e-gauntlet-roles.md#L92)).

Choose one contract and use its terms consistently. The closest match to the source is:

- `e2e_specifier`: Gherkin plus a human-runnable QA procedure, with stable scenario IDs.
- `e2e_verifier`: repo-native automation mapped to those scenario IDs, execution evidence, and verdict.

If the intended product requirement is instead that the specifier writes Cypress, Maestro, or API automation, say that this deliberately differs from the source and make the verifier read-only for behavioral test content.

### P2, evidence: the generic packet forbids the commits this feature requires

The shared delegation packet says, "Do not commit" ([packet template](../../references/handoff-template.md#L23)). The feature requires both E2E agents to commit work in their worktrees. The new packet must replace that line with "commit on this worktree branch; do not merge, push, or delete the worktree." Appending a conflicting instruction is not enough. Driver-owned merge should remain a hard stop.

### P2, evidence: the design overstates its coverage of the five-stage Gauntlet

The supplied article has Specifier, Coder, Cleaner, Hardener, and QA stages. Cleaner includes CRAP analysis and Hardener includes mutation testing. Handoff's current diff review and fastest relevant check are not equivalents. This feature adopts two ideas from the Gauntlet; it does not close the whole Gauntlet gap. Reword the context so future readers do not assume cleanup and mutation-hardening gates exist.

### P2, inference: Claude and Codex worktree behavior is not equivalent yet

The Codex proposal records branch, base, worktree, and cleanup metadata. The Claude path is summarized as Task-tool `isolation: "worktree"` with no equivalent base pinning, metadata, resume, commit, or cleanup contract ([design lines 59-70](design_e2e-gauntlet-roles.md#L59)). Either define one driver-owned worktree protocol for both backends, or scope the first version to the backend whose lifecycle can be verified. Do not call the two paths equivalent until the same acceptance checks pass for both.

## What Survives

- Keep both stages optional and let the driver decide per feature.
- Run specification work in parallel with implementation when both consume the same frozen source spec.
- Run verification only after reviewed tests and implementation exist on one combined commit.
- Keep worktree creation in a script or driver-controlled primitive. The agent should work inside the prepared worktree, not improvise its own path.
- Keep merge ownership with the driver.
- Keep product fixes with the original implementer. The verifier reports product failures.
- Keep stack detection and refuse to install a new E2E framework silently.
- Keep PASS, FAIL, and BLOCKED distinct.

## Recommended Minimal Design

1. Add two functional role packets in `references/e2e-gauntlet.md`.
2. Add a `role` and `depends` column to the goal table while retaining `identity` for routing.
3. Extend `delegate-codex.sh` only with the common worktree lifecycle and immutable base metadata needed by these jobs.
4. Add one validated verdict artifact and its smallest validator, following the existing generated-and-validated receipt pattern.
5. Update the flow, packet template, test prompts, and focused runtime tests.
6. Leave config, setup presets, setup UI, generated identity agents, and `roles_used` unchanged.

The corrected flow is:

```text
frozen goal/spec
  |-- T_spec: role=e2e_specifier, identity=fast_worker, base=<sha>
  `-- T_impl: role=implementer,   identity=fast_worker, base=<sha>

driver reviews T_spec
driver integrates T_spec + T_impl on feature branch at <combined-sha>

T_verify: role=e2e_verifier, identity=fast_worker,
          depends=T_spec,T_impl, base=<combined-sha>

PASS    -> driver reviews verifier diff/evidence -> merge decision
FAIL    -> original implementer fixes product -> new combined SHA -> rerun
BLOCKED -> driver resolves prerequisite or reports blocked; never treat as PASS
```

## Assignment

- Translate the frozen spec into reviewed acceptance artifacts -> owner: Codex, role: `e2e_specifier`, identity: `fast_worker`. Use `deep_reasoner` only when requirements are ambiguous.
- Implement the product change -> owner: Codex, role: implementer, identity: `fast_worker` unless the existing split gate selects otherwise.
- Review acceptance strength and integrate both branches -> owner: Claude driver, identity: `-`. This is cross-task integration and final gate ownership.
- Automate or execute the reviewed QA procedure and produce evidence -> owner: Codex, role: `e2e_verifier`, identity: `fast_worker`.
- Fix product findings -> owner: the original implementer, identity: its original identity. Do not transfer product ownership to the verifier.
- Re-review any changed acceptance semantics and decide whether to merge -> owner: Claude driver, identity: `-`.

## Open Questions

- Does "fix any errors from the test" mean test harness defects only, or product defects too? Recommended answer: harness defects only. If product edits are intended, the independence claim and driver review sequence must be redesigned.
- Is the Specifier expected to write machine automation, or only Gherkin plus a human QA procedure? Recommended answer: follow the source split unless the product requirement explicitly chooses otherwise.
- Must both backends be supported in the first release? Recommended answer: only claim the backend path covered by the same worktree, commit, resume, cleanup, and verdict tests.
