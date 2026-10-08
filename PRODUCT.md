# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Solo developers running Claude Code on their own machine who want their driver quota and context window to go further by delegating execution to a worker CLI (Codex, a second Claude Code, GitHub Copilot, or Cursor). They open the UI pages locally: the config wizard on first run or when they swap identities, and the session view right after a run, to check what ran where, what failed, and what it cost.

## Product Purpose

Agent Handoff is an Agent Skill, not an app. Claude Code plans, splits, and signs off; a worker CLI executes delegated tasks as durable background jobs; the driver reads the full diff before accepting it. Every run ends in a generated Handoff Session Receipt. The UI surfaces exist to make setup safe and previewable, and to make a finished run inspectable from files on disk.

Success: a user configures identities without hand-editing TOML, and after a run can see each job, its transcript, and its measured cost, without trusting any summary they can't trace back to a receipt or job log.

## Positioning

The driver never accepts delegated work on a worker's summary. It reviews the complete diff, and the run leaves a schema-validated receipt with per-backend job counts. Cost figures are a workload pressure model plus token counters from job logs, never invented savings.

## Operating Context

- Pages are served only on `127.0.0.1`, opened from a Claude Code session (`/agent-handoff config`, `/agent-handoff visualise`, `/agent-handoff transcript`, `/agent-handoff cost-receipt`).
- Config wizard: `scripts/handoff-setup-ui.py`. It delegates every preview and write to `scripts/handoff-setup.py`. Model/effort lists come from probing local CLIs and the Copilot entitlement API, never hardcoded.
- Session view: `scripts/handoff-session-ui.py` with `assets/session-view.html`. It shows a timeline with one lane per job, job transcripts, the cost receipt, receipt facts, and denials/anomalies.
- Other rendered pages: `assets/transcript-viewer.html`, `assets/cost-receipt.html`, `docs/user-guide/agent-handoff.html`, and the promo (`assets/promo.html`).
- Design focus, in order: session view, then config wizard.

## Capabilities and Constraints

- Five identities, each a backend + model + effort triple: core `deep_reasoner`, `fast_worker`, `arbiter`; optional `e2e_specifier`, `e2e_verifier`.
- Four backends: `codex`, `claude`, `copilot`, `cursor`. Cursor effort is always `model`.
- Config writes are atomic with backups, and the wizard shows the exact diff before confirming.
- Pages are self-contained. They load no CDN scripts or web fonts.
- Prose (`SKILL.md`, `README.md`, `references/*.md`) is product surface gated by CI (`scripts/check-skill-repo.sh`).

## Brand Commitments

- Own identity, not Zühlke-branded.
- Name: Agent Handoff (short form Handoff). Tagline: "Claude Code decides, a worker CLI executes — every handoff leaves a receipt."
- Logo: `assets/logo.png`. Version badge colour `#ef6f4f`.
- Voice: plain, factual, evidence-first. No decorative emoji.

## Evidence on Hand

- Real session receipt replay: `assets/session-visualise-demo.gif` (the run that added Copilot as a backend: 283 minutes, nine jobs).
- Config demo: `assets/config-switch-demo.gif` / `.mp4`. Promo: `assets/promo.mp4`.
- Cost receipt sample: `assets/v3.6.1-conversation-cost-receipt.png`; showcase ledger `examples/showcase-cost-ledger.json`.
- Absent: no user testimonials, adoption numbers, or billing-grade savings figures. Do not fabricate them.

## Product Principles

1. Show only what can be traced to a file on disk; label anything that isn't (e.g. hand-written timeline labels).
2. Preview before write: every config change is a visible diff first.
3. Never hide failure. Failed jobs, denials, and anomalies get first-class space.
4. Local and private by default: loopback only, no third-party requests.
5. Probe, don't guess: options shown come from the user's actual CLIs and accounts.

## Accessibility & Inclusion

Existing pages respect `prefers-reduced-motion`; the config wizard follows the system colour scheme and offers a light/dark toggle. Keep both.
