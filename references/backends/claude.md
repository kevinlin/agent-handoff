# Backend: claude

A second Claude Code, run as its own background job.

- **Preflight:** `claude --version`.
- **Posture:** `default` is `dontAsk` with the tools `Read Glob Grep Edit Write Bash`: a call the user's settings do not already approve is denied, not asked. `allow-all` is `bypassPermissions`. Neither adds an OS-level sandbox.
- **Read-only:** plan mode, without the tool allowlist.
- **Override:** the environment variable `HANDOFF_CLAUDE_PERMISSION_MODE` replaces the configured posture. Its value is validated first, and `--read-only` then wins over it.
- **Denials are counted** in `permission_denied`.
