---
name: agent-handoff
version: 3.7.2
description: |
  Agent Handoff — delegation workflow where Claude Code drives and a configured worker CLI executes. Claude plans and splits the work, attacks its own split before acting on it, delegates each task as a durable background job on that identity's configured backend (Codex, a second Claude Code, or GitHub Copilot), monitors them, and full-reviews the result before accepting. Use on "agent handoff" or "/agent-handoff" (the bare skill name), "/agent-handoff resume" or "resume agent handoff" (resume from .handoff/), "/agent-handoff config" (also "setup" or "init"), "config agent handoff", "setup agent handoff" (first-run setup wizard), "/agent-handoff tryout" or "tryout agent handoff" (identity tryout report), "/agent-handoff transcript" or "show me the transcript" / "conversation history" of a job (renders a delegated job's log.jsonl as HTML), "/agent-handoff cost-receipt" or "cost receipt" (renders a session receipt and its job state as a measured cost report), "/agent-handoff visualise" or "visualize", "visualise the session" or "show me the session timeline" (opens a loopback session review page), "hand this off to codex", "delegate this to codex", "let codex do it", "run codex in the background", "Claude plans, Codex implements", or any request to split coding work between Claude Code and a worker CLI to save quota. Not for ordinary code review; do not trigger on the bare English word "handoff" in unrelated contexts.
---

# Agent Handoff

> Claude Code decides, a worker CLI executes — every handoff leaves a receipt.

## Overview

Use this skill to run a driver-and-worker coding workflow: Claude Code is the driver that plans, splits, and quality-gates; a worker CLI executes the delegated work as a background job. Delegating buys two things — execution on a subscription meter instead of the driver's, and a driver context window kept clear of execution history it will never need again.

The flow: Claude refines the request into a plan, decides which tasks the driver should not carry itself, delegates those via `bash "$HANDOFF_DIR/scripts/delegate-codex.sh"` background jobs, monitors them with a loop, and reviews the complete diff before accepting it. Read `references/claude-driven.md` and follow its five phases; the shared prompting rules live in `references/fable5-principles.md` and the wrap-up memory rules in `references/memory-protocol.md`.

**The identity's configured `backend` decides which CLI runs a job — always.** `codex` runs `codex exec --json`; `claude` runs a second Claude Code as `claude --print --output-format stream-json`; `copilot` runs `copilot -p --output-format json`. All three are real background jobs with the same jobId, `.handoff/jobs/` state, monitoring loop, bounded `resume` fix round, worktree lifecycle, and receipt evidence. Never move a task to a different identity to change its vendor or meter: that is a config change (`/agent-handoff config`), said out loud, not a routing decision made while delegating. If the configured identity is wrong for the work, say so and ask.

Handoff is not a delegation excuse. The user remains the owner, delegated work stays accountable to repository evidence, and nothing is accepted on a summary the driver has not verified against the diff.

The worker CLIs are what make the delegation primitives work. When one the config needs is missing, say so up front instead of pretending its jobs are available.

## Configuration

