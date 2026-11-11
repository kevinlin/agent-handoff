# Backend: codex

- **Preflight:** `codex --version`.
- **Posture:** `default` inherits the user's Codex config and adds no sandbox flag. `allow-all` runs with approvals and the sandbox bypassed.
- **Effort:** `ultra` exists only here, and not every model offers it. It adds automatic task delegation inside the job, so use it only when the worker should subdivide the job itself.
- **Read-only:** Codex's read-only sandbox.
- **Denials are not counted.** `permission_denied` stays at 0 on codex whatever was refused, so it proves nothing here. Verify edits and checks on disk.
