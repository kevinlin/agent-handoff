# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Solo developers running Claude Code on their own machine who want their driver quota and context window to go further by delegating execution to a worker CLI (Codex, a second Claude Code, GitHub Copilot, Cursor, or Antigravity). They open the UI pages locally: the config wizard on first run or when they swap identities, and the session view right after a run, to check what ran where, what failed, and what it cost.

## Product Purpose

Agent Handoff is an Agent Skill, not an app. Claude Code plans, splits, and signs off; a worker CLI executes delegated tasks as durable background jobs; the driver reads the full diff before accepting it. Every run that delegates at least one job ends in a generated Handoff Session Receipt; a run that delegates nothing gets none. The UI surfaces exist to make setup safe and previewable, and to make a finished run inspectable from files on disk.

Success: a user configures identities without hand-editing TOML, and after a run can see each job, its transcript, and its measured cost, without trusting any summary they can't trace back to a receipt or job log.

## Positioning

The driver never accepts delegated work on a worker's summary. It reviews the complete diff (for a build, render, or other row that is not a text diff, the evidence the worker left in its place), and the run leaves a schema-validated receipt with per-backend job counts. The cost receipt reports token counters per meter, plus CLI-reported cost for Claude-backed jobs, as measured. It never reprices tokens, computes a saving, or claims context kept out of the driver. The showcase ledger is an illustrative workload model, not billing.

## Operating Context

- Two pages are served, only on `127.0.0.1`, behind a per-run token and a `Host` check. Each runs until Ctrl-C: the config wizard (`/agent-handoff config`) and the session view (`/agent-handoff visualise`).
- `/agent-handoff transcript` and `/agent-handoff cost-receipt` write self-contained HTML into the session folder and open it from disk. The session view frames those same pages instead of reimplementing them.
- Config wizard: `scripts/handoff-setup-ui.py` with `assets/setup-ui.html`, project scope only. It delegates every preview and write to `scripts/handoff-setup.py`. Six steps: detect, preset (`balanced`, `quality`, `cost`, `custom`), tune, preview, apply, smoke. Tune sets backend, model, effort, and permission per identity, a checkbox for each optional identity, and the two review round caps. Each worker CLI has a tile showing its state (missing, update, found, listed, no models) and an install or update popover. Model and effort lists come from probing local CLIs and the Copilot entitlement API, never hardcoded. A reload shows what is installed.
- Session view: `scripts/handoff-session-ui.py` with `assets/session-view.html`. Five tabs: Overview, Transcript, Cost receipt, Session facts, Denials and anomalies. Overview is a hand-authored timeline SVG with one lane per delegated job plus a driver lane, clickable hotspots over the lanes, and a job table beside it. It degrades in steps: a diagram with no lane map shows the image, a banner, and the job table; no diagram shows the job table and a copyable generation prompt, and only when started with `--allow-missing-diagram`, otherwise the script refuses to start.
- Evidence lives under `.handoff/sessions/<date>_<session_id>/` (receipt, cost receipt, transcripts, timeline diagram). Job state lives under `.handoff/jobs/<jobId>/`.
- Other rendered pages: `assets/transcript-viewer.html`, `assets/cost-receipt.html`, and `docs/user-guide/agent-handoff.html`.
- Design focus, in order: session view, then config wizard.

## Capabilities and Constraints

- Five identities, each a backend + model + effort triple plus a permission mode (`default` or `allow-all`): core `deep_reasoner`, `fast_worker`, `arbiter`; optional `e2e_tester`, `operator`. The driver is not an identity.
- Five backends: `codex`, `claude`, `copilot`, `cursor`, `antigravity`. Cursor and Antigravity effort is always `model`, and on both the permission mode changes nothing; the wizard says so.
- Two review gates (plan and delegated diff) with round caps set under `[review]`. A dispute that runs out of rounds goes to the `arbiter`, and every ruling is recorded in `.handoff/goal.md` and in the receipt's anomalies.
- Config writes are atomic with backups, and the wizard shows the exact diff before confirming. A configured model missing from the account's catalogue stays in the form, marked, and is never silently replaced.
- Pages are self-contained. They load no CDN scripts or web fonts, and the session view's framed pages cannot fetch images from outside.
- Prose (`SKILL.md`, `README.md`, `references/*.md`) is product surface gated by CI (`scripts/check-skill-repo.sh`).

## Brand Commitments

- Own identity, not Zühlke-branded.
- Name: Agent Handoff (short form Handoff). Tagline: "Claude Code decides, a worker CLI executes — every handoff leaves a receipt."
- Logo: `assets/logo.png`. Version badge colour `#ef6f4f`.
- Voice: plain, factual, evidence-first. No decorative emoji.

## Evidence on Hand

- Real session receipt replay: `assets/session-visualise-demo.gif` (the run that added Copilot as a third backend: 283 minutes, nine jobs).
- Config demo: `assets/config-switch-demo.gif` / `.mp4`. Promo: `assets/promo.mp4`.
- Cost receipt sample: `assets/v3.6.1-conversation-cost-receipt.png` and `examples/v3.6.1-conversation-cost-receipt.{md,html}`; showcase ledger `examples/showcase-cost-ledger.json`.
- Absent: no user testimonials, adoption numbers, or billing-grade savings figures. Do not fabricate them.

## Product Principles

1. Show only what can be traced to a file on disk; label anything that isn't (the timeline's hand-authored narrative). Attribute nothing by assumption: the working tree's `goal.md` appears as a run's only when its jobIds match the receipt.
2. Preview before write: every config change is a visible diff first.
3. Never hide failure. Failed jobs, denials, and anomalies get first-class space. A denied tool call does not change a job's exit code, so denials are listed across every job in the run.
4. Local and private by default: served pages bind loopback only and load nothing third-party, and captured worker output never becomes markup (agent prose is the only text rendered as markdown). The wizard's probe makes a few read-only requests when it opens: the npm registry and Cursor's install script for the newest CLI version, with no credentials, and Copilot's entitlement API with the user's own `gh` token, which never reaches the page.
5. Probe, don't guess: options shown come from the user's actual CLIs and accounts. When a probe fails, the page offers no guessed model and names the fix.
6. Name a gap, never zero it. `unknown`, `running`, `unavailable`, and `not reported` are real values, and a measured zero prints `0`. A partial total reads `≥ <subtotal> (n of m jobs measured)`.

## Accessibility & Inclusion

All four pages respect `prefers-reduced-motion` and use `light-dark()` tokens that follow the system colour scheme. The config wizard and the transcript viewer also offer a System/Light/Dark switch; the session view and cost receipt follow the system only. Session view tabs are reachable by keyboard with focus rings, hover styles sit behind `(hover: hover)`, and tables and long output scroll inside their own container instead of pushing the page sideways. Keep all of it.
