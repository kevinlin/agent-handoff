# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this repo is

`partner-skill` (搭子.skill) is an **Agent Skill**, not an application. There is nothing to build or serve. The deliverable is a directory that gets copied into `~/.codex/skills/`, `~/.claude/skills/`, or `~/.agents/skills/` by `install.sh`, plus Python/Bash helper scripts the skill invokes at runtime.

Consequence: prose files (`SKILL.md`, `README.md`, `README.en.md`, `references/*.md`) are **product surface**, gated by CI the same way code is. Editing them can break `check-skill-repo.sh` or `check-readme-parity.py`.

## Commands

```bash
python3 -m unittest discover -s tests          # unit suite
python3 -m unittest tests.test_partner_config  # one module
python3 -m unittest tests.test_partner_config.ClassName.test_name  # one test

bash scripts/check-skill-repo.sh .             # publish-readiness gate (required files, triggers, secret scan)
python3 scripts/check-readme-parity.py         # zh/en README structural alignment
python3 scripts/run-test-prompts.py            # static validation of test-prompts.json
bash scripts/sandbox-matrix.sh                 # dual-host setup matrix in scratch HOME/XDG dirs
bash install.sh --target all --dry-run         # install without writing
```

Ledger reproducibility check (CI asserts no diff):

```bash
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

Receipt roundtrip:

```bash
python3 scripts/make-receipt.py --phase review --claude-session x --reused yes \
  --new-claude-p 0 --codex-passes 1 --checks "ci" --monitoring-level none \
  --host claude_code --scope project --config-source project --roles-used '[]' \
  | python3 scripts/validate-receipt.py -
```

`.github/workflows/checks.yml` runs all of the above; run it locally before pushing.

## Architecture

**Core + adapter.** `SKILL.md` is the host-agnostic core. It decides the *host* (the runtime that actually loaded it — self-identification wins; the `host=` line in `.install-meta` is only a tiebreaker) and then loads one adapter:

- `references/codex-driven.md` — Direction A: Codex orchestrates, Claude Code plans/polishes/reviews in one reused session.
- `references/claude-driven.md` — Direction B: Claude plans, delegates to Codex background jobs, monitors, full-reviews. Five phases plus an arbiter protocol.

Both directions terminate in the same **Partner Session Receipt** (schema v2, `docs/receipt-schema.json`). Receipt fields must be generated, never hand-typed: `make-receipt.py` fills `monitoring_level` from the probe and refuses invalid output; `validate-receipt.py` re-checks written receipts.

**Identity layer.** Three identities — `deep_reasoner`, `fast_worker`, `arbiter` — each a `backend + model + effort` triple, freely mixed across vendors. Values live *only* in `.partner/config.toml` (project) or `~/.config/partner/config.toml` (global), never in prompts or docs. Resolution order: session override → project → global → built-in defaults, merged per field. Schema in `docs/config-schema.md`.

- `scripts/partner-config.py` — pure read/write/resolve engine. Each host owns only its `hosts.<host>` namespace and must preserve the other host's bytes, `[routing]`, comments, and unknown sections.
- `scripts/partner-setup.py` — the plan/preview/apply/smoke/rollback/uninstall engine. All writes are atomic with backups.
- `scripts/partner-setup-ui.py` — localhost-only single-page wizard; delegates every preview and write to `partner-setup.py`. Model and effort lists come from probing the local CLIs (`model/list`, `claude --help`), never from hardcoded guesses.

**Runtime primitives.**

- `scripts/delegate-codex.sh` — wraps `codex exec --json` as durable background jobs under `<repo>/.partner/jobs/<jobId>/`. Subcommands: `submit|status|result|resume|cancel|list`.
- `scripts/run-claude-plan.py` — bounded Claude planning: ≤24k-char evidence packet, no tools/subagents, wall and idle timeouts, process-group kill, CLI-enforced budget. Failure preserves a checkpoint and recovery command; it never silently substitutes a different model.
- `scripts/check-claude-cli.sh` — probes CLI internals and prints `MONITORING_LEVEL=full|degraded|none`. Never claim a monitoring signal the probe says is unavailable.
- `scripts/session-snapshot.sh diff` — computes `new_claude_p_sessions` from transcript evidence.
- `scripts/partner_runtime.py` — `clean_claude_env()`, stripping `ANTHROPIC_*`/`CLAUDE_CODE_*` before spawning a child CLI so it authenticates like a fresh terminal. Any new script that spawns a CLI should use it.

## Conventions that CI enforces

- **Bilingual READMEs move together.** `check-readme-parity.py` pins the heading sequence, a marker list, and the File Map entry order in both files. Adding a top-level doc usually means updating: the doc, both READMEs' File Maps, `EXPECTED_HEADINGS`/`FILE_MAP_ENTRIES`, and `check-skill-repo.sh`'s required-file list.
- **Version strings appear in several places** — `SKILL.md` frontmatter, README badges, `CHANGELOG.md`, `docs/releases/`. Bump them together.
- Risky command text (`git reset --hard`, `rm -rf`, `--force`) in docs is scanned. `check-skill-repo.sh` warns; `run-test-prompts.py` requires such text to sit in a `must_not` list. Use the `# risk-ok:` marker for genuine detection patterns.
- Don't fabricate token savings. The cost numbers are a workload *pressure model* (`docs/showcase-cost-model.md`), not billing telemetry. Report verifiable behavior instead: session reused, no fresh `claude -p`, bounded handoff used, checks passed.

## Changing the workflow itself

`references/darwin-ratchet.md` is the gate: change one dimension at a time (planning, implementation, polish, review, monitoring, permissions, reporting), validate against test prompts or a real miniloop, and keep the change only when repo evidence improves. Don't let one agent be both sole maker and sole judge on high-risk changes.
