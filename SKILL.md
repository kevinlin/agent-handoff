---
name: agent-handoff
version: 3.6.0
description: |
  Agent Handoff — delegation workflow where Claude Code drives and a configured worker CLI executes. Claude plans and splits the work, attacks its own split before acting on it, delegates each task as a durable background job on that identity's configured backend (Codex or a second Claude Code), monitors them, and full-reviews the result before accepting. Use on "agent handoff" or "/agent-handoff" (the bare skill name), "/agent-handoff resume" or "resume agent handoff" (resume from .handoff/), "/agent-handoff config" (also "setup" or "init"), "config agent handoff", "setup agent handoff" (first-run setup wizard), "/agent-handoff tryout" or "tryout agent handoff" (identity tryout report), "/agent-handoff transcript" or "show me the transcript" / "conversation history" of a job (renders a delegated job's log.jsonl as HTML), "hand this off to codex", "delegate this to codex", "let codex do it", "run codex in the background", "Claude plans, Codex implements", or any request to split coding work between Claude Code and Codex to save quota. Not for ordinary code review; do not trigger on the bare English word "handoff" in unrelated contexts.
---

# Agent Handoff

> Claude Code decides, Codex executes — every handoff leaves a receipt.

## Overview

Use this skill to run a driver-and-worker coding workflow: Claude Code is the driver that plans, splits, and quality-gates; a worker CLI executes the delegated work as a background job. Delegating buys two things — execution on a subscription meter instead of the driver's, and a driver context window kept clear of execution history it will never need again.

The flow: Claude refines the request into a plan, decides which tasks the driver should not carry itself, delegates those via `bash "$HANDOFF_DIR/scripts/delegate-codex.sh"` background jobs, monitors them with a loop, and reviews the complete diff before accepting it. Read `references/claude-driven.md` and follow its five phases; the shared prompting rules live in `references/fable5-principles.md` and the wrap-up memory rules in `references/memory-protocol.md`.

**The identity's configured `backend` decides which CLI runs a job — always.** `codex` runs `codex exec --json`; `claude` runs a second Claude Code as `claude --print --output-format stream-json`. Both are real background jobs with the same jobId, `.handoff/jobs/` state, monitoring loop, bounded `resume` fix round, worktree lifecycle, and receipt evidence. Never move a task to a different identity to change its vendor or meter: that is a config change (`/agent-handoff config`), said out loud, not a routing decision made while delegating. If the configured identity is wrong for the work, say so and ask.

Handoff is not a delegation excuse. The user remains the owner, delegated work stays accountable to repository evidence, and nothing is accepted on a summary the driver has not verified against the diff.

The worker CLIs are what make the delegation primitives work. When one the config needs is missing, say so up front instead of pretending its jobs are available.

## Configuration

