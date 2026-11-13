---
name: agent-handoff
version: 4.0.1
description: |
  Agent Handoff: Claude Code plans, splits and reviews project work while a worker CLI (Codex, a second Claude Code, GitHub Copilot, Cursor or Antigravity) executes it as background jobs, and every run ends with a receipt. Use on "agent handoff" or "/agent-handoff" with any sub-command: resume, config (also setup, init), tryout, transcript, cost-receipt, visualise (visualize), migrate. Also use on "hand this off to codex", "delegate this to codex", "let codex do it", "run codex in the background", "Claude plans, Codex implements", or any request to split work between Claude Code and a worker CLI to save quota; on "resume", "config", "setup" or "tryout agent handoff"; on a request that names a Handoff identity (deep_reasoner, fast_worker, arbiter); and on requests to show a delegated job's transcript or conversation history, a run's cost receipt, the session timeline, or to migrate handoff evidence. Not for ordinary code review. Do not trigger on the bare English word "handoff" in unrelated contexts.
---

# Agent Handoff

> Claude Code decides, a worker CLI executes — every handoff leaves a receipt.

Claude Code is the driver. It plans and splits the work, and it reviews every diff before accepting it. A worker CLI executes each delegated task as a durable background job. Delegating buys two things: execution on another meter, and a driver context kept clear of execution history.

This file routes. Find the command below, read the file it names, and read further files only when that file sends you to one.

## Rules that always hold

- **The identity's configured `backend` decides which CLI runs a job.** A task row names an identity (`deep_reasoner`, `fast_worker`, `arbiter`, `e2e_tester`, `operator`), and the config maps it to a backend (`codex`, `claude`, `copilot`, `cursor`, `antigravity`), a model and an effort. All five backends run the same kind of job: a jobId, state under `.handoff/jobs/`, the monitor loop, a bounded `resume` fix round, worktrees, and receipt evidence.
- **Never move a task to another identity to change its vendor or meter.** That is a config change (`/agent-handoff config`, or `handoff-config.py set`), and you say so. A user who asks for such a swap is asking for that config change: name it and offer it, and do not re-route the task. If the configured identity is wrong for the work, say so and ask.
- **Identity values live only in config**: `.handoff/config.toml` (project) or `~/.config/handoff/config.toml` (global). Never copy them into a prompt or a doc.
- **The user owns the work.** Accept nothing on a worker's summary. Review the diff, or the evidence of a row that is not a diff, against the acceptance criteria first.
- **No paid call without the user's recorded cap, and never print a key's value.** See `references/artefact-rows.md`.
- **Report only what was measured.** Never state a token saving, an avoided cost, or context kept out of the driver.
- **Say what is missing.** When a worker CLI the config needs is not installed, say so up front.

## Access boundaries

This skill runs local worker CLIs and Git, reads repository files and job logs,
and writes delegated changes and `.handoff/` evidence. Setup previews configuration
writes before applying them. Helpers read the process environment to locate tools
and isolate child authentication. Copilot model discovery sends the existing GitHub
bearer only to the fixed Copilot catalogue endpoint and refuses redirects. Worker
CLIs use their configured providers, and a worker inherits the driving session's
environment apart from the Claude variables a claude worker runs without. Local viewers bind to loopback and require a
per-run token. These capabilities describe the workflow; they grant no additional
host permissions. Read-only jobs retain their backend restrictions, and an
unrestricted permission mode requires explicit configuration.

## Where the scripts are

The helper scripts are in this skill's own directory, the one that holds this file, not in the target repo. Resolve it once as `$HANDOFF_DIR`. You know it from where this file was loaded; otherwise probe `~/.claude/skills/agent-handoff` or the local clone. `$REPO` is the target repository. Every script runs from any directory, and the repo-dependent ones take `--repo`.

## Commands

| The user says | Read |
|---|---|
| `/agent-handoff`, "agent handoff", "hand this off to codex", "delegate this", any request to split work with a worker CLI | `references/delegation-flow.md` |
| `/agent-handoff resume`, "resume agent handoff" | `references/delegation-flow.md`; it continues from `.handoff/goal.md` |
| "full protocol", "PR delivery", "goal mode" | `references/delegation-flow.md`, then `references/pr-delivery.md` |
| `/agent-handoff config` (also `setup`, `init`), or a flow needs an identity that has no config | `references/setup-wizard.md` |
| `/agent-handoff tryout` | `references/identity-tryout.md` |
| `/agent-handoff transcript`, "show me the transcript" or "conversation history" of a job | `references/session-evidence.md` |
| `/agent-handoff cost-receipt`, "cost receipt", what a run cost or consumed | `references/session-evidence.md` |
| `/agent-handoff visualise` (or `visualize`), "visualise the session", "show me the session timeline" | `references/session-evidence.md` |
| `/agent-handoff migrate`, "migrate handoff evidence" | `references/session-evidence.md` |

Any other request that triggered this skill is a delegation request: read `references/delegation-flow.md`.

A bare request for "the receipt" means the Handoff Session Receipt (`references/session-receipt.md`). Only "cost receipt", or a question about what a run consumed, means the cost receipt.

## Further references

The files above name these at the step that needs them. Do not read one ahead of that step.

| File | Read it when |
|---|---|
| `references/backends/<backend>.md` (`codex`, `claude`, `copilot`, `cursor`, `antigravity`) | a job will run on that backend, or you set an identity to it: its preflight check, permission modes and failure signs |
| `references/goal-file-template.md` | writing `.handoff/goal.md` |
| `references/packet-templates.md` | writing a prompt for a delegated job or a spec review |
| `references/arbitration.md` | a review gate runs out of rounds, the user says "arbitrate" or asks for a second opinion, you judge a call disputable, or the goal file has a row in `arbitration` or `rejected` |
| `references/e2e-acceptance.md` | the change alters user-observable behaviour, or the run has e2e rows |
| `references/artefact-rows.md` | a row's result is not a text diff, one of its commands outlasts a monitor tick, or it calls a paid service |
| `references/session-receipt.md` | closing a run, a setup or a tryout, or answering what the receipt holds |
| `references/wrap-up-memory.md` | wrapping up a run |
| `references/skill-change-gate.md` | improving this workflow, or applying it to a substantial task |
