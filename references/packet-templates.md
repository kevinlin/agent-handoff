# Packet templates

The prompts the driver writes for delegated jobs. Routing comes from config, so a packet never sets the backend, model or effort its job runs on.

## Rules for every packet

- **Open with why.** Say who the work is for and what the output enables, then make the request. A frontier model performs on purpose, so never send a bare instruction.
- **Keep it short.** State one clear task and how it will be checked. List a constraint only when it is a real blocker. A long constraint list degrades the output of a model that would have found the approach itself, so trust the worker with decisions inside the scope. Do not reuse prompt templates written for older, weaker models.
- **Write acceptance as commands or observable behaviour.** It is what the Phase 4 review checks.
- **Give the evidence, not the repo.** Cite the paths, the failing check's output, the snippet that carries the decision. The worker then spends its tokens on the work instead of on rediscovering the repo.
- **State the checkpoint rule.** Without one, an autonomous agent sets its own stopping points.
- **End with the output rule.** In controlled runs a terse output contract cut reviewer output by 41% with no loss in judgment. The lessons-learned lines feed the wrap-up memory step.
- **A worker with its own memory store** gets one more line: "Store one lesson per note with a one-line summary. Record corrections and confirmed approaches alike, including why they mattered. Don't save what the repo or chat history already records. Update an existing note rather than duplicating it. Delete notes that turn out to be wrong."

## Delegation Packet

For every task row submitted with `delegate-job.sh submit`, on any backend.

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

**Fix round.** A `resume` goes to the session that still holds the original packet. Send only the prioritized review findings, the acceptance criteria that failed, and "Continue end-to-end from here." The same shape recovers a worker that stopped early: "Continue end-to-end from [last checkpoint]. Reference: [goal file / job log]. Report back when complete." Never restart a task from zero. The one exception is a round that lost its session, which the backend file tells you how to recognise: resubmit that one fresh, with the full packet.

**E2E packets** (`references/e2e-acceptance.md`) replace the "Do not commit" line with a commit-on-this-worktree-branch rule. They are the only packets that do. Never append a commit permission to this template.

**Artefact rows** (`references/artefact-rows.md`) add output, evidence and long-command lines to this packet. A row that makes a paid call replaces the secrets clause with the one that file gives. No other packet relaxes it.

## Spec Review Packet

Sent on every plan before it reaches the user, as a read-only job to `deep_reasoner` with the label `spec-review` (Phase 1 of the flow). Put the plan in verbatim. The reviewer starts cold and judges what it is given, so it must not have to go looking for the plan. Where the backend's read-only mode cannot run commands, inline everything the reviewer must see and state check results instead of asking for them.

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
- Prioritized findings, worst first, each tagged `blocking` (the plan should not go ahead as written) or `advisory`, each naming what breaks and where.
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

The Arbitration Packet is in `references/arbitration.md`, the Goal Packet in `references/pr-delivery.md`, and the two e2e packets in `references/e2e-acceptance.md`.
