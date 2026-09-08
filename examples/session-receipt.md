# Agent Handoff Session Receipt Example

A receipt from a real session that delegated four jobs to Codex. This is the minimal public proof that Handoff reports verifiable session behavior instead of claiming token savings.

```text
[Handoff session receipt]
phase: review
claude_session: c5d355c5-0eb1-4d57-9421-73a245d9a69c
duration: 96min 40sec
checks: python3 -m unittest discover -s tests -t . (195 OK); /usr/bin/python3 -m unittest discover -s tests -t . (195 OK, 3.9.6 floor); tests.test_stdlib_only OK; git -C kevinlin.github.io status --short empty; live acceptance run, 15 rows, all passed
anomalies: other: two Goodhart resolutions caught in review, one per job (a getattr built from string concatenation to defeat a grep; two preflight tests merged into subtests to hit a stated count) - both reversed in one fix round each; plus a driver-side shell slip that truncated .handoff/goal.md, rebuilt from a scratchpad copy with no work lost
codex_jobs: 4
codex_job_durations: job-t1=18min 12sec; job-t1-r2=6min 05sec; job-t2=24min 31sec; job-t3=9min 47sec
cc_jobs: 0
cc_job_durations: none
scope: project
config_source: project
roles_used: [{"role":"fast_worker","host":"codex","model":"gpt-5.6-sol","effort":"high","verified":true}]
receipt_schema_version: 5
```

The run predates the `.handoff/session-start` marker, so the two timing lines were backfilled when the schema moved to v4. The durations and job ids in them are placeholders, not measurements. It also predates backend dispatch, so every job it ran was codex-backed and the v5 `cc_` pair reads zero. Everything else is what that session emitted. The next real run replaces this example.

How to read it:

- `duration` is wall clock from the Phase 0 marker to the receipt, so time the run spent blocked on a permission prompt is inside it. `codex_job_durations` breaks that down per delegated job.
- `codex_jobs: 4` is counted from the job directories under `.handoff/jobs/`, fix rounds included — not from recall. `cc_jobs` counts the same way for jobs whose `meta` records `backend=claude`; the two are partitioned, never double-counted.
- `checks` names the commands that ran and what each returned, so a reader can rerun them.
- `anomalies` records what went wrong: two Goodhart resolutions the full-review gate caught (a `getattr` assembled from string concatenation to defeat a grep; two preflight tests merged into subtests to hit a stated count), each reversed in one fix round, plus a driver-side shell slip that truncated the goal file. On a run like this, `anomalies: none` would itself be the failure.
- `roles_used` carries the model and effort that actually executed the role, with `verified` read from the config rather than guessed.

This example is validated in CI: `python3 scripts/validate-receipt.py examples/session-receipt.md`.

Use exact token counts only when reliable telemetry is available. Without telemetry, report behavior that can be verified from job state, transcripts, and repo evidence.
