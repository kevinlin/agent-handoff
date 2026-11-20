# Backend: cursor

- **Preflight:** `cursor-agent --version`, then `cursor-agent status` for the sign-in. Never check `cursor`: that is the IDE launcher.
- **Model and effort:** the model is a whole Cursor catalogue slug that carries its own effort (`claude-opus-5-5-high`), so the effort is always `model`. `auto`, an empty model and the bracket form (`claude-sonnet-5-5[context=1m,…]`) are refused. The bracket form can select Cursor's 1M context, and a job with no model would run whatever Cursor last saved as its selected model. `(NO ZDR)` in a model's display name means no zero data retention, and a delegated job sends repository contents to that model.
- **Context window:** every cursor job passes its slug, fix rounds included. Every slug probed ran at the model's default context (300K on Claude Sonnet 5.5), not Cursor's 1M Max Mode. If a job ran at 1M anyway, `status` and `result` print a `context_window: 1M …` line. That means a Cursor update changed what the slug selects. Cancel the job if it is still running, record the anomaly with the jobId, the window and the `codex_version` from `meta`, and tell the user. Review a finished job's output as usual, because its usage is already spent. From then on `submit` and `resume` refuse that slug on that Cursor build in this repo. That covers a fix round, a retry and an arbiter resubmit. The user picks another slug or backend, or waits for a Cursor build that fixes it.
- **Saved selection:** each job saves its slug as Cursor's selected model in `~/.cursor/cli-config.json`, so the user's next interactive `cursor-agent` opens on it. That is Cursor's own write.
- **Posture:** `default` and `allow-all` are the same here. Both run in Cursor's force mode, and every writing job prints the bypass warning. Deny rules are the only narrowing: the user's global ones in `~/.cursor/cli-config.json`, the project's in `.cursor/cli.json`. Handoff writes nothing to either.
- **Read-only:** plan mode.
- **Commits:** Cursor's attribution setting adds a `Co-authored-by: Cursor <cursoragent@cursor.com>` trailer to worker commits, e2e commits included. It is the user's setting: report it, do not strip it.
- **Denials are counted** in `permission_denied`.

Failures:

- A model rejected at launch gives `FAILED` with an empty `log.jsonl`. Cursor's message and the models it accepts are in `stderr.log`.
- A `spawnError` reading `spawn /bin/zsh ENOENT` usually means the worker mistyped the working directory it passed to the shell, not that the shell is missing. Read `workingDirectory` in the matching started event and name the typo in the fix round.
- A fix round never reports a lost session. Resuming an id Cursor never persisted starts a new, empty session under that id. A round that shows no memory of its parent's work is that case: resubmit with the full packet instead of resuming again.
- `resume` refuses a parent that recorded no plain slug: a cursor fix round from before 4.0.2 recorded `inherit`. Resubmit with the full packet.
