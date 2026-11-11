# Arbitration

The `arbiter` identity serves two protocols, and they are opposites on one point. Blind arbitration withholds each solver's answer from the other. An escalation ruling hands the arbiter both sides of a dispute. Never read the independence of the first into the second. Every arbiter job is a real `delegate-job.sh` job on the identity's configured backend, so it has a jobId and appears in the receipt.

## Blind arbitration: a contested call

Use it when you judge an answer disputable, or the user says "arbitrate", "this is contested" or "second opinion".

1. Send the same problem, verbatim, to `deep_reasoner` and to `arbiter`: `delegate-job.sh submit --role deep_reasoner` and `--role arbiter`.
2. Keep both packets clean. Neither may contain the other solver's answer, its conclusion, or a hint of which way it leans ("X thinks A, verify it" is already contaminated). A contaminated run produces fake agreement, so rerun it. Do not patch it.
3. Compare the answers. On agreement, adopt the answer and note it as dual-verified. On disagreement you rule, and you record the point of divergence and your reasoning in the report and the goal file's Notes. Both solvers appear in `roles_used`.
4. When both identities run on the same vendor the protocol still runs, and the receipt notes `same-vendor` so the weaker independence is visible.

This differs from the Phase 1 gate, which attacks a plan you already have. Here a second solver works the same problem with no knowledge of the first answer.

## Escalation ruling: a review gate out of rounds

When a review gate reaches its cap without consensus, the dispute goes to `arbiter`. For the spec gate that means a blocking finding is still declined. For the implementation gate it means findings are still open after the last allowed pass. Always escalate. A disputed task is never taken back instead, because a takeback settles the dispute for the side that raised it.

1. Build the Arbitration Packet below with the whole dispute. Nothing is withheld, and the contamination rule does not apply.
2. Submit it read-only: `delegate-job.sh submit --role arbiter --read-only --label arbitrate-<task-id>` (`arbitrate-spec` for the plan).
3. Read the verdict. It is well formed only when the first line of the output is exactly `verdict: approve` or `verdict: reject` and the reasons do not contradict it. The ruling binds: record it whatever it says, and do not re-argue it.

   | Gate | Approve | Reject |
   |---|---|---|
   | Spec | the plan goes to the user as you revised it | final: no row is delegated, the run stops before Phase 2, the user gets the plan with the ruling, and Phase 5 still writes a receipt with phase `planning` |
   | Implementation | the diff is accepted over your open findings and the row goes to `done` | final: the row becomes `rejected` and its diff is set aside (Phase 4 of the flow) |

   An approval never stands in for an e2e PASS. With a validated FAIL among the open findings, approval means the scenario was judged wrong: the scenario goes back to step 1 of the e2e sequence, a new hash is recorded, and the verifier reruns.
4. An arbiter job that fails, stalls, is cancelled, or returns anything but a well-formed verdict is an anomaly. Resubmit one fresh arbiter job with the same packet. A second failure, or a `submit` refused before any job exists, is recorded as `no verdict`. The row, or the plan, stays in `arbitration`, the run wraps up, and only the user moves it.
5. Record every ruling, approvals included, as one line in the goal file's `## Arbitration` block: the gate, the task (`plan` for the spec gate), the arbiter's jobId or the refusal message, the verdict (`approve`, `reject` or `no verdict`), a one-line reason, and `same-vendor` when the arbiter's backend matches either party's. A rejection also writes a handover into Notes: what the reviewer found, what the arbiter ruled, and what continuing would take (raise the cap, rewrite the brief, or take the task over). Every line also goes into the receipt's `anomalies` (`references/session-receipt.md`).

### Arbitration Packet

```markdown
# Handoff Arbitration

## Context
I'm working on [the larger task] for [who it's for]. A review gate ran out
of rounds without agreement, and your ruling decides what happens next.
Gate: [spec | implementation]. Task: [task id, or "plan"].

## Brief
[The plan, or the task brief with its acceptance criteria, verbatim.]

## Evidence
[Implementation: the full scoped diff and the results of the checks the
driver ran. Spec: the plan as the driver revised it.]

## Dispute
[Every round in order: each finding with its tag, and the other side's
response, whether the driver's disposition and reason or the worker's report.]

## Task
Rule on the dispute: does the [plan | diff] satisfy the brief, given the
findings still open?

## Acceptance
- The first line of your answer is exactly `verdict: approve` or
  `verdict: reject`.
- Then one reason per disputed point, citing the evidence above.
- Approve only if every open finding is wrong or does not block the brief.

## Constraints
- Read-only. Do not edit any file, write the goal file, or change product
  code.
- Rule on the open findings. Do not propose your own solution, and do not
  reopen points both sides already agreed on.

## Output
- DO NOT send optional commentary. Answer only what was asked.
- End with at most 3 lines of lessons learned.
```
