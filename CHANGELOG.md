# Changelog

## v3.5.0 (2026-09-08)

### Breaking: a claude-backed identity is a real delegated job

- feat: `delegate-codex.sh` dispatches on the identity's configured `backend` instead of fail-closing on `claude`. A claude-backed row now becomes a background job with the same jobId, `.handoff/jobs/` state, monitoring loop, fix-round `resume`, worktree lifecycle, and receipt evidence a codex-backed row already had. The name stays: one script, one job shape, one code path — which is what makes parity real rather than asserted. The script wraps `claude --print --output-format stream-json --verbose --permission-prompts none --permission-mode <acceptEdits|plan> --effort <e>`, and `result` parses the Claude event stream alongside the Codex one
- fix: the real defect was a flow that re-decided the vendor per run. In a live run the driver hit a claude-backed `fast_worker`, was refused, and re-routed the work to `deep_reasoner` purely because that identity was Codex-backed — the split decision's capability judgment thrown away to buy a cheaper meter, with the swap hidden in a jobId. The mechanism was only half of it: `references/fable5-principles.md` had ranked the Claude subagent last and framed delegation as a cost move, which is the reasoning the driver quoted back
- feat: a second reason to delegate is now stated everywhere the first one was. Handoff moves execution off the driver's meter **and** off its context window; a claude-backed job earns the second even where it moves no meter. The three-channel table becomes one delegation channel that runs on either backend per config, plus two in-process escape hatches, and the rule is written plainly: never swap an identity to move a job onto another vendor — change the config and say so
- feat: `submit --backend codex|claude` for role-less ad-hoc jobs. One that contradicts a named role is refused; that guard replaces the old backend-is-claude fail-close. Efforts are validated per CLI rather than against one shared enum: codex keeps `minimal`-`ultra`, claude takes `low`-`max`
- fix: `resume` reads the parent job's `backend` from its `meta` and lands on the same CLI, and carries the parent's `role` forward. A fix round used to lose its identity provenance, so a run whose work happened mostly in rework had thinner `roles_used` than a clean first pass
- fix: a generated claude `run.sh` strips `ANTHROPIC_*` and `CLAUDE_CODE_*` with Bash prefix expansion before exec, mirroring `handoff_runtime.clean_claude_env()`, so a delegated worker authenticates like a fresh terminal instead of inheriting the host's injected credentials. A first prototype used a `sed` alternation and failed silently on macOS — BSD sed has no `\|` in a basic regular expression
- feat: `tests/test_delegate_role.py` runs the whole worktree lifecycle against both backends from one base class: create, record `worktree`/`branch`/`base_commit`, immutable SHA pinning, invalid base refused before the job dir exists, worktree as cwd, idempotent `cleanup`, dirty-tree refusal, `resume` landing in the parent worktree. Monitoring parity (`status`/`result` returning the same shape) is asserted too, so the Phase 3 `/loop` claim is tested rather than assumed
- **breaking**: receipt schema v5 adds `cc_jobs` and `cc_job_durations`; `codex_jobs` and `codex_job_durations` narrow to codex-backed jobs. `make-receipt.py` partitions the job directories by each `meta`'s `backend=` line (absent means codex, for job dirs written before this change), and `phase` gains `delegated implementation` — `codex implementation` is a false phase name for a run whose work ran on Claude. An existing v4 receipt fails `validate-receipt.py`; that is the intended signal to regenerate it

`schema_version` stays `2`: no configuration change was needed, because `backend = "claude"` was always a valid value — it just had nowhere to go.

## v3.4.0 (2026-09-07)

### A second pair of eyes on the plan

- feat: `deep_reasoner` gains one responsibility toggle, `auto_review_spec`, written by `handoff-setup.py --spec-review` (default off). With it on, the last step of Phase 1 hands the plan to `deep_reasoner` on its own configured backend for one independent read, and the driver folds in the findings before the plan reaches the user. Until now the adversarial gate was the only check on a plan, and the driver ran it against itself — the agent that wrote the plan was the only agent that judged it
- feat: the review fires at most once per run. The goal file gains a `## Spec Review` block, and a non-empty block means the automatic review is spent: an adjusted plan, a thin review, and a resumed session all fail to re-trigger it, and another one takes an explicit request. The goal file is rewritten per run, so the marker resets by itself
- feat: `handoff-config.py set --spec-review` / `--no-spec-review`, `--override deep_reasoner.auto_review_spec=true`, and the field in `--status`. It is a responsibility switch rather than a routing value, so toggling it leaves `verified` and `verified_at` alone, and it is refused on any identity but `deep_reasoner` rather than silently ignored
- feat: the setup UI carries the toggle as one checkbox, seeded from the resolved config so re-running setup cannot silently clear it
- docs: `references/handoff-template.md` gains the Spec Review Packet — read-only, findings only, the spec inline. It is the one packet that narrows the template's write permission instead of widening it, which is the opposite direction from the e2e pair. The flow prose states plainly that this review is informed rather than blind: the plan under review is the driver's own answer, so the arbiter's contamination rule does not apply, and same-vendor config is noted the way the arbiter protocol notes it

