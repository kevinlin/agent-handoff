# Agent Handoff Packet Templates

Three packets, all bounded: the delegation packet the driver sends to a delegated job, the spec review packet it sends to `deep_reasoner` during planning, and the Goal Packet it sends to the user for authorization. Cite evidence; do not paste the whole repo.

## Handoff Delegation Packet

Use this packet when Claude Code delegates a task via `delegate-codex.sh submit`, whichever backend the row's identity is configured for — the packet is identical, because routing comes from config and never from the prose. It follows `references/fable5-principles.md`: why-forward opening, one-sentence task, verifiable acceptance, only genuine constraints, fixed output discipline.

```markdown
# Handoff Delegation

## Context
I'm working on [the larger task] for [who it's for]. They need
[what the output enables]. With that in mind:

## Task
[One clear sentence. What to produce or change.]

## Acceptance
- [Verifiable condition, e.g. `npm test` passes, all call sites migrated]
- [Check command the worker must run before finishing]

## Constraints
- Only touch: [paths in scope]. Do not touch: [paths out of scope].
- Do not commit, push, deploy, publish, or touch secrets or `.env` files.
- Pause only for a destructive or irreversible action, a real scope
  change, or something only the user can provide. Otherwise continue
  end-to-end and report when done.

## Output
- DO NOT send optional commentary. Answer only what was asked — no
  preamble, no unsolicited suggestions, no closing remarks.
- End with at most 3 lines of lessons learned (for rollout memory).
```

Delegation packet rules:

- Acceptance criteria are what the Phase 4 full review checks against; write them as commands or observable behavior, never vibes.
- Keep the constraints section short — genuine blockers only. Trust the model with approach decisions inside the scope boundary.
- For fix rounds (`delegate-codex.sh resume`), send only: the review findings (prioritized), the acceptance criteria that failed, and "Continue end-to-end from here." Do not resend the whole packet.
- The e2e packets in `references/e2e-gauntlet.md` **replace** the "Do not commit" constraint line with a commit-on-this-worktree-branch rule. They are the only packets that do; do not append a commit permission to this template. The Spec Review Packet below moves in the opposite direction — it removes the write permission this template implies rather than widening it.

## Spec Review Packet (Phase 1, optional)

Use this packet when `deep_reasoner` reviews the plan before it reaches the user — automatically when its config carries `auto_review_spec = true`, or on request. It runs once per run. Send it as a read-only job on the identity's configured backend: `delegate-codex.sh submit --role deep_reasoner --read-only --label spec-review`. On a claude-backed `deep_reasoner`, `--read-only` becomes `--permission-mode plan`; on a copilot-backed one it becomes `--mode plan`, which never carries `--allow-all-tools`. The job, the jobId, and the receipt entry are the same on all three.

The spec goes in the packet verbatim. The reviewer starts cold and must not go looking for the plan itself; what it is given is what it judges.

```markdown
# Handoff Spec Review

## Context
I'm planning [the larger task] for [who it's for]. They need [what the
output enables]. I wrote the plan below and I am about to send it to them,
so I want one independent read of it first.

## Specification
[The plan verbatim: the goal line with its done_when, the task table with
identities and acceptance criteria, and the design or spec document under
review. Inline, not by path.]

## Task
Review this plan and report what you would change before any of it is
built.

## Acceptance
- Prioritized findings, worst first, each naming what breaks and where.
- Say plainly which acceptance criteria are not verifiable as written.
- Say plainly where the plan is wrong about the repository, and cite the
  evidence you were given for it.
- Name what is missing, not only what is wrong.
- If the plan is sound, say so in one line rather than inventing findings.

## Constraints
- Read-only. Do not edit any file, write the goal file, or change product
  code. Findings only.
- Judge the plan I wrote. Do not rewrite it into your own plan.
- Do not run the work; this is a review of a plan, not an implementation.

## Output
- DO NOT send optional commentary. Answer only what was asked.
- End with at most 3 lines of lessons learned.
```

Spec review rules:

- **The driver rules.** The findings are input to a judgment, never a verdict. Fold in what holds, say what you rejected and why, and keep ownership of the plan.
- **Once per run.** Record the outcome in the goal file's `## Spec Review` block; a non-empty block means the automatic review is spent. Another one takes an explicit user request.
- **Not blind, and not arbitration.** The plan under review is the driver's own answer, so nothing here is withheld. When the same problem has to be solved twice independently, that is the arbiter protocol in `references/claude-driven.md`, not this packet.

## Goal Packet (Plan→Goal→PR→Verification, `references/goal-to-pr.md`)

Use this packet to present a Stage 1 plan for authorization before writing `.handoff/goal.md` and starting Stage 3. It is a decision artifact for the user, not a delegation packet for a worker.

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

Rules:

- Send this before creating a branch, worktree, or `.handoff/goal.md` — Stage 1 (Plan) touches no files.
- A reply that only answers a question in this packet is not authorization to proceed; wait for an actual imperative.
- Keep it bounded: cite evidence, do not paste the whole repo.
