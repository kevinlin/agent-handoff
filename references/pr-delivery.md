# PR delivery (opt-in)

The full protocol: plan, goal, PR, verification. It runs only when the user asks for it, with words such as "full protocol", "PR delivery", "deliver a PR" or "goal mode". Everything in `references/delegation-flow.md` still applies. This file adds a delivery phase and the authorization rules that let it run unattended up to a hard stop.

- A one-off small change stays on the default flow. Escalating a one-line fix into a full goal and PR cycle is a cost, not a safety margin.
- Recurring, independently verifiable work the user wants to run by itself (a nightly sweep, a repeating audit) is not this protocol. Hand it to a loop skill instead of stretching this one to fit.

## Stage 1: Plan

- Work with plan-mode permissions, and read the real repo, not only the request.
- Produce: goal, non-goals, current-state evidence (`file:line`), file scope, phases, risks, acceptance criteria, a rollback plan, and the open decisions only the user can make.
- Run the Phase 1 adversarial gate on the plan, and clarify the need until you are about 95% sure of it. Do not let a plan through on a guessed understanding of what is wanted. Once it passes, the executing agent reviews it independently: the same agent must not be the only maker and the only judge of its own plan.
- This stage changes no tracked file: no branch, no worktree, no commit. The task table waits for Stage 2. The spec review still needs its record, so when the review starts, create `.handoff/goal.md` with the Goal line, the Checkpoint Rule and the `## Spec Review` block, and nothing else. A review that fails, is rejected, ends in `no verdict` or is interrupted then leaves its status, its rounds and any `## Arbitration` line on disk.
- Send the Goal Packet below and wait for an imperative.

## Stage 2: Goal

- Complete `<repo>/.handoff/goal.md` from `references/goal-file-template.md`, with its `## Delivery` section, through `goal-sync.py`.
- Keep the task table's status values as they are. The monitor loop's stop rule depends on them.
- Every `done_when` keeps the template's anti-Goodhart clause. A check that a maker could pass by making it meaningless is not a valid `done_when`.
- `done_when` and the hard stops are independent. Meeting every `done_when` never gives permission to cross a hard stop.

## Stage 3: PR

Once the user has authorized the protocol, run without asking again, up to a merge-ready PR with its preview verified:

1. Create the branch or worktree recorded in the goal file's Delivery section.
2. Implement each task, delegating as the flow's phases describe.
3. Run the Verify Ladder (Stage 4) after each risky change, not only once at the end.
4. Push the task branch, open or update the PR, and record its URL in `Delivery.pr`.
5. Wait for CI. On a failure, read the actual CI log instead of only re-running locally, then fix. Record the status in `Delivery.ci`.
6. Verify the preview deployment if the repo has one, and record it in `Delivery.preview`.

Then stop. Each of the following is its own hard stop. Each needs a fresh imperative sentence from the user, and none is implied by "finish it" or by an earlier authorization for a different action:

- Merge into the target branch.
- Anything that touches production (deploy, promote, migrate live data).
- Creating a release tag.
- A force-push of any kind.
- Deleting a branch, file or resource that is not scratch this session created.
- A destructive or irreversible migration.
- Any external publish (registry, marketplace, announcement, visibility change).

Record each authorization actually given, with the user's intent verbatim, in the goal file's `Delivery.authorization` field. It is the audit trail for why a blocked action ran.

## Stage 4: Verify Ladder

Fast feedback first, and climb only as far as needed:

1. The single targeted test for the change just made.
2. Lint and typecheck.
3. The affected module's test suite.
4. The full test suite and build.
5. Diff against acceptance: re-read the acceptance criteria in `.handoff/goal.md` and the actual diff, not the maker's report of what it did.
6. Secret scan.
7. Migration check, if the change touches schema or persisted state.

Rules throughout:

- Run only commands the target repo itself documents (package scripts, Makefile targets, its CI config). Never invent a CI or deploy command. When the repo has none for a rung, mark that rung `n/a` and say so.
- CI green does not mean the feature works, and a preview is not production. Report the three separately.
- After an authorized merge or deploy, verify the actual production page or API, and record the result in `Delivery.live`. Do not report success from the deploy log alone.
- The final report has one line per layer: local checks, branch and PR, CI, preview, production, remaining risk. A layer that was not reached says `not reached`.

## Authorization

- A question from the user ("should I open a PR?") asks for status. Answer it, and do not act on it.
- One imperative sentence authorizes one action. "Ship it" after a plan review authorizes Stage 3 up to merge-ready. It does not authorize a merge, and a later "looks good" does not either.
- When you are unsure whether an instruction covers a hard-stop action, treat it as not covering it, and ask.

## Goal Packet

Present the Stage 1 plan with this packet. It is a decision document for the user, not a delegation packet for a worker. A reply that only answers one of its questions is not authorization. Keep it bounded: cite evidence, do not paste the repo.

```markdown
# Handoff Goal Packet

## Ask
[One why-forward sentence: what the user asked for and why it matters.]

## Plan
- Goal / non-goals: ...
- Current-state evidence: [file:line citations, not vibes]
- File scope: ...
- Phases: ...
- Risks: ...
- Acceptance criteria: [verifiable per phase]
- Rollback plan: ...

## Gate Verdict
[The Phase 1 adversarial gate's outcome: which rows survived all three
questions, and the changes already folded into the plan above.]

## Open Decisions
[Anything only the user can decide — scope tradeoffs, priorities,
constraints not visible in the repo.]

## What Authorization Unlocks
Confirming this packet authorizes Stage 3 up through merge-ready PR +
preview verified. It does NOT authorize merge, production, tags,
force-push, deletion, destructive migration, or external publish — each of
those needs its own explicit imperative sentence when the time comes.
```
