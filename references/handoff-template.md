# Agent Handoff Packet Templates

Two packets, both bounded: the delegation packet Claude sends to a Codex job, and the Goal Packet Claude sends to the user for authorization. Cite evidence; do not paste the whole repo.

## Claude → Codex Delegation Packet

Use this packet when Claude Code delegates a task to Codex via `delegate-codex.sh submit`. It follows `references/fable5-principles.md`: why-forward opening, one-sentence task, verifiable acceptance, only genuine constraints, fixed output discipline.

```markdown
# Handoff Delegation

## Context
I'm working on [the larger task] for [who it's for]. They need
[what the output enables]. With that in mind:

## Task
[One clear sentence. What to produce or change.]

## Acceptance
- [Verifiable condition, e.g. `npm test` passes, all call sites migrated]
- [Check command Codex must run before finishing]

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
- The e2e packets in `references/e2e-gauntlet.md` **replace** the "Do not commit" constraint line with a commit-on-this-worktree-branch rule. They are the only packets that do; do not append a commit permission to this template.

## Goal Packet (Plan→Goal→PR→Verification, `references/goal-to-pr.md`)

Use this packet to present a Stage 1 plan for authorization before writing `.handoff/goal.md` and starting Stage 3. It is a decision artifact for the user, not a delegation packet for Codex.

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
