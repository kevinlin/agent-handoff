# Changelog

## v3.8.1 (2026-09-12)

### Copilot models are picked, not typed

- feat: the setup wizard reads the models the authenticated account is entitled to and offers them in a dropdown, matching the Codex and Claude experience. Each model carries its accepted reasoning efforts. Opening the wizard costs no premium request.
- **breaking**: apply no longer validates Copilot model–effort pairs against the CLI before writing. Validation still runs on smoke. The three backends are now symmetric.
- feat: the wizard refuses an unrecognised model before building a preview. No catalogue means no Copilot model offered — never a typed fallback. A preserved Copilot identity stays selectable offline.

### Wrap up leaves the receipt on disk

- feat: wrap up writes the session receipt to disk by default; `--no-save` opts out. Previously whether a run persisted its receipt depended on an undocumented flag.
- fix: a claim that the receipt was re-validated after writing was dropped from docs — validation is one pass before the write.

## v3.8.0 (2026-09-11)

### Two review gates, one shape

- **breaking**: spec review runs on every plan. The previous opt-in toggle is retired; old configs still load and the toggle is removed on next write.
- feat: a `[review]` config section with two round caps — spec (default 1) and implementation (default 3). A round is one review pass; the original job counts as pass one. Configurable via CLI, setup engine, or the wizard.
- feat: resume enforces the round cap. The count is derived from the job chain, not from naming conventions.
- feat: at the cap without consensus, the full dispute goes to the arbiter, whose ruling binds. An approval can overrule the driver but never replaces an e2e PASS. A rejection is final. A failed arbiter job gets one resubmit.
- feat: spec review findings are tagged blocking or advisory; only a declined blocking finding escalates. A resumed session finishes an open review chain instead of misreading it as done.
- Every ruling is recorded in the goal file and in the receipt's anomalies line.

## v3.7.2 (2026-09-10)

- feat: add `/agent-handoff visualise` (also `visualize`) — a single loopback page with a narrative SVG overview, transcript, and cost-receipt in authenticated same-origin frames.
- A diagram with no lane map now reports that so the driver can offer to fix it in place.
- Validate lane maps and reconcile declared windows with measured job evidence; surface per-lane failures, axis gaps, and missing-diagram fallbacks.
- Gate the page on its assets: a missing timeline diagram exits with the target path and a generation prompt rather than opening an empty tab.
- The generation prompt now carries presentation requirements: light theme, 12 px minimum font, clock labels in local timezone, one sub-timeline per identity.
- Add receipt-associated goal facts, joined permission denials, measured anomalies, and a transcript Git-ignore banner.

## v3.7.1 (2026-09-10)

- fix: the transcript and cost-receipt renderers crashed when `--repo` was relative. Fixed and tested.
- fix: receipt generation now checks that declared job counts match measured durations, catching a silent mismatch that could omit a job from the cost report.
- feat: `--ended-at <ISO8601>` pins the receipt time so a past run's receipt can be regenerated without duration growing to today.
- feat: per-identity `permission_mode` (`default` or `allow-all`) across config, delegation, and setup. Claude default uses dontAsk with the worker tool set; Codex and Copilot defaults stay unchanged.
- Resume preserves parent read-only state and posture. Requested and effective mode are recorded in job metadata.

## v3.7.0 (2026-09-09)

### A third execution backend: GitHub Copilot CLI

- feat: `backend = "copilot"` is a fully supported execution backend with the same job lifecycle, monitoring, fix rounds, worktree support, and receipt evidence as Codex and Claude.
- feat: the copilot binary is identified by its `--version` output to avoid confusion with AWS Copilot CLI. An override env var is available.
- feat: a copilot job's session id is assigned at submit, so a job that dies mid-run can still be resumed.
- fix: `--read-only` now correctly uses `--mode plan` rather than combining it with `--allow-all-tools`, which silently failed — the agent reported success while executing nothing.
- feat: delegated copilot jobs run with `--allow-all-tools`. Denial events are counted, and any non-zero count marks the job as failed.
- feat: every generated copilot command blocks remote export and memory. No `--share`, `--yolo`, or `--allow-all` is ever generated.
- feat: the effort enum is Copilot's own (`none` through `max`). Rejection is surfaced, not silently downgraded. `model = "auto"` is refused for configured identities.
- feat: the setup wizard renders Copilot's model as a text field. Opening the wizard costs nothing; apply and smoke validate against the real CLI.
- **breaking**: receipt schema v6 adds `copilot_jobs` and `copilot_job_durations`. A v5 receipt fails validation — regenerate it.
- feat: the cost receipt reads Copilot's usage and reports premium requests and nano-AIU — never a currency figure. A rejected run writes zeroed usage; a killed one writes none.
- feat: the transcript viewer parses copilot logs. Ephemeral events are dropped; tool events fold by call id.