On `/agent-handoff config`, or when a Handoff flow needs an identity with no configuration yet, run the local setup UI in `references/setup.md` with `python3 "$HANDOFF_DIR/scripts/handoff-setup-ui.py" --repo <repo>`. Do not collect the matrix through repeated chat questions when a browser is available. The single page shows every backend, concrete model, effort, and permission_mode (default or allow-all), then delegates every preview/write to `handoff-setup.py` (preview → atomic apply → automatic smoke test). It uses beginner-safe project defaults instead of asking scope/Git/routing questions in chat; the terminal engine remains available for explicit advanced overrides. Five identities, each carrying its own backend (which CLI executes the identity's delegated jobs: claude, codex, or copilot), model, effort, and permission_mode (default or allow-all), freely mixed across vendors. Both setup UIs expose permission_mode; absence resolves to default and read-only wins over it. Three are always configured: deep_reasoner, fast_worker, and arbiter (the blind second solver for contentious calls, and the judge a review gate escalates to). Two are an opt-in add-on written only when setup runs `--with-e2e`: e2e_specifier and e2e_verifier, which write and run acceptance tests for user-observable changes — see `references/e2e-gauntlet.md` for when to use them. A config carrying only the three core identities is complete. `deep_reasoner` also reviews every plan before it reaches the user: read-only, findings tagged blocking or advisory. Both review gates carry a round cap in the config's `[review]` section (`spec_max_rounds`, default 1; `implementation_max_rounds`, default 3; setup: `--spec-max-rounds`, `--implementation-max-rounds`), and when a gate runs out of rounds without agreement the driver escalates to `arbiter` for a binding ruling. Full step in `references/claude-driven.md`, packets in `references/handoff-template.md`. Identity values live only in `.handoff/config.toml` (project) or `~/.config/handoff/config.toml` (global) — schema in `docs/config-schema.md`; never duplicate them into prompts or docs. On `/agent-handoff tryout`, run the identity tryout in `references/tryout.md`: each identity executes one micro-task and the report proves they are live on the configured models.

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

## Cost Receipt

On `/agent-handoff cost-receipt [<receipt-file>]`, or when the user asks what a
handoff run cost or consumed, render it and open it:

```bash
python3 "$HANDOFF_DIR/scripts/render-cost-receipt.py" [<receipt-file>] --repo "$REPO"
```

The selector is optional; omitted or `last` means the newest saved receipt under
`.handoff/receipts/`. It writes a markdown and an HTML page to
`.handoff/cost-receipts/`.

A bare request for "the receipt" is the Handoff Session Receipt above, not this.
Only "cost receipt", or an explicit question about what the run consumed, routes
here.

Every number is measured: codex jobs carry token counters and no cost figure,
because that work runs on a subscription; claude jobs carry the CLI's own
`total_cost_usd`, quoted unrounded and labelled CLI-reported; copilot jobs carry
token counters plus premium requests and nano-AIU, which are AI credits and
never a currency figure. The summary figures overlap by design and are never
added. Do not report a saving, an avoided cost, or context kept out of the
driver — no counter establishes any of them. This renders a page; it delegates nothing and starts no job.

## Session Visualisation

On `/agent-handoff visualise [<receipt-file>]`, `visualize`, "visualise the session",
or "show me the session timeline", open the session review page:

```bash
python3 "$HANDOFF_DIR/scripts/handoff-session-ui.py" [<receipt-file>] --repo "$REPO"
```

Omitted means the newest saved receipt under `.handoff/receipts/`. The server
binds to `127.0.0.1` with a per-run access token; `--port` selects a port and
`--no-open` prints the URL. Keep the terminal open; Ctrl-C stops the server.
This reads a run and starts no delegated job.

**Both assets exist before the browser opens.** The cost receipt is rendered
eagerly by the script. The diagram needs a model, so the script cannot make one
and refuses to open without it:

- **Exit 3** — no timeline diagram for this receipt. The script prints the target
  path and the exact generation prompt. Generate the SVG with `baoyu-diagram`
  (fall back to `baoyu-image-gen` when it is present instead, or author the SVG
  directly when neither is), save it at the printed path, then run the command
  again. Do this without asking: it writes one gitignored artifact under
  `.handoff/` and starts no delegated job.
- **Declares no lane map** — the diagram predates the lane contract. Exit 0, the
  page opens with the image and job table, and the script prints the state and
  the generation prompt on stdout. **Ask** the user before spending anything,
  and say the page is already open and usable without hotspots. Offer the cheap
  fix first: **annotate the existing SVG in place** — read its tick `x`
  positions and lane band `y` bounds out of the file and add `viewBox`,
  `data-axis` and `data-lanes` to the root, keeping the drawing. Redraw from the
  printed prompt only when the file has no readable geometry or the user asks
  for a new diagram. Either way, run the command again afterwards and check the
  banner reconciles. This is the opposite of exit 3, where nothing opens and you
  generate unasked.
- **Exit 2** — bad receipt, bad selector, or a cost receipt that cannot render.
  Read the message; do not retry unchanged.
- `--allow-missing-diagram` opens on the job-table rung, for when generation is
  unavailable or the user wants the page as it stands.

The overview frames the run with a hand-authored SVG, labelled as a narrative
illustration whose prose is unverified. Transcript and cost-receipt tabs reuse
the existing pages as the measured record. Session facts associate `goal.md`
only by an indexed jobId in its Tasks table; an unmatched file is disclosed as
the current goal. Denials retain missing-data states and copilot call-id joins.
A Git-ignore banner warns when transcript output is not ignored.

The generated diagram goes at
`.handoff/receipts/diagram/<receipt-stem>_handoff-session-timeline.svg`. Draw it
light-themed with a 12px minimum font size, clock labels in the local timezone
with that zone named in the subtitle, and one sub-timeline per identity forked
from the driver lane where the driver submitted the job and rejoining where it
read the result. The SVG root must carry a positive-width/height `viewBox`, JSON `data-axis`
segments (`t0`, `t1`, `x0`, `x1`) at tick positions, and JSON `data-lanes`
(`job`, `y0`, `y1`, `t0`, `t1`). Use offset-aware timestamps, finite coordinates,
ordered non-overlapping axis segments, and non-overlapping lane bands. Include
every indexed job once; match declared windows to `meta.submitted_at` and
`exit_code` mtime within 60 seconds. An optional `lane: "driver"` has only a band,
no job or window. Never invent a running job's end.

An invalid lane map keeps the image and job table. Reconciliation
drops only contradicted hotspots; a running lane reads `unverifiable (job running)`.
Use **Show hotspots** to check alignment. The SVG is always an image, never DOM.
Only cached transcript and cost-receipt renderings are written, under `.handoff/`.
Receipt schema stays 6. See `docs/specs/design_session-visualisation.md`.

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

Delegated jobs are monitored from evidence, never from chat text. Phase 3 of `references/claude-driven.md` has the loop; the signals are the job's own `status`, the tail of its `log.jsonl`, its `stderr.log`, and repo evidence (`git status --short`, `git diff --stat`, plus the fastest relevant check). A job with no new JSONL events across two consecutive ticks, or a `FAILED` status, is an anomaly: cancel it, read `stderr.log` and the tail of `log.jsonl` — a copilot API failure can leave stderr empty and report itself only as a `session.error` event in the log — then resubmit with a corrected prompt or take the task back. Record the anomaly in the receipt.

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
copilot_jobs: <0 | count>
copilot_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
checks: <commands run or not run>
anomalies: <none | job stalled | job failed | takeback | failed check | arbitration | other>
scope: <project | global | n/a>
config_source: <session | project | global | default | n/a>
roles_used: <none | JSON array of {role, host, model, effort, verified}>
receipt_schema_version: 6
```

Generate the receipt with `python3 "$HANDOFF_DIR/scripts/make-receipt.py"` — it refuses to emit an invalid receipt, including one with no recorded start. `codex_jobs`, `cc_jobs`, and `copilot_jobs` partition this run's `delegate-codex.sh` jobs three ways by the backend that executed them, fix rounds counted in each; each job's `meta` `backend=` line under `<repo>/.handoff/jobs/` is the evidence, not recall. A copilot job is never folded into either of the other two counts. Use `phase: delegated implementation` when the run's delegated work was not all codex-backed. A written receipt can be re-checked any time with `validate-receipt.py` against `docs/receipt-schema.json`.

Each escalation ruling goes into `anomalies` as `arbitration: <task> approve|reject|no verdict (<jobId>)`, approve included; all entries share the one line, joined with `; `.

`duration` is wall clock from `<repo>/.handoff/session-start` to the receipt, so a run that sat waiting on a permission prompt carries that wait in the number. Stamp the marker once at preflight with `make-receipt.py --start --repo <repo>`; without it the tool refuses rather than accepting a remembered start time. `codex_job_durations`, `cc_job_durations`, and `copilot_job_durations` are measured the same way, from each job's `submitted_at` and `exit_code` under `<repo>/.handoff/jobs/`, split by that job's backend. Jobs left over from an earlier run are excluded; one still running reads `running`.

`claude_session` is the current session. `scope` and `config_source` come straight from `handoff-setup.py --status` or a `handoff-config.py resolve` call (`n/a` when the run touched no configured role). `roles_used` lists every role actually invoked this run, each entry's `verified` taken from the config's `verified` field, not guessed — an unconfigured or unverified role still gets an entry with `verified: false`, it is never omitted to make the receipt look cleaner. In `roles_used`, an entry's `host` is the CLI that executed that role, not the runtime that loaded this file. `roles_used` may carry `e2e_specifier` and `e2e_verifier` on runs that used them. `receipt_schema_version` is always `6`; a receipt with no `copilot_jobs` and `copilot_job_durations`, or one carrying `direction` or `monitoring_level`, predates this contract and will fail `validate-receipt.py`, which is the intended signal to regenerate it with the current `make-receipt.py`.

Do not fabricate token savings. When exact token telemetry is unavailable, report verifiable behavior instead: which tasks ran on which backend, how many jobs and fix rounds, that the full diff was reviewed against the acceptance criteria, and that the checks passed.
