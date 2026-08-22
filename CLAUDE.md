# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`partner-skill` (Partner) is an **Agent Skill**, not an application. There is nothing to build or serve. The deliverable is a directory that gets copied into `~/.claude/skills/partner-skill` by `install.sh`, plus Python/Bash helper scripts the skill invokes at runtime.

Consequence: prose files (`SKILL.md`, `README.md`, `references/*.md`) are **product surface**, gated by CI the same way code is. Editing them can break `check-skill-repo.sh`.

## Commands

```bash
python3 -m unittest discover -s tests          # unit suite
python3 -m unittest tests.test_partner_config  # one module
python3 -m unittest tests.test_partner_config.ClassName.test_name  # one test

bash scripts/check-skill-repo.sh .             # publish-readiness gate (required files, triggers, secret scan)
python3 scripts/english-only-scan.py           # fail on CJK in any tracked file
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
python3 scripts/make-receipt.py --phase review --claude-session x \
  --checks "ci" --codex-jobs 1 \
  --scope project --config-source project --roles-used '[]' \
  | python3 scripts/validate-receipt.py -
```

`.github/workflows/checks.yml` runs all of the above; run it locally before pushing.

## Architecture

**One flow.** `SKILL.md` is the core contract; `references/claude-driven.md` is the flow it loads — five phases (preflight, plan and split, delegate, monitor, full review, wrap up) plus an arbiter protocol. Claude Code drives; Codex executes delegated work on its own subscription.

The run terminates in a **Partner Session Receipt** (schema v3, `docs/receipt-schema.json`). Receipt fields must be generated, never hand-typed: `make-receipt.py` refuses invalid output; `validate-receipt.py` re-checks written receipts. In `roles_used`, an entry's `host` is the CLI that executed the role — unrelated to the skill's own host.

**Identity layer.** Three identities — `deep_reasoner`, `fast_worker`, `arbiter` — each a `backend + model + effort` triple, freely mixed across vendors. Values live *only* in `.partner/config.toml` (project) or `~/.config/partner/config.toml` (global), never in prompts or docs. Resolution order: session override → project → global → built-in defaults, merged per field. Schema in `docs/config-schema.md`.

- `scripts/partner-config.py` — pure read/write/resolve engine. It owns only `hosts.claude_code.identities.*` and must preserve `[routing]`, comments, and unknown sections (including stale `hosts.codex.*` blocks from dual-host-era configs) byte-for-byte. The `hosts.*` nesting is kept so existing configs keep loading.
- `scripts/partner-setup.py` — the plan/preview/apply/smoke/rollback/uninstall engine. All writes are atomic with backups.
- `scripts/partner-setup-ui.py` — localhost-only single-page wizard; delegates every preview and write to `partner-setup.py`. Model and effort lists come from probing the local CLIs (`model/list`, `claude --help`), never from hardcoded guesses.

**Runtime primitives.**

- `scripts/delegate-codex.sh` — wraps `codex exec --json` as durable background jobs under `<repo>/.partner/jobs/<jobId>/`. Subcommands: `submit|status|result|resume|cancel|list`. `--role` resolves backend/model/effort from config and fail-closes when the identity's backend is `claude`.
- `scripts/goal-sync.py` — hash-checked read/write for `.partner/goal.md`, so the Phase 3 monitor loop and the driver cannot silently clobber each other.
- `scripts/partner_runtime.py` — `clean_claude_env()`, stripping `ANTHROPIC_*`/`CLAUDE_CODE_*` before spawning a child CLI so it authenticates like a fresh terminal. Any new script that spawns a CLI should use it.

## Conventions that CI enforces

- **The repo is English-only.** `english-only-scan.py` (run from `check-skill-repo.sh`) fails on CJK in any tracked file, including trigger phrases and UI strings. Adding a top-level doc means updating: the doc, the README File Map, and `check-skill-repo.sh`'s required-file list.
- **Version strings appear in several places** — `SKILL.md` frontmatter, README badges, `CHANGELOG.md`, `docs/releases/`. Bump them together.
- Risky command text (`git reset --hard`, `rm -rf`, `--force`) in docs is scanned. `check-skill-repo.sh` warns; `run-test-prompts.py` requires such text to sit in a `must_not` list. Use the `# risk-ok:` marker for genuine detection patterns.
- Don't fabricate token savings. The cost numbers are a workload *pressure model* (`docs/showcase-cost-model.md`), not billing telemetry. Report verifiable behavior instead: which work ran on the Codex subscription, job and fix-round counts, the full diff reviewed against acceptance criteria, checks passed.

## Changing the workflow itself

`references/darwin-ratchet.md` is the gate: change one dimension at a time (planning, the split decision, delegation, monitoring, review, permissions, reporting), validate against test prompts or a real miniloop, and keep the change only when repo evidence improves. Don't let one agent be both sole maker and sole judge on high-risk changes.
