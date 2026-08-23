# Changelog

## v3.0.0 (2026-08-22)

### Breaking: renamed to agent-handoff

The skill was called `partner-skill` ("Partner") through v2.x. Every name below changed at once, and there is no compatibility shim — a machine with the old install re-runs setup.

- refactor!: skill name `partner-skill` → `agent-handoff`; install destination `~/.claude/skills/agent-handoff`. Triggers are now the skill name or the word handoff: `/agent-handoff` (bare), `/agent-handoff config` (also `setup`/`init`), `/agent-handoff tryout`, `/agent-handoff resume`, plus "config agent handoff", "setup agent handoff", "tryout agent handoff", "hand this off to codex". The verb is `config`, never `configure` — `install.sh --configure`/`--configure-cli` became `--config`/`--config-cli`
- refactor!: per-repo state directory `.partner/` → `.handoff/` (config, goal, jobs, receipts, backups, generated manifest), and global config `~/.config/partner/config.toml` → `~/.config/handoff/config.toml`. Nothing reads the old locations
- refactor!: `scripts/partner-{config,setup,setup-ui}.py` and `scripts/partner_runtime.py` → `scripts/handoff-{config,setup,setup-ui}.py` and `scripts/handoff_runtime.py`; tests renamed to match
- refactor!: `$PARTNER_DIR` → `$HANDOFF_DIR`, `PARTNER_CODEX_BIN` → `HANDOFF_CODEX_BIN`, `PARTNER_AGENT_CMD` → `HANDOFF_AGENT_CMD`; generated subagents `partner-deep-reasoner`/`-fast-worker`/`-arbiter` → `handoff-*`; managed routing markers now say `HANDOFF MANAGED ROUTING`
- refactor!: receipt header `[Partner session receipt]` → `[Handoff session receipt]` and schema `$id` `partner.receipt.v3` → `handoff.receipt.v3`. Field set is unchanged, so `receipt_schema_version` stays `3`; a receipt written under the old header no longer validates
- refactor!: showcase ledger schema `handoff.showcase_cost_ledger.v1` with mode key `handoff` and comparisons `handoff_vs_pure_claude` / `handoff_vs_codex_only`
- docs: release notes under `docs/releases/`, the three archived receipts under `examples/`, and older changelog entries keep the Partner name — that was the name when they were written

### Breaking: one flow, one host

- refactor!: remove the Codex-driven flow (Direction A). Partner is now a single flow — Claude Code plans and splits, Codex executes delegated background jobs, Claude full-reviews before accepting. `references/codex-driven.md`, `monitoring.md`, `scenarios.md`, `failure-playbook.md`, and `bounded-planning.md` are gone, along with `make-handoff.sh`, `check-claude-cli.sh`, `session-snapshot.sh`, and `run-claude-plan.py`
- refactor!: collapse the dual-host configuration layer. `--host` is gone from `partner-config.py`, `partner-setup.py`, `partner-setup-ui.py`, and `delegate-codex.sh`; `install.sh` installs only to `~/.claude/skills/partner-skill` and no longer writes a `host=` marker. Existing `.partner/config.toml` files keep working — the `[hosts.claude_code.identities.*]` shape is unchanged, and a stale `hosts.codex.*` block round-trips byte-for-byte
- refactor!: Partner Session Receipt schema v3 drops `direction`, `monitoring_level`, `claude_session_reused`, `new_claude_p_sessions`, `codex_passes`, and `host`. `roles_used[].host` is unchanged — it records the CLI that executed a role. `examples/session-receipt.md` now carries a real v3 receipt and is CI-validated again; the two v2.0.x conversation-cost receipts stay as schema v2 archives and are not
- refactor!: remove the bundled `idea-king` companion skill. Its adversarial gate survives inline as Phase 1 of `references/claude-driven.md`: the same three questions, answered in writing before anything is delegated
- fix: `read_legacy_v1` now reads v1 role values from any host namespace, so a schema-v1 config written by a Codex-side install still seeds the setup wizard
- test: 32 behavior prompts down to 17; `sandbox-matrix.sh` and `test_run_claude_plan.py` removed

## v2.0.1 (2026-07-29)

### Bounded Claude planning

- feat: add `scripts/run-claude-plan.py`, a config-only Claude planner with a validated 24,000-character evidence packet delivered over stdin, safe mode, zero tools/subagents, wall and no-event timeouts, and a Claude-CLI-enforced API budget
- feat: persist bounded sanitized event envelopes, visible-text checkpoints, exact configured/observed model/session, runner and packet hashes, cost metadata, atomically created plans, and same-session recovery instructions under `.partner/`
- fix: distinguish authentication, upstream idle, local idle timeout, wall timeout, budget stop, tool-use violation, and protocol error instead of treating every Fable failure as login trouble
- fix: invalidate verification when an identity changes; refuse unverified/non-Claude roles, malformed or secret-like packets, silent truncation, incomplete/conversational plan output, nonzero nominal success, observed model/session mismatches, output overwrite races, and all automatic model fallback
- fix: bound partial-line, visible-output, event-log, process-group termination, and expanded secret-redaction paths so watchdogs cannot be defeated by byte trickles, inherited pipes, or common bearer/password/GitLab/AWS/Google credentials
- refactor: share the nested Claude environment cleanup between setup smoke and the bounded planner
- test: add mocked stream/budget/timeout/config boundary coverage, repository behavior prompts, and the full unit suite to GitHub Actions
- docs: define who builds the bounded packet, its exact contract, the honest budget telemetry boundary, and the fixed recovery path

