# Rename `partner-skill` → `agent-handoff`

## Context

The repo is being shaved down to one thing: a Claude-Code-driven coding-agent handoff. The name `partner-skill` / "Partner" is a leftover from the two-direction era and no longer describes the product. `partner` currently appears in 42 tracked files — not only prose, but the install path, the runtime state directory, the global config path, five script filenames, three test filenames, four env vars, generated subagent names, the receipt header, two JSON schema ids, an HTTP header, and the CI gates that assert those strings.

Decisions taken:

- Brand: **Agent Handoff** in titles, badges, and first mention per document; **Handoff** in running prose.
- Identifiers: `handoff-*` / `HANDOFF_*`. Runtime dir `.handoff/`, global config `~/.config/handoff/config.toml`.
- History (`CHANGELOG.md` v2.x/v3.0.0 sections, `docs/releases/v2.0.*.md`, `examples/v2.0.*-conversation-cost-receipt.*`) keeps saying "Partner" — that was the name then. Rename notes fold into the existing v3.0.0 section; version stays **3.0.0**.
- **Clean break**: nothing reads `.partner/` or `~/.config/partner/`. A user with old state re-runs setup.
- Remote is already `kevinlin/agent-handoff`; install URLs point there. The fork-attribution line at the bottom of README keeps `LearnPrompt/partner-skill`.

## The rename map

Apply consistently; every row is mechanical unless noted.

| From | To |
|---|---|
| skill name `partner-skill` | `agent-handoff` |
| install dir `~/.claude/skills/partner-skill` | `~/.claude/skills/agent-handoff` |
| state dir `.partner/` | `.handoff/` (jobs, goal.md, config.toml, receipts, backups, `.generated-manifest`) |
| `${XDG_CONFIG_HOME:-~/.config}/partner/config.toml` | `.../handoff/config.toml` |
| `$PARTNER_DIR` | `$HANDOFF_DIR` |
| `PARTNER_CODEX_BIN`, `PARTNER_AGENT_CMD`, `PARTNER_SMOKE_OK` | `HANDOFF_CODEX_BIN`, `HANDOFF_AGENT_CMD`, `HANDOFF_SMOKE_OK` |
| subagents `partner-deep-reasoner` / `-fast-worker` / `-arbiter` | `handoff-deep-reasoner` / `-fast-worker` / `-arbiter` |
| `[Partner session receipt]` | `[Handoff session receipt]` |
| `partner.receipt.v3` (`$id`) | `handoff.receipt.v3` — `receipt_schema_version` stays `3`, no field change |
| `partner.showcase_cost_ledger.v1`; keys `partner`, `partner_vs_pure_claude`, `partner_vs_codex_only`, label `"Partner"` | `handoff.…v1`; `handoff`, `handoff_vs_pure_claude`, `handoff_vs_codex_only`, `"Handoff"` |
| `X-Partner-Token`, UA `PartnerSetupUI/1`, `partner-content-hash`, `BEGIN PARTNER MANAGED ROUTING`, `clientInfo.name "partner-setup"` | `X-Handoff-Token`, `HandoffSetupUI/1`, `handoff-content-hash`, `BEGIN HANDOFF MANAGED ROUTING`, `handoff-setup` |

### File renames (`git mv`, preserve history)

- `scripts/partner-config.py` → `scripts/handoff-config.py`
- `scripts/partner-setup.py` → `scripts/handoff-setup.py`
- `scripts/partner-setup-ui.py` → `scripts/handoff-setup-ui.py`
- `scripts/partner_runtime.py` → `scripts/handoff_runtime.py`
- `tests/test_partner_config.py` → `tests/test_handoff_config.py`
- `tests/test_partner_setup.py` → `tests/test_handoff_setup.py`
- `tests/test_partner_setup_ui.py` → `tests/test_handoff_setup_ui.py`

