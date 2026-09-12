# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`agent-handoff` (Handoff) is an **Agent Skill**, not an application. There is nothing to build or serve. The deliverable is a directory that gets copied into `~/.claude/skills/agent-handoff` by `install.sh`, plus Python/Bash helper scripts the skill invokes at runtime.

Consequence: prose files (`SKILL.md`, `README.md`, `references/*.md`) are **product surface**, gated by CI the same way code is. Editing them can break `check-skill-repo.sh`.

## Commands

```bash
python3 -m unittest discover -s tests          # unit suite
python3 -m unittest tests.test_handoff_config  # one module
python3 -m unittest tests.test_handoff_config.ClassName.test_name  # one test

bash scripts/check-skill-repo.sh .             # publish-readiness gate (required files, triggers, secret scan)
python3 scripts/run-test-prompts.py            # static validation of test-prompts.json
bash install.sh --dry-run                      # install without writing
```

Ledger reproducibility check (CI asserts no diff):

```bash
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

Receipt roundtrip:

```bash
python3 scripts/make-receipt.py --start --repo .          # stamps .handoff/session-start
python3 scripts/make-receipt.py --repo . --phase review --claude-session x \
  --checks "ci" --codex-jobs 0 --cc-jobs 0 --copilot-jobs 0 \
  --scope project --config-source project --roles-used '[]' --no-save \
  | python3 scripts/validate-receipt.py -
```

E2E verdict check:

```bash
python3 scripts/validate-verdict.py .handoff/e2e/<jobId>/verdict.json
```

`.github/workflows/checks.yml` runs all of the above; run it locally before pushing.

## Architecture

**One flow.** `SKILL.md` is the core contract; `references/claude-driven.md` is the flow it loads — five phases (preflight, plan and split, delegate, monitor, full review, wrap up) plus an arbiter protocol. Claude Code drives; a worker CLI executes delegated work on the identity's configured backend — `codex`, a second `claude`, or `copilot` — each on its own meter.

The run terminates in a **Handoff Session Receipt** (schema v6, `docs/receipt-schema.json`). It carries three job counts, one per backend — `codex_jobs`, `cc_jobs`, `copilot_jobs` — with matching `*_job_durations`; a copilot job is never folded into another count. Receipt fields must be generated, never hand-typed: `make-receipt.py` refuses invalid output; `validate-receipt.py` re-checks written receipts. In `roles_used`, an entry's `host` is the CLI that executed the role — unrelated to the skill's own host.

**Identity layer.** Five identities, each a `backend + model + effort` triple, freely mixed across three vendors (`claude`, `codex`, `copilot`). Three core — `deep_reasoner`, `fast_worker`, `arbiter` — plus two optional, `e2e_specifier` and `e2e_verifier`, written only when setup runs `--with-e2e`. A config carrying just the core three is complete. Values live *only* in `.handoff/config.toml` (project) or `~/.config/handoff/config.toml` (global), never in prompts or docs. Resolution order: session override → project → global → built-in defaults, merged per field. Schema in `docs/specs/design_agent-identities-and-config.md`.

- `scripts/handoff-config.py` — pure read/write/resolve engine. It owns `hosts.claude_code.identities.*` and `[review]` and must preserve `[routing]`, comments, and unknown sections (including stale `hosts.codex.*` blocks from dual-host-era configs) byte-for-byte. The `hosts.*` nesting is kept so existing configs keep loading.
- `scripts/handoff-setup.py` — the plan/preview/apply/smoke/rollback/uninstall engine. All writes are atomic with backups.
- `scripts/handoff-setup-ui.py` — localhost-only single-page wizard; delegates every preview and write to `handoff-setup.py`. Model and effort lists come from probing the local CLIs (`model/list`, `claude --help`), never from hardcoded guesses. Copilot has no catalog at its CLI surface, so its models are read from the account's entitlement API with the bearer `gh auth token` returns; each model carries the efforts it accepts. No catalogue means no Copilot model offered, never a typed fallback. `validate_copilot_pair` now runs on smoke only.

**Runtime primitives.**

- `scripts/delegate-codex.sh` — wraps `codex exec --json`, `claude --print --output-format stream-json`, and `copilot -p --output-format json` as durable background jobs under `<repo>/.handoff/jobs/<jobId>/`. The name is historical. Subcommands: `submit|status|result|resume|cancel|cleanup|list`. `--role` resolves backend/model/effort from config; a per-job `--backend` contradicting a named role is refused. On copilot, the binary is identified by its `--version` string because AWS Copilot CLI shares the name, the session id is assigned at submit, and `--read-only` means `--mode plan`, never `--allow-all-tools`. `submit --worktree` runs the job in a worktree pinned to an immutable base SHA; `--repo` is always the main repo, never a worktree.
- `scripts/goal-sync.py` — hash-checked read/write for `.handoff/goal.md`, so the Phase 3 monitor loop and the driver cannot silently clobber each other.
- `scripts/handoff_runtime.py` — `clean_claude_env()`, stripping `ANTHROPIC_*`/`CLAUDE_CODE_*` before spawning a child CLI so it authenticates like a fresh terminal. Any new script that spawns a `claude` should use it. The copilot branch deliberately does not: Copilot authenticates through `gh auth` and `~/.copilot` and reads none of those variables.

## Conventions that CI enforces

- Adding a top-level doc means updating both the doc and `check-skill-repo.sh`'s required-file list.
- **Version strings appear in several places** — `SKILL.md` frontmatter, README badges, `CHANGELOG.md`, `docs/releases/`. Bump them together.
- Risky command text (`git reset --hard`, `rm -rf`, `--force`) in docs is scanned. `check-skill-repo.sh` warns; `run-test-prompts.py` requires such text to sit in a `must_not` list. Use the `# risk-ok:` marker for genuine detection patterns.
- Don't fabricate token savings. The cost numbers are a workload *pressure model*, not billing telemetry. Report verifiable behavior instead: which work ran on the Codex subscription, job and fix-round counts, the full diff reviewed against acceptance criteria, checks passed.

## Changing the workflow itself

`references/darwin-ratchet.md` is the gate: change one dimension at a time (planning, the split decision, delegation, monitoring, review, permissions, reporting), validate against test prompts or a real miniloop, and keep the change only when repo evidence improves. Don't let one agent be both sole maker and sole judge on high-risk changes.

## Agent output

DO NOT send optional commentary. Answer only what was asked — no preamble, no unsolicited suggestions, no closing remarks.
