# Agent Handoff Goal File Template

The Handoff flow persists its plan and delegation state in `<repo>/.handoff/goal.md` so a `/loop` tick, a resumed session, or the other agent can pick up the state without rebuilding context. Update it in place as jobs progress; do not create parallel copies.

```markdown
# Handoff Goal

## Goal
[One why-forward sentence: working on X for Y, so that Z. Done when: <verifiable completion condition>. Anti-Goodhart: the done_when check must not be satisfiable by deleting tests, skipping steps, or weakening the acceptance bar — if it can be, fix the check, not the standard.]

## Checkpoint Rule
Pause for the user only on: a destructive or irreversible action, a real
scope change, or something only the user can provide. Otherwise keep going
and report when done.

## Spec Review
[One line. "not run" until it happens; then: done — <identity>, <jobId or subagent>, <what changed in the plan>. A non-empty line means the automatic review is spent for this run.]
status: not run

## Delivery
[Only used under the full Plan→Goal→PR→Verification protocol; references/goal-to-pr.md. Leave as "n/a" on the lightweight path.]
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

status: pending | in_progress | delegated | review | rework-1 | rework-2 | taken-back | done

## Anomalies
[none, or one line per monitoring anomaly: job, what happened, action taken]

## Notes
[Integration decisions and takebacks worth carrying into the receipt and memory.]
```

Splitting a task means making **one** judgment per row: which capability does this work need? The identities are defined by `/agent-handoff config`, each carrying its own backend (which CLI executes and which meter bills), model, and effort — so picking the identity picks the execution channel automatically; there is no separate "owner" decision:

- **`deep_reasoner`** — architecture, ambiguous requirements, root-cause diagnosis, anything where a wrong premise in step one is expensive to discover late. It also carries the optional Phase 1 spec review, which is a responsibility rather than a row in this table.
- **`fast_worker`** — mechanical, well-scoped, specification-complete work where the acceptance criteria alone are enough to verify correctness.
- **`arbiter`** — the blind second solver for contentious or high-stakes calls; normally invoked by the Arbiter protocol in `references/claude-driven.md`, not assigned routine rows of its own.
- **`e2e_specifier`** — optional. Turns a frozen specification into Gherkin scenarios with stable IDs plus repo-native executable tests. Runs in parallel with implementation; depends only on the spec.
- **`e2e_verifier`** — optional. Executes the reviewed tests against a pinned commit and produces a validated verdict. Depends on both the specifier row and the implementation row.

E2E rows are optional: add them only when the Phase 1 criterion fires and the identities are configured. See `references/e2e-gauntlet.md`.

Rows the driver keeps for itself — the split decision, cross-task integration, final acceptance — take identity `-`: they run inline in the driving session and never spawn or delegate.

At execution time every delegated row takes the same path: `delegate-codex.sh submit --role <identity>`. The identity's configured `backend` decides which CLI runs it and therefore which meter bills — `codex` on the Codex subscription, `claude` on the Claude meter — and nothing else about the job changes. The same identity can point at either vendor; that mapping lives in `.handoff/config.toml`, not in this table, and it is never re-decided per run to chase a cheaper meter.

Rules:

- One row per task; `jobId` comes from `delegate-codex.sh submit`. Every delegated row gets one, on either backend. Only identity `-` rows, which the driver keeps inline, stay at `-`.
- `acceptance` must be verifiable (a command to run, a behavior to observe), not a vibe. It is what Phase 4 reviews against.
- `depends` is a comma-separated list of task ids that must reach `done` before this row is submitted, or `-`. The `/loop` monitor reads it; a row with unmet dependencies is not submitted.
- The `/loop` monitoring prompt reads this file first, so keep statuses current — stale rows cause duplicate delegation.
- `## Spec Review` is the once-per-run marker for the optional `deep_reasoner` review of the plan (Phase 1 in `references/claude-driven.md`). A non-empty line means it already happened; nothing re-runs it automatically, and only an explicit user request produces another. This file is rewritten per run, so the marker resets by itself.