`schema_version` stays `2` and the receipt schema stays `4`. Absent means off, so every existing config keeps its current behaviour, and an older engine ignores the field rather than refusing the file — `validate_config` fails closed on an unknown identity, not on an unknown field inside a known one.

## v3.3.0 (2026-09-07)

### The Codex effort dial reaches the top of the GPT-5.6 range

- fix: `max` and `ultra` are configurable Codex efforts. `codex model/list` reports both on the GPT-5.6 models, but the setup engine intersected that list against a five-value tuple, so the wizard silently dropped the top two levels and `delegate-codex.sh` refused a config that carried one — `ERROR: invalid --effort: max` on an identity the wizard itself could not have offered. Both values are verified against the live CLI rather than assumed: `model_reasoning_effort` accepts them, and an unknown level still fails closed with the API naming what it supports
- docs: `references/fable5-principles.md` says what the two new levels are for, and that `ultra` subdivides the job through automatic task delegation rather than only thinking longer

No change was needed for the newly released models themselves. Model names have never been hardcoded. Claude Code resolves `opus` and `fable` as rolling aliases; verified locally, `opus` and `claude-opus-5` smoke-pass and `fable` resolves to Fable 5.1. Codex models come from the account-aware `model/list`, so a model this account cannot list yet still routes end to end once it does.

## v3.2.0 (2026-08-24)

### Optional e2e acceptance roles

- feat: two optional identities, `e2e_specifier` and `e2e_verifier`, behind `handoff-setup.py --with-e2e` (default off). The specifier writes Gherkin scenarios with stable IDs plus repo-native executable tests; the verifier runs them and reports a verdict. A three-identity config stays complete: the smoke gate and the "identities are not configured" error compute over the core three, and `--status` lists all five with `<unset>` for the unconfigured ones
- feat: `delegate-codex.sh submit --worktree <branch> [--base <commit-ish>]` runs a job in its own Git worktree under `.handoff/worktrees/<jobId>`, pinned to a base resolved to an immutable SHA before the job directory exists. `resume` inherits the parent's worktree, and the new idempotent `cleanup <jobId>` removes it, refusing one that holds uncommitted changes
- feat: `scripts/validate-verdict.py` and `docs/verdict-schema.json`. A verdict is the input to a merge decision, so PASS is checked against the exit code, the scenario counts, an empty findings list, and the hash of the scenarios the driver reviewed, rather than trusted as written
- feat: the goal file's task table gains a `depends` column, so the `/loop` monitor can hold a verifier row until both the specifier and the implementation are done
- feat: `roles_used` accepts the two e2e roles. `receipt_schema_version` stays `4` and `$id` stays `handoff.receipt.v4` — widening the enum is additive, so every existing v4 receipt still validates
- docs: `references/e2e-gauntlet.md` carries the worktree protocol, both delegation packets, the frozen/repairable/forbidden split, the verdict contract, and the review-the-tests-first gate. It states two limits rather than implying otherwise: the Codex and Claude worktree paths are not equally hardened, and the hash lock covers `.feature` files only

## v3.1.0 (2026-08-23)

### Breaking: receipts record how long the run took

- feat: `make-receipt.py --start --repo <repo>` stamps `.handoff/session-start` at Phase 0 preflight, and the receipt measures wall clock against it, so a run that sat waiting on a permission prompt carries that wait in `duration`. With no marker and no explicit `--started-at`, the tool refuses to emit rather than accept a remembered start time
- feat: `codex_job_durations` breaks the run down per delegated job, read from each job's `meta` `submitted_at` and the mtime of its `exit_code` under `.handoff/jobs/`. Jobs left over from an earlier run are excluded, one still running reads `running`, and `delegate-codex.sh` is unchanged
- refactor!: receipt schema v3 → v4. `duration` and `codex_job_durations` are required and `$id` is now `handoff.receipt.v4`; a v3 receipt fails `validate-receipt.py`, which is the signal to regenerate it with the current `make-receipt.py`
- docs: the two timing lines in `examples/session-receipt.md` are backfilled placeholders: that session predates the marker, and the next real run replaces them

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