On `/agent-handoff config`, or when a Handoff flow needs an identity with no configuration yet, run the local setup UI in `references/setup.md` with `python3 "$HANDOFF_DIR/scripts/handoff-setup-ui.py" --repo <repo>`. Do not collect the matrix through repeated chat questions when a browser is available. The single page shows every backend, concrete model, and effort, then delegates every preview/write to `handoff-setup.py` (preview → atomic apply → automatic smoke test). It uses beginner-safe project defaults instead of asking scope/Git/routing questions in chat; the terminal engine remains available for explicit advanced overrides. Five identities, each carrying its own backend (which CLI executes the identity's delegated jobs: claude or codex), model, and effort, freely mixed across vendors. Three are always configured: deep_reasoner, fast_worker, and arbiter (the blind second solver for contentious calls). Two are an opt-in add-on written only when setup runs `--with-e2e`: e2e_specifier and e2e_verifier, which write and run acceptance tests for user-observable changes — see `references/e2e-gauntlet.md` for when to use them. A config carrying only the three core identities is complete. `deep_reasoner` also carries one responsibility toggle, `auto_review_spec` (setup: `--spec-review`, default off): with it on, `deep_reasoner` gives the Phase 1 plan one independent read before the plan reaches the user — once per run, read-only, findings the driver rules on. Full step in `references/claude-driven.md`, packet in `references/handoff-template.md`. Identity values live only in `.handoff/config.toml` (project) or `~/.config/handoff/config.toml` (global) — schema in `docs/config-schema.md`; never duplicate them into prompts or docs. On `/agent-handoff tryout`, run the identity tryout in `references/tryout.md`: each identity executes one micro-task and the report proves they are live on the configured models.

## Tool Location

The helper scripts referenced below live in this skill's install directory (the directory containing this SKILL.md), not in the target repo. Resolve it once as `$HANDOFF_DIR` — you know it from wherever this file was loaded; otherwise probe `~/.claude/skills/agent-handoff` or the local clone. All scripts accept running from any cwd; repo-dependent ones take `--repo`.

## Transcript

On `/agent-handoff transcript [<pid|jobId|folder>]`, or when the user asks to see
a job's transcript or conversation history, render it and open it:

```bash
python3 "$HANDOFF_DIR/scripts/render-transcript.py" [<selector>] --repo "$REPO"
```

The selector is optional; omitted or `last` means the newest job. A folder path,
an exact jobId, a pid, and a remembered label all resolve. When more than one job
matches — a label and its `-r2` resume round, for instance — the script lists the
candidates and exits without guessing; pass one of the printed names.

This renders and opens a page. It does not delegate anything and starts no job.

## Routing Rules

- Keep in the driving session: architecture, the split decision itself, cross-task integration, security- and correctness-critical paths, difficult product tradeoffs, and final acceptance.
- Delegate as a job: scaffolding, implementation, long-context code edits, tests, build fixes, wide read-only scans, batch migrations, doc generation.
- Pick the identity by the capability the work needs. Which CLI and meter that implies is the config's answer, not a second decision — and never a reason to swap identities mid-run.
- Never route a quality-critical step to a cheaper identity to save money, and never spend the driver's seat on mechanical work.

## Validation Gate

Use the Darwin-style ratchet in `references/darwin-ratchet.md` when improving this workflow or applying it to a substantial task:

- Change one workflow dimension at a time: planning, the split decision, delegation, monitoring, review, permissions, or reporting.
- Run test prompts or a real miniloop before calling an improvement better.
- Do not let the same agent be the only maker and only judge for high-risk changes.
- Keep the change only when repo evidence improves. If it regresses, use a reviewable revert, not `git reset --hard`.
- Stop when another prompt loop produces low signal or <1 point expected improvement.

## Monitoring

Delegated jobs are monitored from evidence, never from chat text. Phase 3 of `references/claude-driven.md` has the loop; the signals are the job's own `status`, the tail of its `log.jsonl`, its `stderr.log`, and repo evidence (`git status --short`, `git diff --stat`, plus the fastest relevant check). A job with no new JSONL events across two consecutive ticks, or a `FAILED` status, is an anomaly: cancel it, read the stderr, then resubmit with a corrected prompt or take the task back. Record the anomaly in the receipt.

## Output Contract

When reporting back to the user, include:

- Current phase: planning, delegated implementation, review, or final fix.
- Claude Code session id when one exists.
- Files changed and checks run.
- Review findings fixed or still open.
- Whether the work is ready to commit; do not commit by default.
- Any monitoring anomaly such as idle session, permission wait, empty review output, no diff, or failed check.
- A Handoff Session Receipt for non-trivial Claude Code workflows:

```text
[Handoff session receipt]
phase: <planning | codex implementation | delegated implementation | review | final fix>
claude_session: <sessionId or none>
duration: <99min 12sec>
codex_jobs: <0 | count>
codex_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
cc_jobs: <0 | count>
cc_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
checks: <commands run or not run>
anomalies: <none | job stalled | job failed | takeback | failed check | other>
scope: <project | global | n/a>
config_source: <session | project | global | default | n/a>
roles_used: <none | JSON array of {role, host, model, effort, verified}>
receipt_schema_version: 5
```

Generate the receipt with `python3 "$HANDOFF_DIR/scripts/make-receipt.py"` — it refuses to emit an invalid receipt, including one with no recorded start. `codex_jobs` and `cc_jobs` partition this run's `delegate-codex.sh` jobs by the backend that executed them, fix rounds counted in both; each job's `meta` `backend=` line under `<repo>/.handoff/jobs/` is the evidence, not recall. Use `phase: delegated implementation` when the run's delegated work was not all codex-backed. A written receipt can be re-checked any time with `validate-receipt.py` against `docs/receipt-schema.json`.

`duration` is wall clock from `<repo>/.handoff/session-start` to the receipt, so a run that sat waiting on a permission prompt carries that wait in the number. Stamp the marker once at preflight with `make-receipt.py --start --repo <repo>`; without it the tool refuses rather than accepting a remembered start time. `codex_job_durations` and `cc_job_durations` are measured the same way, from each job's `submitted_at` and `exit_code` under `<repo>/.handoff/jobs/`, split by that job's backend. Jobs left over from an earlier run are excluded; one still running reads `running`.

`claude_session` is the current session. `scope` and `config_source` come straight from `handoff-setup.py --status` or a `handoff-config.py resolve` call (`n/a` when the run touched no configured role). `roles_used` lists every role actually invoked this run, each entry's `verified` taken from the config's `verified` field, not guessed — an unconfigured or unverified role still gets an entry with `verified: false`, it is never omitted to make the receipt look cleaner. In `roles_used`, an entry's `host` is the CLI that executed that role, not the runtime that loaded this file. `roles_used` may carry `e2e_specifier` and `e2e_verifier` on runs that used them. `receipt_schema_version` is always `5`; a receipt with no `cc_jobs` and `cc_job_durations`, or one carrying `direction` or `monitoring_level`, predates this contract and will fail `validate-receipt.py`, which is the intended signal to regenerate it with the current `make-receipt.py`.

Do not fabricate token savings. When exact token telemetry is unavailable, report verifiable behavior instead: which tasks ran on which backend, how many jobs and fix rounds, that the full diff was reviewed against the acceptance criteria, and that the checks passed.
