# Agent Handoff Setup Wizard (`/agent-handoff config`)

First-run configuration for Handoff. Triggered by `/agent-handoff config` (or when a Handoff flow needs a role that has no configuration yet). Open the localhost-only single-page UI so the user can see and change every concrete backend/model/effort without repeated chat questions. Every preview and state change still goes through `python3 "$HANDOFF_DIR/scripts/handoff-setup.py"` — one engine, no second implementation. Never edit `.handoff/config.toml` by hand in this flow.

## Render paths

Pick exactly one:

- **With a browser**: run `python3 "$HANDOFF_DIR/scripts/handoff-setup-ui.py" --repo <repo>`. Report the printed localhost URL. The UI binds only to `127.0.0.1`, uses a per-run token, shows the full matrix on one page, and cannot apply a payload that no longer matches its latest preview.
- **No browser (plain CLI)**: run `python3 "$HANDOFF_DIR/scripts/handoff-setup.py" --interactive` in the terminal and step back. Conversational questions are a last-resort fallback, not the default setup experience.

## Single-page UI

1. **Detection (display)** — show every CLI's availability, versions, existing config (if any), and every model/effort source. Codex models and each model's supported effort values come from the account-aware CLI `model/list`; the current config value is preserved if absent from that response. Claude aliases come from `claude --help` plus the stable `fable`/`opus`/`sonnet`/`haiku` aliases, with `[1m]`/`1M` context variants normalized and deduplicated. Claude effort values come from the same help output. Copilot has no model catalog to read — `copilot model` is not a command and `availableModels` inside a run's JSONL is `auto`'s routing candidate set, not a list of what an account may name — so its model is a text field, source-labelled `Typed; validated against the CLI`, and its effort list is the CLI's own enum `none|minimal|low|medium|high|xhigh|max`. Opening the page runs no copilot subprocess and costs nothing. Availability for copilot is decided by reading `--version` for `GitHub Copilot CLI`, not by finding a binary named `copilot`: AWS Copilot CLI shares the name. Never render one shared effort enum across the three CLIs.
2. **Work mode and full matrix** — offer balanced (default) / quality / cost as starting points. The three identity rows (deep_reasoner / fast_worker / arbiter) always keep their backend → model → effort controls visible. Changing any row makes the matrix custom internally. Switching a backend or model immediately constrains effort to that exact selection's supported values and falls back to the nearest safe value (prefer `high`) when the old value is invalid.
3. **Beginner-safe write policy** — do not ask first-time users to choose scope, Git treatment, routing blocks, generated files, or whether to run a smoke test. The UI fixes these to: current project, `.git/info/exclude`, no persistent routing block, generated Claude agents, and automatic smoke. Advanced callers can still use `handoff-setup.py` directly for global scope or explicit overrides.

4. **Preview** — the UI runs `handoff-setup.py --preview ...` with the current controls and shows exact file paths and diffs inline. Any control change invalidates the preview.
5. **Apply** — enable apply only after the user checks the inline confirmation. Re-run preview and reject if its output changed, then pass the same arguments to `--apply`. Report exactly what was written. If the repo-scope config is not git-ignored, the engine handles the exclude choice (default: one line in `.git/info/exclude`); relay its report.
6. **Automatic smoke test** — run `handoff-setup.py --smoke` after apply. Codex-backend identities verify through the delegate dry-run chain. Claude-backend identities start a fresh, tool-free, non-persistent Claude CLI session using the selected model and effort; a generated namespaced agent is selected when one was generated. Copilot-backend identities take the delegate dry-run chain and then a real no-tool `copilot -p` run that asks the CLI whether this account can use the configured model-and-effort pair, because the dry run only exercises Handoff's own argument handling. That check is cheap in one direction only: a model this account cannot use, or an effort the model does not support, is refused before any session exists and spends no premium request, while a pair that does work starts a real session and costs one. Apply refuses to write a config whose pair the CLI rejected, and reports the CLI's own wording verbatim rather than a paraphrase. Only a successful backend check writes `verified=true` and one shared `verified_at` timestamp. Apply remains a completed write if smoke fails, but the UI must visibly say `Installed, but the automatic check did not pass` and preserve `verified=false` for the failed identity.
7. Point the user at `/agent-handoff tryout` (`references/tryout.md`) — the real end-to-end proof pass where every identity runs a micro-task and a report shows each one live on its configured model. Close with a normal Handoff Session Receipt.

## Existing user agents

If the user already has their own `deep-reasoner.md` / `fast-worker.md` agents, the generated files stay namespaced (`handoff-deep-reasoner`, `handoff-fast-worker`) and never touch user files. When the engine refuses a path (exists, not in the manifest), offer the three-way:

- **Import existing settings** — read the user agent's model as the initial value, then still write only `handoff-*` files.
- **Generate namespaced `handoff-*`** (default) — skip the conflicting path, write the rest.
- **Skip** — no agent files; config only.

## Rules

- Show every identity and its concrete backend/model/effort selection in one local page, including the optional e2e pair when it is enabled.
- The page carries one more optional toggle: whether `deep_reasoner` reviews the plan once during planning before the user sees it (`--spec-review`, default off). Seed the checkbox from the resolved config so re-running setup never silently clears it, and treat it as a responsibility toggle, not a routing value — turning it on or off leaves `verified` alone.
- Values the engine detected are filled and source-labelled, not re-asked.
- Preview before every write; the user sees paths + diffs, not a summary.
- No silent model fallback. Effort may only be adjusted to an advertised value while the user is changing backend/model in the UI; apply and smoke surface unsupported combinations and never swap models quietly. A copilot identity must name a concrete model: `auto` is refused at setup validation and again at submit, because an identity is a deliberate `backend + model + effort` choice and `auto` hands that choice back to the vendor per request.
- `--rollback` restores the last-apply backup; offer it if the user is unhappy right after an apply.
- User-owned files (their agents, hand-written CLAUDE.md/AGENTS.md content) are read-only to this flow; the managed routing block writes only inside its own markers and only when explicitly enabled.

## Uninstall

`python3 "$HANDOFF_DIR/scripts/handoff-setup.py" --uninstall [--remove-config] [--dry-run]`

Removes only what Handoff generated: `handoff-*` agent files whose hash still matches `.handoff/.generated-manifest` (a file the user hand-edited since generation is left in place and reported as skipped, never deleted), and a structurally valid managed routing block. Config is untouched unless `--remove-config` is passed, which clears only the identity sections — the top-level fields, `[routing]`, and comments are byte-preserved. `--dry-run` reports what would be removed without writing anything.
