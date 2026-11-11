# Backend: cursor

- **Preflight:** `cursor-agent --version`, then `cursor-agent status` for the sign-in. Never check `cursor`: that is the IDE launcher.
- **Model and effort:** the model is a whole Cursor catalogue slug that carries its own effort (`claude-opus-5-5-high`), so the effort is always `model`. `auto` is refused. `(NO ZDR)` in a model's display name means no zero data retention, and a delegated job sends repository contents to that model.
- **Posture:** `default` and `allow-all` are the same here. Both run in Cursor's force mode, and every writing job prints the bypass warning. Deny rules in the user's or the project's `.cursor/cli.json` are the only narrowing, and Handoff writes nothing there.
- **Read-only:** plan mode.
- **Commits:** Cursor's attribution setting adds a `Co-authored-by: Cursor <cursoragent@cursor.com>` trailer to worker commits, e2e commits included. It is the user's setting: report it, do not strip it.
- **Denials are counted** in `permission_denied`.

Failures:

- A model rejected at launch gives `FAILED` with an empty `log.jsonl`. Cursor's message and the models it accepts are in `stderr.log`.
- A `spawnError` reading `spawn /bin/zsh ENOENT` usually means the worker mistyped the working directory it passed to the shell, not that the shell is missing. Read `workingDirectory` in the matching started event and name the typo in the fix round.
- A fix round never reports a lost session. Resuming an id Cursor never persisted starts a new, empty session under that id. A round that shows no memory of its parent's work is that case: resubmit with the full packet instead of resuming again.
