# Agent Handoff Session Receipt Example

A real receipt from a session that delegated four jobs to Codex. This is the minimal public proof that Handoff reports verifiable session behavior instead of claiming token savings.

```text
[Handoff session receipt]
phase: review
claude_session: c5d355c5-0eb1-4d57-9421-73a245d9a69c
checks: python3 -m unittest discover -s tests -t . (195 OK); /usr/bin/python3 -m unittest discover -s tests -t . (195 OK, 3.9.6 floor); tests.test_stdlib_only OK; git -C kevinlin.github.io status --short empty; live acceptance run, 15 rows, all passed
anomalies: other: two Goodhart resolutions caught in review, one per job (a getattr built from string concatenation to defeat a grep; two preflight tests merged into subtests to hit a stated count) - both reversed in one fix round each; plus a driver-side shell slip that truncated .handoff/goal.md, rebuilt from a scratchpad copy with no work lost
codex_jobs: 4
scope: project
config_source: project
roles_used: [{"role":"fast_worker","host":"codex","model":"gpt-5.6-sol","effort":"high","verified":true}]
receipt_schema_version: 3
```

How to read it:

- `codex_jobs: 4` is counted from the job directories under `.handoff/jobs/`, fix rounds included — not from recall.
- `checks` names the commands that ran and what each returned, so a reader can rerun them.
- `anomalies` records what went wrong: two Goodhart resolutions the full-review gate caught (a `getattr` assembled from string concatenation to defeat a grep; two preflight tests merged into subtests to hit a stated count), each reversed in one fix round, plus a driver-side shell slip that truncated the goal file. On a run like this, `anomalies: none` would itself be the failure.
- `roles_used` carries the model and effort that actually executed the role, with `verified` read from the config rather than guessed.

This example is validated in CI: `python3 scripts/validate-receipt.py examples/session-receipt.md`.

Use exact token counts only when reliable telemetry is available. Without telemetry, report behavior that can be verified from job state, transcripts, and repo evidence.