Python module aliases loaded via `importlib.util.spec_from_file_location` follow: `partner_config` → `handoff_config`, `partner_setup_ui_engine` → `handoff_setup_ui_engine`, and the `from partner_runtime import clean_claude_env` in `handoff-setup.py`. Load sites: [scripts/goal-sync.py:21-27](scripts/goal-sync.py#L21-L27), [scripts/partner-setup.py:25-34](scripts/partner-setup.py#L25-L34), [scripts/partner-setup-ui.py:29-30](scripts/partner-setup-ui.py#L29-L30), and the three test modules.

Note: `references/handoff-template.md` already exists (delegation packet templates) and keeps its filename; only its "Partner Packet Templates" / "Partner Delegation" / "Partner Goal Packet" headings change.

### Trigger words

`SKILL.md` frontmatter description and `test-prompts.json` prompts:

The word **`configure` is retired everywhere** — the verb is `config`. That covers trigger phrases, prose ("run `/agent-handoff config` first"), UI copy, the `V1_UPGRADE_MESSAGE`, the managed-block comment in `handoff-setup.py`, and the `install.sh` flags `--configure` / `--configure-cli` → `--config` / `--config-cli`.

| Old | New |
|---|---|
| "Partner skill" (bare) | `/agent-handoff`, "agent handoff", "agent handoff skill" |
| "Partner, configure" | `/agent-handoff config` \| `setup` \| `init`, "config agent handoff", "setup agent handoff" |
| "Partner, tryout" | `/agent-handoff tryout`, "tryout agent handoff" |
| "Partner, resume" | `/agent-handoff resume`, "resume agent handoff" |
| verb triggers | unchanged — "delegate this to codex", "let codex do it", "run codex in the background", "Claude plans, Codex implements"; add "hand this off to codex" |

Keep the existing negative guard, retargeted: do not trigger on the bare English word "handoff" in unrelated contexts.

## Files to change

**Contract / gates** (do these first — they define what the rest must satisfy):

- [scripts/check-skill-repo.sh](scripts/check-skill-repo.sh) — required-file list lines 53-56; `^name: agent-handoff$` (L110-113); bare-trigger grep `"agent handoff"` (L124-127); README identity grep → `# Agent Handoff` + the new slogan (L131-134); "Handoff Session Receipt" contract greps (L171-178).
- [.github/workflows/checks.yml](.github/workflows/checks.yml) — the `py_compile` script list.
- [.gitignore](.gitignore) — `.partner/` → `.handoff/`.

**Runtime scripts** — mechanical per the map: `handoff-config.py` (incl. `V1_UPGRADE_MESSAGE` trigger text, `project_config_path`, `global_config_path`), `handoff-setup.py` (markers, hash prefix, routing text, manifest/backup paths, smoke sentinel, generated agent filenames), `handoff-setup-ui.py` (UI copy, page title, brand mark, token header, UA), `install.sh` `--configure`/`--configure-cli` → `--config`/`--config-cli`, `delegate-codex.sh` (`JOBS_SUBDIR`, codex-bin env var, config script path, the backend=claude refusal message), `goal-sync.py`, `make-receipt.py`, `validate-receipt.py`, `run-test-prompts.py`, `showcase-cost-ledger.py`, `install.sh` (`DEST`, usage text, error message, closing hint → a new trigger phrase).

**Data / schema**: [docs/receipt-schema.json](docs/receipt-schema.json) (`$id`, title, description), [test-prompts.json](test-prompts.json) (prompts, expected_behavior, must_not, ids `bare-partner-trigger` → `bare-handoff-trigger`, `resume-from-partner-state` → `resume-from-handoff-state`), [examples/showcase-cost-ledger.json](examples/showcase-cost-ledger.json) — **regenerate, do not hand-edit**.

**Prose**: [SKILL.md](SKILL.md), [README.md](README.md) (title, slogan, badges, install URLs → `kevinlin/agent-handoff`, File Map, trigger block), [CLAUDE.md](CLAUDE.md), [docs/config-schema.md](docs/config-schema.md), the showcase cost model doc (since retired), all nine `references/*.md`, [examples/session-receipt.md](examples/session-receipt.md) (live example — regenerate under the new header), and a rename bullet set in the existing `## v3.0.0` section of [CHANGELOG.md](CHANGELOG.md).

**Left alone**: `docs/releases/v2.0.*.md`, `examples/v2.0.*-conversation-cost-receipt.{md,html}`, older CHANGELOG sections, the fork-attribution URL. Nothing in CI greps those for the brand string — confirm after the gate edits.

## Verification

```bash
# 1. No stray occurrences outside the history allowlist
git grep -inE 'partner' -- . \
  ':!:CHANGELOG.md' ':!:docs/releases' ':!:examples/v2.0.*' ':!:README.md'
# README should show exactly one hit: the fork-attribution line.

# 1b. "configure" is retired outside history
git grep -in 'configure' -- . ':!:CHANGELOG.md' ':!:docs/releases' ':!:examples/v2.0.*'

# 2. Gate + unit suite
bash scripts/check-skill-repo.sh .
python3 -m unittest discover -s tests
python3 scripts/run-test-prompts.py

# 3. Receipt roundtrip under the new header
python3 scripts/make-receipt.py --phase review --claude-session x \
  --checks "ci" --codex-jobs 1 --scope project --config-source project \
  --roles-used '[]' | python3 scripts/validate-receipt.py -

# 4. Ledger regeneration must be reproducible
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json   # after committing the regenerated file

# 5. Install path
bash install.sh --dry-run      # prints ~/.claude/skills/agent-handoff
bash install.sh --status

# 6. End-to-end on a scratch repo: config write lands in .handoff/, delegate resolves a role
python3 scripts/handoff-config.py --repo /tmp/scratch init
python3 scripts/handoff-config.py --repo /tmp/scratch resolve
bash scripts/delegate-codex.sh list --repo /tmp/scratch
```

Also re-run the README table generator (`showcase-cost-ledger.py --markdown`) and confirm the README cost table matches the regenerated ledger labels.

## Sequence

1. Gates + `.gitignore` → verify they now fail against the un-renamed tree (proves the gates bite).
2. `git mv` the seven files; fix module load sites and imports → `python3 -m unittest discover -s tests` green.
3. Runtime scripts, schemas, `test-prompts.json`; regenerate the ledger and `examples/session-receipt.md`.
4. Prose sweep + CHANGELOG bullets.
5. Full verification block above; `git grep -in partner` clean apart from the allowlist.