Config schema stays `2`.

## v3.6.1 (2026-09-09)

### Read what a finished run consumed

- feat: `/agent-handoff cost-receipt` reads a saved receipt and its job logs, then writes a markdown and HTML cost page. Omitting the selector picks the newest receipt.
- feat: every number is measured. Codex jobs contribute token counters (subscription, no cost figure); Claude jobs contribute the CLI's reported cost. No saving or avoided-cost figure appears.
- feat: a missing measurement prints `unknown`; a measured zero prints `0`.
- feat: the driver row is scoped to the run's interval. A session hosting two runs yields two rows.
- fix: a job with no exit code is FAILED, never RUNNING.

## v3.6.0 (2026-09-08)

### Read a delegated job instead of grepping its log

- feat: `/agent-handoff transcript` renders a job's log as a self-contained HTML page. The selector is optional; ambiguous matches list candidates instead of guessing.
- feat: the viewer reads both Codex and Claude log formats. A failed edit renders as an error; an unrecognised event keeps its source. Dropping a log file onto the page renders it with no script needed.
- fix: log content is treated as untrusted input. External links are restricted to safe schemes.

## v3.5.1 (2026-09-08)

### A delegated Claude worker can run its own checks

- fix: a Claude-backed job now runs with bypass permissions. The previous mode denied nearly everything — every shell command outside the explicit allowlist was refused, including the repo's own checks.
- feat: job status and result now report permission denials. A denied tool call used to leave the exit code unchanged, making a blocked check look like a passed one. Any non-zero denial count now marks the job as failed.
- feat: submit warns when a Claude worker starts with checks bypassed. An override env var restores prompting.
- fix: the permission mode override is validated before use, preventing shell injection.

## v3.5.0 (2026-09-08)

### Breaking: a claude-backed identity is a real delegated job

- feat: a Claude-backed identity now becomes a real background job with the same lifecycle, monitoring, fix rounds, worktree, and receipt evidence as Codex.
- fix: the flow used to re-decide the vendor per run, swapping a Claude identity's work onto Codex for a cheaper meter — discarding the split decision's capability judgment.
- feat: delegation moves execution off the driver's meter **and** off its context window. The rule: never swap an identity to change vendors — change the config.
- feat: ad-hoc submit accepts `--backend codex|claude`. One that contradicts a named role is refused. Efforts are validated per CLI.
- fix: resume preserves the parent job's backend and role across fix rounds.
- fix: a delegated Claude worker strips inherited credentials so it authenticates independently.
- **breaking**: receipt schema v5 adds `cc_jobs` and `cc_job_durations`. `codex_jobs` narrows to codex-only. A v4 receipt fails validation — regenerate it.

Config schema stays `2`.

## v3.4.0 (2026-09-07)

### A second pair of eyes on the plan

- feat: `deep_reasoner` gains a spec review toggle. When on, the plan is reviewed by deep_reasoner independently before reaching the user — the first time the plan gets a check from a different agent than its author.
- feat: the review fires at most once per run and resets automatically between runs.
- feat: configurable via CLI, override, and the setup UI. Refused on identities other than `deep_reasoner`.

Config and receipt schemas unchanged. Absent means off.

## v3.3.0 (2026-09-07)

### The Codex effort dial reaches the top of the GPT-5.6 range

- fix: `max` and `ultra` are now configurable Codex efforts. The wizard previously dropped them silently, and delegation refused configs that carried them.
- docs: `ultra` subdivides the job through automatic task delegation rather than only thinking longer.

No change needed for newly released models — model names have never been hardcoded.

## v3.2.0 (2026-08-24)

### Optional e2e acceptance roles

- feat: two optional identities, `e2e_specifier` and `e2e_verifier`, behind `--with-e2e` (default off). The specifier writes Gherkin scenarios and executable tests; the verifier runs them and reports a verdict. A three-identity config stays complete.
- feat: jobs can run in their own Git worktree, pinned to an immutable base SHA. Resume inherits the parent's worktree; cleanup refuses if uncommitted changes remain.
- feat: a verdict is checked against exit code, scenario counts, findings list, and a scenario hash — not trusted as written.
- feat: the task table gains a `depends` column so the monitor can hold a row until prerequisites are done.

