# Backend: copilot

- **Preflight:** `copilot --version`, and read the output: only the string `GitHub Copilot CLI` identifies it. AWS Copilot CLI shares the binary name, so the exit code proves nothing.
- **Model:** a concrete model name. `auto` is refused, because an identity is a deliberate choice and `auto` hands it back to the vendor on every request.
- **Posture:** `default` allows all tools and keeps Copilot's path and URL checks. `allow-all` drops those checks too.
- **Read-only:** plan mode, and never together with the allow-all-tools flag. That pairing was probed: the worker reports writes that never happened and emits no denial, so nothing would catch it.
- **Failures:** an API failure can leave `stderr.log` empty. The detail is a `session.error` event in `log.jsonl`, which `result --json` also reports.
- **Fix rounds:** the session id is assigned at submit. A job that failed before Copilot created its session cannot be resumed, and `copilot --resume` says so on stderr. Submit a fresh job with the full packet.
- **Denials are counted** in `permission_denied`.
