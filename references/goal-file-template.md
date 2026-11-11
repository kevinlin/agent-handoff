# Goal file template

`<repo>/.handoff/goal.md` holds the plan and the delegation state of one run, so a `/loop` tick or a resumed session can pick the run up without rebuilding context. It is rewritten for each run. Update it in place through `goal-sync.py`, and keep no parallel copy.

```markdown
# Handoff Goal

## Goal
[One why-forward sentence: working on X for Y, so that Z. Done when: <verifiable completion condition>. Anti-Goodhart: the done_when check must not be satisfiable by deleting tests, skipping steps, or weakening the acceptance bar — if it can be, fix the check, not the standard.]

## Checkpoint Rule
Pause for the user only on: a destructive or irreversible action, a real
scope change, or something only the user can provide. Otherwise keep going
and report when done.

## Spec Review
status: not run
[statuses: not run | round <n> running | awaiting arbitration | consensus | approved | rejected | failed]
[round <n>: <jobId> — each blocking finding accepted or declined, with the reason for each decline]

## Delivery
[Only under the full protocol in references/pr-delivery.md. Otherwise "n/a".]
- branch/worktree: <name or path, or n/a>
- pr: <URL, or n/a>
- ci: <status, or n/a>
- preview: <URL/status, or n/a>
- live: <status, or n/a — production/deploy state, verified independently of ci/preview>
- authorization: <one line per hard-stop action actually authorized, verbatim user intent, or none yet>

## Tasks
| id | identity | task | acceptance | depends | effort | status | jobId |
|----|----------|------|------------|---------|--------|--------|-------|
| T1 | deep_reasoner | ... | ... | - | - | in_progress | - |
| T2 | fast_worker | ... | [check command that must pass] | - | high | delegated | job-... |
| T3 | e2e_verifier | ... | verdict.json validates as PASS | T1,T2 | high | pending | - |

status: pending | in_progress | delegated | review | rework-<n> | arbitration | rejected | taken-back | done

## Anomalies
[none, or one line per monitoring anomaly: job, what happened, action taken]

## Arbitration
[none, or one line per escalation ruling, approve included: <gate> · <task id or plan> · <arbiter jobId or refusal message> · approve | reject | no verdict · <one-line reason> · [same-vendor]]

## Notes
[Integration decisions, takebacks after monitoring anomalies, and the handover for any rejected row: what the reviewer found, what the arbiter ruled, what continuing would take.]
```

## Rules

- One row per task. `identity` is the capability the work needs (Phase 1 of the flow), or `-` for a row the driver keeps inline. Which CLI runs a row is the identity's config and is never written in this table.
- `arbiter` takes no routine rows. `references/arbitration.md` invokes it.
- `acceptance` is verifiable: a command to run or a behaviour to observe. Phase 4 reviews against it.
- `depends` is a comma-separated list of task ids that must reach `done` before the row is submitted, or `-`. An `e2e_verifier` row lists both the specifier row and the implementation row.
- `jobId` comes from `delegate-job.sh submit`. Every delegated row gets one, and a `-` row stays at `-`.
- Keep statuses current. The monitor loop reads this file first, and a stale row causes a duplicate delegation.
- `## Spec Review` holds the status line and one line per round. The file is rewritten per run, so the marker resets by itself.
- `## Arbitration` records every escalation ruling, approvals included.
