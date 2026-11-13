# Backend: copilot

- **Preflight:** `copilot --version`, and read the output: only the string `GitHub Copilot CLI` identifies it. AWS Copilot CLI shares the binary name, so the exit code proves nothing.
- **Egress:** session sync to GitHub is always off. Submit reads `copilot --help` and passes `--sync-session=off`, or `--no-remote --no-remote-export` on a CLI older than 1.0.103. A CLI whose help lists neither is refused at submit, and the refusal says to run `copilot update`.
- **Model:** a concrete model name. `auto` is refused, because an identity is a deliberate choice and `auto` hands it back to the vendor on every request.
- **Posture:** `default` allows all tools and keeps Copilot's path and URL checks. `allow-all` drops those checks too.
- **Read-only:** plan mode, and never together with the allow-all-tools flag. That pairing was probed: the worker reports writes that never happened and emits no denial, so nothing would catch it.
- **Failures:** an API failure can leave `stderr.log` empty. The detail is a `session.error` event in `log.jsonl`, which `result --json` also reports.
- **A launch failure that names a flag** (`was removed`, `unexpected argument`) means the installed CLI no longer accepts Handoff's command line. The job ends `FAILED` about a second after submit, with an empty `log.jsonl`. Do not retry it or change the model. Take the row back, record the anomaly with the `codex_version` from `meta`, and tell the user Handoff needs an update for that CLI version.
- **Fix rounds:** the session id is assigned at submit. A job that failed before Copilot created its session cannot be resumed, and `copilot --resume` says so on stderr. Submit a fresh job with the full packet, unless the failure named a flag (above).
- **Denials are counted** in `permission_denied`.
- **Usage on fix rounds:** on CLI 1.0.109 a fix round's `usage.json` repeats its parent's, so the cost receipt counts the parent twice. Say so when you report a run that had copilot fix rounds.
- **Commits:** a worker commit carries `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`. Report it; do not strip it.
