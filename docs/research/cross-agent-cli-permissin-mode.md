# Cross-Agent CLI Permission Modes Specification

## Objective

Implement a common permission abstraction for three coding-agent CLIs:

* Anthropic Claude Code
* GitHub Copilot CLI
* OpenAI Codex CLI

Expose only two application-level permission modes:

```ts
type PermissionMode =
  | "default"
  | "allow-all";
```

The goal is to give each provider a sensible autonomous default while preserving the provider's native unrestricted mode.

Do not try to make the three CLIs use identical low-level flags. Their permission and sandbox models differ.

`default` should mean:

* no interactive approval prompts
* repository files can be read and modified
* normal development and verification commands can run
* outbound network access is denied unless explicitly configured
* writes outside the workspace are denied
* dangerous or unrestricted host access is avoided

`allow-all` should mean:

* use the provider's native unrestricted permission mode
* do not add extra restrictions beyond what the provider itself keeps active
* do not manually disable an independent sandbox unless the provider's native unrestricted mode already disables it

---

## Permission comparison

| Agent CLI              | permissionMode | CLI mapping                                                     | File read                                                     | File write                              | Shell execution                         | Network                                    | Outside workspace           | Git mutation                                                                | Approval prompts                                   | Sandbox behavior                                                       |
| ---------------------- | -------------- | --------------------------------------------------------------- | ------------------------------------------------------------- | --------------------------------------- | --------------------------------------- | ------------------------------------------ | --------------------------- | --------------------------------------------------------------------------- | -------------------------------------------------- | ---------------------------------------------------------------------- |
| **Claude Code**        | `default`      | `--permission-mode dontAsk` + sandbox settings + allowed tools  | Workspace only, if `blockReadsOutsideWorkingDirectories=true` | Workspace only                          | Broad `Bash`, but sandboxed             | Denied by strict sandbox network allowlist | Read denied, write denied   | Local Git operations may work unless explicitly denied                      | None. Unapproved actions fail                      | OS sandbox enabled and required                                        |
| **Claude Code**        | `allow-all`    | `--dangerously-skip-permissions`                                | Unrestricted by Claude permission rules                       | Unrestricted by Claude permission rules | Unrestricted by Claude permission rules | Permission checks bypassed                 | Permission checks bypassed  | Allowed, subject to external controls                                       | None                                               | Independent sandbox may still remain active if configured              |
| **GitHub Copilot CLI** | `default`      | `--allow-tool=write` + targeted `shell(...)` rules + deny rules | Current repo / allowed paths                                  | Current repo / allowed paths            | Only explicitly approved commands       | Only explicitly approved URLs/tools        | Denied by path verification | Git inspection allowed, destructive/mutating Git commands explicitly denied | None with `--no-ask-user`; unapproved actions fail | Optional sandbox if supported, otherwise permission-rule based         |
| **GitHub Copilot CLI** | `allow-all`    | `--allow-all` / `--yolo`                                        | All paths                                                     | All paths                               | All tools / shell operations approved   | All URLs approved                          | Allowed                     | Allowed                                                                     | None                                               | Independent sandbox may still remain active unless separately disabled |
| **OpenAI Codex CLI**   | `default`      | `--sandbox workspace-write --ask-for-approval never`            | Allowed according to sandbox profile                          | Workspace only                          | Arbitrary commands inside sandbox       | Denied by default                          | Writes denied               | `.git` protected by workspace sandbox; normal inspection allowed            | None. Disallowed actions fail                      | Native `workspace-write` sandbox enforced                              |
| **OpenAI Codex CLI**   | `allow-all`    | `--dangerously-bypass-approvals-and-sandbox` / `--yolo`         | Unrestricted                                                  | Unrestricted                            | Unrestricted                            | Unrestricted                               | Allowed                     | Allowed                                                                     | None                                               | Sandbox explicitly disabled                                            |

A more implementation-focused version is:

| Agent           | `default` permissionMode                                                                                                                                                | `allow-all` permissionMode                                                                                                                                                         |
| --------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Claude Code** | `dontAsk`; allow `Read`, `Glob`, `Grep`, `Edit`, `Bash`; enforce sandbox; workspace-only read/write; network denied; sandbox escape denied                              | `--dangerously-skip-permissions`; skip Claude permission checks; allow file, shell, Git, and network operations; do **not** explicitly disable an independently configured sandbox |
| **Copilot CLI** | Allow `write`; allow Git inspection; allow stack-specific test/build/lint commands; deny destructive Git operations; keep path and URL verification; no arbitrary shell | `--allow-all`; equivalent to all tools + all paths + all URLs; permission checks removed; do **not** add `--no-sandbox` automatically                                              |
| **Codex CLI**   | `workspace-write` sandbox + `approval=never`; arbitrary commands within workspace sandbox; network denied; outside-workspace writes denied                              | `--dangerously-bypass-approvals-and-sandbox`; approvals disabled and sandbox disabled                                                                                              |

The main semantic difference is:

```text
Claude allow-all
= permission checks off
+ sandbox remains independent

Copilot allow-all
= tool/path/URL checks off
+ sandbox remains independent

Codex allow-all
= approval checks off
+ sandbox off
```

So I would keep the common abstraction:

```ts
type PermissionMode =
  | "default"
  | "allow-all";
```

but document that `allow-all` means **use the provider's native unrestricted mode**, not “force all three CLIs into exactly the same security posture.”