## v2.0.0 (2026-07-29)

### Setup, identity routing, and safety

- fix: strip host-injected Claude Code environment markers before spawning the real Claude CLI, preserving first-party OAuth instead of triggering a nested-session/login failure
- fix: restore delegate-codex.sh auto --skip-git-repo-check for non-git --repo targets, lost in the v1.5 rewrite (jobs against non-git dirs FAILed immediately)
- fix: align the model matrix rows and synchronize the matrix/settings header grid without removing the routing motion
- feat: rebuild the setup UI as a kinetic local Agent routing console with live role mapping, purposeful motion, responsive layout, and reduced-motion support
- feat: localhost single-page setup UI with a taste-skill guided decision rail, concrete model matrix, exact diff preview, preview-bound confirmation, and smoke test without repeated chat questions
- config: balanced preset fast_worker now uses the detected Codex model with high reasoning effort
- feat: identity matrix — three cross-vendor identities (deep_reasoner / fast_worker / arbiter), each with its own backend/model/effort; schema v2 with fail-closed v1 migration (`5a1f3d7`, `52ad950`, `da269ce`)
- feat: arbiter blind-solve protocol + "Partner, tryout" first-run tryout; goal.md task table drops owner in favor of identity (`f329daf`)
- feat: idea-king adds an Assignment section to Partner work-split reviews (`18dd247`)
- fix: wire per-task role decision into the split flow; clarify owner vs role (`983457f`, `493d561`, superseded by the identity matrix)

### Protocol, receipts, and verification

- test: make the dual-host sandbox matrix self-contained with deterministic fake CLI binaries so CI does not depend on installed Claude or Codex tools
- test: dual-host CI sandbox matrix — install order, idempotence, fail-closed (`6333e8c`)
- fix: redirect codex exec stdin from /dev/null to prevent hung background jobs (`0d673cc`)
- docs: README bilingual rewrite — setup wizard, host self-ID, receipt v2, opt-in full protocol (`54ce055`)
- feat: goal-sync.py hash-checked goal.md read/write, no silent lost update (`94349df`)
- test: test-prompts.json +4 goal-to-pr case incl. ordinary-pr-no-trigger negative (`d99b630`)
- feat: Plan→Goal→PR→Verification protocol, references/goal-to-pr.md (`f5b9232`)
- feat: extend Partner Session Receipt with host/scope/config_source/roles_used, schema v2 (`5246d45`)
- feat: activate Sub Agent three-level routing, partner-* > user agent > generic Task (`3d5e077`)
- test: test-prompts.json +9 case — 4 setup, 4 host-adapter, 1 idea-king (`c6020f5`)
- feat: partner-setup.py wizard engine + references/setup.md, "Partner, configure" first-run setup (`5e3744b`)
- feat: install.sh --configure forwards to the terminal setup wizard (`9e95036`)
- feat: idea-king absorbs Occam/Murphy/Coase laws, ported from installed copy (`51a6389`)
- feat: idea-king clarify-to-95% pre-verdict protocol, headless degrades to Open Questions (`d86ae57`)
- refactor: split SKILL.md into Partner Core + references/codex-driven.md with host detection (`4cc5ccd`)
- feat: partner-config.py TOML-subset config engine (schema v1) with tests and docs (`760d938`)
- feat: delegate-codex.sh --role injection from partner-config, with --dry-run and tests (`63b6807`)
- feat: install.sh writes host= marker into .install-meta (`d9eab44`)
- test: run-test-prompts.py supports should_trigger:false negative cases (`b21cbe3`)
- fix: remove deprecated --enable web_search_cached from delegate-codex.sh (`30999a2`)

## v1.4.2 (2026-07-05)

- feat: idea-king absorbs official adversarial-review, grilling, and packet hygiene (`a1ddbc9`)
- docs: add idea-king adversarial-review showcase GIF to both READMEs (`4ab807c`)
- docs: add Red Skill submission copy for Partner (`9ebc3e7`)

## v1.4.1 (2026-07-04)

- feat: execution-channel routing + evidence-backed prompting principles (`ab679e8`)
- fix: harden bidirectional delegation utilities (`f5539bb`)

## v1.4.0 (2026-07-03)

- feat: bidirectional Partner + idea-king + frontier prompting & memory protocol (`a9b5dde`)

## v1.3.0 (2026-07-02)

- Release partner skill v1.3.0 (`e7266e8`)
- feat: showcase redesign with green palette, GSAP animation, and GIF (`e67966c`)
- fix: replace curly quotes with straight ASCII quotes in img tags (`764f4bb`)

## Earlier (2026-06-24 – 2026-07-01)

- Showcase and release polish: reproducible cost ledger, README parity release gate, README language split (`ad19e6c`, `efed0d8`, `8ebb5cf`, `3418b60`, `a266e94`, `b984911`, `c35955b`, `2eac3c1`)
- Public readiness: Partner session receipt, same-session Claude strategy, release preparation (`258779f`, `f87a17e`, `0ae1d81`, `7fa24da`)
- Origin: initial Claude Codex relay skill, renamed to Partner (`1ba62e6`, `5979fab`)