## v3.1.0 (2026-08-23)

### Breaking: receipts record how long the run took

- feat: preflight stamps a start marker, and the receipt measures wall clock against it. Without the marker, emission is refused.
- feat: per-job durations break the run down by delegated job.
- refactor!: receipt schema v3 → v4. A v3 receipt fails validation — regenerate it.

## v3.0.0 (2026-08-22)

### Breaking: renamed to agent-handoff

The skill was called `partner-skill` through v2.x. Every name changed at once with no compatibility shim.

- refactor!: skill name `partner-skill` → `agent-handoff`. Triggers: `/agent-handoff`, `/agent-handoff config`, `/agent-handoff tryout`, `/agent-handoff resume`, plus natural-language variants.
- refactor!: per-repo state `.partner/` → `.handoff/`; global config `~/.config/partner/` → `~/.config/handoff/`. Nothing reads the old locations.
- refactor!: all scripts, modules, and env vars renamed from `partner-*`/`PARTNER_*` to `handoff-*`/`HANDOFF_*`.
- refactor!: receipt header and schema `$id` renamed. Field set unchanged; schema version stays `3`.
- docs: archived entries keep the Partner name — that was the name when written.

### Breaking: one flow, one host

- refactor!: the Codex-driven flow (Direction A) is removed. The skill is now a single flow: Claude Code plans and splits, workers execute delegated jobs, Claude full-reviews before accepting.
- refactor!: the dual-host configuration layer is collapsed. Existing configs keep loading; stale host blocks round-trip untouched.
- refactor!: receipt schema v3 drops `direction`, `monitoring_level`, `claude_session_reused`, and `host`.
- refactor!: the bundled `idea-king` companion skill is removed. Its adversarial gate survives inline in the flow.
- fix: v1 config from either host namespace now seeds the setup wizard.

## v2.0.1 (2026-07-29)

### Bounded Claude planning

- feat: config-only Claude planner with a validated evidence packet, safe mode, wall and no-event timeouts, and an enforced API budget.
- feat: persist event envelopes, checkpoints, model/session info, cost metadata, plans, and recovery instructions.
- fix: distinguish authentication, upstream idle, local idle, wall timeout, budget stop, tool-use violation, and protocol error.
- fix: refuse unverified roles, malformed packets, silent truncation, incomplete output, model mismatches, output overwrite races, and automatic model fallback.
- fix: bound partial-line, visible-output, event-log, and process-group termination paths; extend secret redaction.

## v2.0.0 (2026-07-29)

### Setup, identity routing, and safety

- fix: strip host-injected environment markers before spawning the Claude CLI, preserving first-party OAuth.
- fix: restore auto mode for non-git repo targets.
- feat: rebuild the setup UI as a kinetic local routing console with live role mapping, responsive layout, and reduced-motion support.
- feat: localhost setup UI with a guided decision rail, model matrix, diff preview, confirmation, and smoke test.
- feat: identity matrix — three cross-vendor identities (`deep_reasoner` / `fast_worker` / `arbiter`), each with backend/model/effort; schema v2 with fail-closed v1 migration.
- feat: arbiter blind-solve protocol + first-run tryout; task table uses identity instead of owner.

### Protocol, receipts, and verification

- feat: hash-checked goal read/write, preventing silent lost updates.
- feat: Plan→Goal→PR→Verification protocol.
- feat: receipt extended with host/scope/config_source/roles_used, schema v2.
- feat: Sub Agent three-level routing.
- feat: setup wizard engine + first-run setup.
- refactor: split SKILL.md into core + references with host detection.
- feat: TOML-subset config engine (schema v1).
- feat: delegation with role injection from config.

## v1.4.2 (2026-07-05)

- feat: idea-king absorbs adversarial-review, grilling, and packet hygiene.

## v1.4.1 (2026-07-04)

- feat: execution-channel routing + evidence-backed prompting principles.
- fix: harden bidirectional delegation utilities.

## v1.4.0 (2026-07-03)

- feat: bidirectional Partner + idea-king + frontier prompting & memory protocol.

## v1.3.0 (2026-07-02)

- feat: showcase redesign with green palette, GSAP animation, and GIF.
- fix: replace curly quotes with straight ASCII quotes in img tags.

## Earlier (2026-06-24 – 2026-07-01)

- Showcase and release polish: reproducible cost ledger, README parity release gate, README language split.
- Public readiness: Partner session receipt, same-session Claude strategy, release preparation.
- Origin: initial Claude Codex relay skill, renamed to Partner.
