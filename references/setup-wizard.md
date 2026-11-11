# Setup wizard

`/agent-handoff config` (also `setup`, `init`) writes the identity configuration. Run it too when a flow needs an identity that has no configuration yet. Every preview and every write goes through `handoff-setup.py`. Never edit `.handoff/config.toml` by hand in this flow, and do not collect the settings through repeated chat questions when a browser is available.

## What gets configured

Five identities. Each has its own `backend` (`claude`, `codex`, `copilot`, `cursor` or `antigravity`), `model`, `effort` and `permission_mode` (`default` or `allow-all`; absent means `default`), and they mix freely across vendors.

- `deep_reasoner`, `fast_worker` and `arbiter` are always configured. A config with only these three is complete.
- `e2e_tester` is written only when setup runs `--with-e2e`, and `operator` only with `--with-operator`. Setup keeps an optional identity the config already has, unless the matching `--no-with-...` flag is given.
- A config from before 4.0.0 still has `[e2e_specifier]` or `[e2e_verifier]`. Setup replaces them with `e2e_tester`, filled from the old specifier, and the preview shows the change.

Two review caps go with them, in the config's `[review]` section: `spec_max_rounds` (default 1) and `implementation_max_rounds` (default 3), set with `--spec-max-rounds` and `--implementation-max-rounds`. Setup seeds both from the resolved config, so running it again never resets them, and changing them leaves `verified` alone.

Model and effort rules differ per backend and live in `references/backends/<backend>.md`. Read the file for a backend before you set an identity to it by hand, for example with `handoff-config.py --repo "$REPO" set --role <identity> --backend <backend> --model <model> --effort <effort>`.

## Run it

Stamp the run first, so the closing receipt has a start to measure from: `python3 "$HANDOFF_DIR/scripts/make-receipt.py" --start --repo "$REPO"`. Then pick one path:

- **With a browser:** `python3 "$HANDOFF_DIR/scripts/handoff-setup-ui.py" --repo "$REPO"`, and report the localhost URL it prints. The page binds to `127.0.0.1` behind a per-run token. It shows every identity's backend, model, effort and permission on one page, with the choices read from the installed CLIs and, for Copilot, from the account. The user previews the exact file paths and diffs, then confirms and applies. A smoke test follows by itself.
- **No browser:** `python3 "$HANDOFF_DIR/scripts/handoff-setup.py" --interactive --repo "$REPO"` in the terminal, then step back. A preset uses `default` permissions. Its custom mode asks a permission question per identity, and an empty answer means `default`. A Cursor or Antigravity model is typed on this path, so only the smoke test checks that id against the catalogue. Chat questions are the last resort. Use this path too for a schema v1 config: the page cannot open on one, and the terminal path replaces the file and keeps a backup.

The page fixes the beginner choices itself: project scope, a `.git/info/exclude` entry, no persistent routing block, generated Claude agents, and an automatic smoke test. Do not ask a first-time user about them. An advanced caller passes flags to `handoff-setup.py` directly: global scope, `--role-permission-mode IDENTITY=VALUE`, `--with-e2e`, `--with-operator`.

## What to relay

- **Preview before every write.** The user sees paths and diffs, not a summary. Report exactly what an apply wrote, including the engine's note when it git-ignores the config.
- **Smoke** runs after apply and can spend: each Claude identity starts one fresh tool-free session, a usable Copilot pair costs one premium request, and Cursor and Antigravity each make one real run. It checks model and effort, not the permission posture. Only a passing check writes `verified = true`. If it fails, the apply stays in place: say `Installed, but the automatic check did not pass`, and that identity keeps `verified = false`.
- **No silent fallback.** A value the engine detected is shown with its source and not asked again. An effort may be adjusted to one the backend advertises only while the user is changing the backend or model in the page. Apply and smoke surface an unsupported combination, and a model is never swapped quietly.
- **Rollback.** `handoff-setup.py --rollback --repo "$REPO"` restores the backup of the last apply. Offer it when the user is unhappy right after an apply.
- Then point the user at `/agent-handoff tryout`, and close with a Handoff Session Receipt (`references/session-receipt.md`).

## Existing user agents

Setup generates Claude agent files under the `handoff-` namespace (`handoff-deep-reasoner`, `handoff-fast-worker`, and so on) and never touches the user's own. User-owned files, hand-written `CLAUDE.md` and `AGENTS.md` content included, are read-only to this flow. The managed routing block is written only inside its own markers and only when explicitly enabled.

When the engine refuses a path because the file exists and is not in its manifest, offer three choices:

- **Import existing settings:** read the user agent's model as the starting value, and still write only `handoff-*` files.
- **Generate namespaced files** (the default): skip the conflicting path and write the rest.
- **Skip:** no agent files, config only.

## Uninstall

`python3 "$HANDOFF_DIR/scripts/handoff-setup.py" --uninstall --repo "$REPO" [--remove-config] [--dry-run]`

It removes only what Handoff generated: `handoff-*` agent files whose hash still matches `.handoff/.generated-manifest`, and a structurally valid managed routing block. A file the user edited since generation is left in place and reported as skipped. The config is untouched unless `--remove-config` is passed, and then only the identity sections are cleared. `--dry-run` reports what would be removed and writes nothing.
