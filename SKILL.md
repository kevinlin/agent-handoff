---
name: agent-handoff
version: 3.1.0
description: |
  Agent Handoff — cost-split workflow where Claude Code drives and Codex executes. Claude plans and splits the work, attacks its own split before acting on it, delegates quota-pressure tasks to Codex background jobs, monitors them, and full-reviews the result before accepting. Use on "agent handoff" or "/agent-handoff" (the bare skill name), "/agent-handoff resume" or "resume agent handoff" (resume from .handoff/), "/agent-handoff config" (also "setup" or "init"), "config agent handoff", "setup agent handoff" (first-run setup wizard), "/agent-handoff tryout" or "tryout agent handoff" (identity tryout report), "hand this off to codex", "delegate this to codex", "let codex do it", "run codex in the background", "Claude plans, Codex implements", or any request to split coding work between Claude Code and Codex to save quota. Not for ordinary code review; do not trigger on the bare English word "handoff" in unrelated contexts.
---

# Agent Handoff

> Claude Code decides, Codex executes — every handoff leaves a receipt.

## Overview

Use this skill to run a two-agent coding workflow: Claude Code is the driver that plans, splits, and quality-gates; Codex executes the delegated work on its own subscription. Keep Claude Code usage focused because it may be billed through API.

The flow: Claude refines the request into a plan, decides which tasks genuinely do not need the expensive tier, delegates those to Codex via `bash "$HANDOFF_DIR/scripts/delegate-codex.sh"` background jobs, monitors them with a loop, and reviews the complete diff before accepting it. Read `references/claude-driven.md` and follow its five phases; the shared prompting rules live in `references/fable5-principles.md` and the wrap-up memory rules in `references/memory-protocol.md`.

Handoff is not a delegation excuse. The user remains the owner, delegated work stays accountable to repository evidence, and nothing is accepted on a summary the driver has not verified against the diff.

The Codex CLI is what makes the delegation primitives work. When it is missing, say so up front instead of pretending the background jobs are available.

## Configuration

On `/agent-handoff config`, or when a Handoff flow needs an identity with no configuration yet, run the local setup UI in `references/setup.md` with `python3 "$HANDOFF_DIR/scripts/handoff-setup-ui.py" --repo <repo>`. Do not collect the matrix through repeated chat questions when a browser is available. The single page shows every backend, concrete model, and effort, then delegates every preview/write to `handoff-setup.py` (preview → atomic apply → automatic smoke test). It uses beginner-safe project defaults instead of asking scope/Git/routing questions in chat; the terminal engine remains available for explicit advanced overrides. Three identities — deep_reasoner, fast_worker, and arbiter (the blind second solver for contentious calls) — each carry their own backend (which CLI executes: claude or codex), model, and effort, freely mixed across vendors. Their values live only in `.handoff/config.toml` (project) or `~/.config/handoff/config.toml` (global) — schema in `docs/config-schema.md`; never duplicate them into prompts or docs. On `/agent-handoff tryout`, run the identity tryout in `references/tryout.md`: each identity executes one micro-task and the report proves they are live on the configured models.

## Tool Location

The helper scripts referenced below live in this skill's install directory (the directory containing this SKILL.md), not in the target repo. Resolve it once as `$HANDOFF_DIR` — you know it from wherever this file was loaded; otherwise probe `~/.claude/skills/agent-handoff` or the local clone. All scripts accept running from any cwd; repo-dependent ones take `--repo`.

## Routing Rules

- Keep in Claude Code: architecture, the split decision itself, cross-task integration, security- and correctness-critical paths, difficult product tradeoffs, and final acceptance.
- Route to Codex: scaffolding, implementation, long-context code edits, tests, build fixes, wide read-only scans, batch migrations, doc generation.
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

- Current phase: planning, Codex implementation, review, or final fix.
- Claude Code session id when one exists.
- Files changed and checks run.
- Review findings fixed or still open.
- Whether the work is ready to commit; do not commit by default.
- Any monitoring anomaly such as idle session, permission wait, empty review output, no diff, or failed check.
- A Handoff Session Receipt for non-trivial Claude Code workflows:

```text
[Handoff session receipt]
phase: <planning | codex implementation | review | final fix>
claude_session: <sessionId or none>
duration: <99min 12sec>
codex_jobs: <0 | count>
codex_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
checks: <commands run or not run>
anomalies: <none | job stalled | job failed | takeback | failed check | other>
scope: <project | global | n/a>
config_source: <session | project | global | default | n/a>
roles_used: <none | JSON array of {role, host, model, effort, verified}>
receipt_schema_version: 4
```

Generate the receipt with `python3 "$HANDOFF_DIR/scripts/make-receipt.py"` — it refuses to emit an invalid receipt, including one with no recorded start. Set `codex_jobs` to the number of `delegate-codex.sh` jobs this run, counting fix rounds; the job directories under `<repo>/.handoff/jobs/` are the evidence, not recall. A written receipt can be re-checked any time with `validate-receipt.py` against `docs/receipt-schema.json`.

`duration` is wall clock from `<repo>/.handoff/session-start` to the receipt, so a run that sat waiting on a permission prompt carries that wait in the number. Stamp the marker once at preflight with `make-receipt.py --start --repo <repo>`; without it the tool refuses rather than accepting a remembered start time. `codex_job_durations` is measured the same way, from each job's `submitted_at` and `exit_code` under `<repo>/.handoff/jobs/`. Jobs left over from an earlier run are excluded; one still running reads `running`.

`claude_session` is the current session. `scope` and `config_source` come straight from `handoff-setup.py --status` or a `handoff-config.py resolve` call (`n/a` when the run touched no configured role). `roles_used` lists every role actually invoked this run, each entry's `verified` taken from the config's `verified` field, not guessed — an unconfigured or unverified role still gets an entry with `verified: false`, it is never omitted to make the receipt look cleaner. In `roles_used`, an entry's `host` is the CLI that executed that role, not the runtime that loaded this file. `receipt_schema_version` is always `4`; a receipt with no `duration` and `codex_job_durations`, or one carrying `direction` or `monitoring_level`, predates this contract and will fail `validate-receipt.py`, which is the intended signal to regenerate it with the current `make-receipt.py`.

Do not fabricate token savings. When exact token telemetry is unavailable, report verifiable behavior instead: which tasks ran on the Codex subscription, how many jobs and fix rounds, that the full diff was reviewed against the acceptance criteria, and that the checks passed.
