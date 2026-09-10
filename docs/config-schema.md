# Agent Handoff configuration schema v2

Handoff uses one TOML configuration shape at project and global scope. The writer owns the `hosts.claude_code` namespace and the `[review]` section; top-level comments, `[routing]`, and unknown sections are preserved as raw bytes.

An identity is the routing choice `backend + model + effort` plus a per-identity `permission_mode`. Tasks select one of `deep_reasoner`, `fast_worker`, `arbiter`, `e2e_specifier`, or `e2e_verifier`; the identity's `backend` determines which CLI executes it.

All three backend values — `claude`, `codex`, and `copilot` — are first-class execution channels for delegated jobs. `backend = "claude"` is not a subagent-only marker, and `backend = "copilot"` is not a second-class one: `delegate-codex.sh --role <identity>` runs any of them as a background job with the same jobId, job state, monitoring, fix-round `resume`, and receipt evidence. A per-job `--backend` contradicting a configured identity is refused — moving work onto another vendor is a change to this file, not a per-run override.

A copilot identity must name a concrete model. `model = "auto"` is refused at submit: an identity is a deliberate `backend + model + effort` choice, and `auto` hands that choice back to the vendor per request, so the receipt would record what Copilot picked rather than what the repo configured.

The first three are core and always configured. `e2e_specifier` and `e2e_verifier` are optional: setup writes them only when it runs with `--with-e2e`, and a config carrying just the three core identities is complete. `schema_version` stays `2` — adding identity names does not change the document shape.

`deep_reasoner` no longer carries a spec-review toggle. Until 3.8.0 it had `auto_review_spec`; spec review now runs on every plan, and the parser drops the retired key on read, so an old config still loads and the next write removes it.

The `[review]` section holds the round caps for the two review gates (`docs/specs/design_agent-handoff.md`, *The two review gates, and where they escalate*). Both keys are optional integers of at least 1; absent values resolve to the built-in defaults, 1 and 3. They merge per field across project, global, and defaults. There is no session override, because `delegate-codex.sh resume` enforces the caps from its own `resolve` call. A pre-3.8 engine ignores the section rather than refusing it.

A `backend = "copilot"` config fails closed the same way on a pre-3.7 engine: `validate_config` gates `backend` on the known tuple, so an older engine refuses the file rather than running the identity on the wrong CLI.

A config that carries the optional identities fails closed on a pre-3.2 engine: `validate_config` raises on an identity name it does not recognise. That is the correct direction for a version skew — an older engine refuses the file rather than silently ignoring two identities.

## Locations and precedence

Values resolve from highest to lowest priority:

1. Session override supplied by the current task.
2. `<repo>/.handoff/config.toml`.
3. `${XDG_CONFIG_HOME:-$HOME/.config}/handoff/config.toml`.
4. Built-in defaults.

Project and global files use the same schema. Higher layers merge by field, so an override for one identity field does not erase unrelated lower-layer fields. Built-in defaults provide schema metadata, empty identity maps, and `always_on_host_rules = false`; model presets belong to setup and are not duplicated in this engine.

## Example

```toml
schema_version = 2
revision = 0

[hosts.claude_code.identities.deep_reasoner]
backend = "claude"          # claude | codex | copilot — which CLI executes
model = "opus"
effort = "high"
permission_mode = "default" # default | allow-all
verified = false

[hosts.claude_code.identities.fast_worker]
backend = "codex"
model = "gpt-5.6-sol"
effort = "medium"
verified = false

[hosts.claude_code.identities.arbiter]
backend = "codex"
model = "gpt-5.6-sol"
effort = "xhigh"
verified = false

# Optional. Setup writes the two sections below only under --with-e2e.
[hosts.claude_code.identities.e2e_specifier]
backend = "codex"
model = "gpt-5.6-sol"
effort = "xhigh"
verified = false

[hosts.claude_code.identities.e2e_verifier]
backend = "codex"
model = "gpt-5.6-sol"
effort = "high"
verified = false

[review]
spec_max_rounds = 1
implementation_max_rounds = 3

[routing]
always_on_host_rules = false
```

## Fields

| Path | Type | Required | Meaning |
|---|---|---:|---|
| `schema_version` | integer | yes | Must be `2`. |
| `revision` | non-negative integer | yes, reserved | Reserved for later optimistic concurrency checks; the current engine does not compare or increment it. |
| `hosts.claude_code` | table | per configured identity | The owned namespace. The `hosts.*` nesting is retained so configs written by earlier versions keep loading. |
| `hosts.claude_code.identities.<identity>` | table | per configured identity | `<identity>` is `deep_reasoner`, `fast_worker`, `arbiter`, `e2e_specifier`, or `e2e_verifier`. The last two are optional. |
| `hosts.claude_code.identities.<identity>.backend` | string enum | per configured identity | Required execution CLI for this identity's delegated jobs: `claude`, `codex`, or `copilot`. All three are first-class; the value decides which CLI `delegate-codex.sh` invokes. |
| `hosts.claude_code.identities.<identity>.model` | string | per configured identity | Non-empty model name or alias passed to the selected backend. On `copilot`, `auto` is refused at submit — name a concrete model. |
| `hosts.claude_code.identities.<identity>.effort` | string | per configured identity | Non-empty reasoning effort passed to the selected backend. Efforts are per CLI, never one shared enum: codex takes `minimal|low|medium|high|xhigh|max|ultra`, claude takes `low|medium|high|xhigh|max`, copilot takes `none|minimal|low|medium|high|xhigh|max`. Copilot's enum is the CLI's superset — which efforts a given Copilot model actually accepts is decided per model by the API, and a rejected pair surfaces as Copilot's own error rather than being silently downgraded. |
| `hosts.claude_code.identities.<identity>.permission_mode` | string enum | no | `default` or `allow-all`; absence resolves to `default` after the per-field merge. |
| `hosts.claude_code.identities.<identity>.verified` | boolean | no | Whether a smoke test or real run verified the identity. |
| `hosts.claude_code.identities.<identity>.verified_at` | string | no | Verification timestamp supplied by the caller. |
| `routing.always_on_host_rules` | boolean | no | Whether setup writes a persistent routing block; default `false`. |
| `review.spec_max_rounds` | integer ≥ 1 | no | Spec review passes (`deep_reasoner` reads the plan) before a still-declined blocking finding goes to the arbiter. Default `1`. |
| `review.implementation_max_rounds` | integer ≥ 1 | no | Driver review passes on a delegated diff (the original job plus each `resume`) before open findings go to the arbiter. Default `3`. |

Every configured identity requires `backend`, `model`, and `effort`. Backend is validated by this engine; model and effort compatibility is checked by the setup/smoke layer.

## Ownership and deterministic writes

The writer may rewrite only `[hosts.claude_code.identities.*]` sections and `[review]`. Everything else in the file round-trips byte-for-byte. The owned identity sections are emitted in identity order (`deep_reasoner`, `fast_worker`, `arbiter`, `e2e_specifier`, `e2e_verifier`) and field order: `backend`, `model`, `effort`, `permission_mode`, `verified`, then `verified_at`. `[review]` is emitted as `spec_max_rounds`, then `implementation_max_rounds`, replaced where it stands, or appended at the end of the file when absent. Strings are double-quoted. Repeating the same write produces identical bytes.

Comments and formatting inside an owned section are intentionally not retained. All unowned chunks remain in their original order and retain their original bytes, including comments and line endings.

## Schema v1 migration

Schema v1 is never converted silently. A file with `schema_version = 1`, or with any `hosts.<host>.roles.*` section even if its version says otherwise, fails closed in `resolve`, `get`, `set`, and `validate`. The error includes the configuration path and this instruction:

> Detected a schema v1 config. Rerun `/agent-handoff config` to upgrade (setup replaces it with a schema v2 document and backs up the old file).

The setup wizard treats a v1 file as a blank starting point: the preview shows the full replacement, the apply backs the original up under `.handoff/backups/`, and `--rollback` restores it. Old v1 values are not carried into the new document; pick them again in the wizard if you still want them.

## Concurrency and atomicity

A write creates `.config.lock` in the directory containing `config.toml` using atomic `os.mkdir`. A lock directory older than 15 seconds is treated as abandoned and reclaimed. While holding the lock, the writer reads the latest file, changes its identity sections, writes a same-directory temporary file, and commits with `os.replace`.

- A lock younger than that fails closed straight away, naming the lock directory to remove if no writer is actually active.

`revision` remains reserved for a later defense-in-depth optimistic concurrency check and has no concurrency behavior in schema v2.

## Supported TOML subset

The parser supports bare keys, double-quoted strings, integers, booleans, standard table headers, and `#` comments. It is not a general TOML parser. Sections it does not own are never parsed, only carried across a write byte-for-byte, so they may use syntax outside this subset.

The following constructs fail closed with a line number, character position, and a pointer back to this section:

- inline tables (`value = { ... }`);
- multiline strings;
- datetime values;
- array-of-tables headers (`[[...]]`) naming `[routing]`, `[review]`, or an owned identity section (elsewhere they are treated as an unowned section and preserved);
- dotted-key assignments (`a.b = ...`).

The engine parses only top-level schema metadata, `[routing]`, `[review]`, and the owned identity sections. This boundary allows any future unknown section to round-trip without reformatting.

## CLI

Run from the repository root:

```sh
python3 scripts/handoff-config.py --scope project init
python3 scripts/handoff-config.py --scope project validate
python3 scripts/handoff-config.py --scope project get hosts.claude_code.identities.deep_reasoner.backend
python3 scripts/handoff-config.py --scope project set --role deep_reasoner --backend codex --model MODEL --effort xhigh
python3 scripts/handoff-config.py --scope project set --role fast_worker --permission-mode allow-all
python3 scripts/handoff-setup.py --preview --role-permission-mode fast_worker=default
python3 scripts/handoff-config.py --scope project set-review --spec-max-rounds 1 --implementation-max-rounds 3
python3 scripts/handoff-config.py --repo /path/to/repo resolve
python3 scripts/handoff-config.py --repo /path/to/repo resolve --override fast_worker.permission_mode=default
python3 scripts/handoff-config.py --repo /path/to/repo resolve --override deep_reasoner.effort=high
```

The `set` command retains `--role` as its identity selector, and accepts the optional e2e identities alongside the core three. `set-review` writes the `[review]` section; pass either flag or both, and a value below 1 is refused without writing. `--override` targets identity fields only; the review caps cannot be overridden per call. `--backend` is required when creating an identity and may be omitted on update to preserve the current value. `get` and `resolve` include `backend` in each configured identity.

Use `--scope global` to target the XDG/HOME location. `resolve` always evaluates the complete precedence chain; `get`, `set`, `validate`, and `init` target the selected scope.

## Permission posture (v3.7.1)

`default` is the CLI's own normal, bounded posture. Nothing prompts (a background
job has no approval surface), so an unapproved action denies. `allow-all` means
*use the provider's native unrestricted mode*, not force three CLIs into one
security posture.

| Backend | `default` | `allow-all` |
|---|---|---|
| claude | `--permission-mode dontAsk --allowed-tools Read Glob Grep Edit Write Bash` | `--permission-mode bypassPermissions` |
| codex (fresh and resume) | No sandbox flag or override; the user's codex config decides | `--dangerously-bypass-approvals-and-sandbox` |
| copilot | `--allow-all-tools` | `--allow-all-tools --allow-all-paths --allow-all-urls` |

Only claude changes behaviour under `default`. Codex and copilot `default` are
exactly what they emitted before; `allow-all` is a new, opt-in escalation on all
three. This deliberately narrows the blast radius for a patch release.

Claude's default set is enough to edit and verify: a `default` worker was
measured editing a file and running its own check script. Under `dontAsk` a tool
call that is not already approved is denied instead of run, where
`bypassPermissions` runs it. That is the whole of the difference, and it is
smaller than it looks. `deny` and `ask` rules in the user's own settings produce
hard denials under **both** postures -- measured, not assumed -- so they are not
what separates them; and on a machine whose settings already approve a tool,
both postures were observed reaching it with zero denials, `WebFetch` included.
How much `default` narrows therefore depends on the user's own Claude Code
settings. It is a permission-rule posture, never containment. The allowlist is
not applied to `--read-only` jobs.

Precedence: `--read-only` > `HANDOFF_CLAUDE_PERMISSION_MODE` (claude only) >
config `permission_mode` > `default`. The legacy env override is still
validated before read-only overrides it. Read-only maps to `-s read-only`
on fresh codex jobs (`-c 'sandbox_mode="read-only"'` on resume),
`--permission-mode plan` on claude, and `--mode plan` on copilot. Copilot
read-only never carries any allow-all flags.

Resolution materializes the default after merging: global `allow-all` plus a
project identity omitting the field keeps `allow-all`. Like `model_source`,
`permission_posture_source=config:<layer>` names the top participating layer,
not per-field provenance. Role-less jobs use `default`. Resume inherits
`permission_posture` and `read_only` from the parent; a missing posture key
means `default`, never `allow-all`. An old key does not prove unrestricted
authority.

Job `meta` and dry-run output record requested `permission_posture`, its
`permission_posture_source` (`config:<layer>`, `explicit`, `env`,
`read-only`, `parent`, or `default`), and the effective concrete
`permission_mode`. Overrides change the source and effective mode while the
requested posture remains visible. Warnings use the effective mode. Receipt
schema stays v6: `roles_used` rejects additional properties, so posture
evidence stays in job `meta`.

Changing posture does not invalidate `verified` or `verified_at`. Smoke
verifies a model/effort pair, not a posture; its plan-mode probes are unchanged.

### Deviations from the research doc

1. **codex `default` passes no sandbox flag**, where the doc says
   `workspace-write`. Probed: ordinary file writes succeed but `.git/` writes
   are refused, and `sandbox_workspace_write.writable_roots` on the repo root
   does not lift it. Forcing it would break the worker-commits-on-its-worktree-
   branch contract for users who have not granted `.git` per repo and narrow
   a `danger-full-access` user whose e2e worker commits today. Inheriting the
   user's codex config preserves the documented contract.
2. **codex has no `--ask-for-approval` on `exec`** (top-level flag only).
   `codex exec` never prompts, so the doc's `approval=never` half is already
   satisfied.
3. **copilot `default` keeps `--allow-all-tools`**, where the doc lists
   `--allow-tool=write` plus stack-specific test/build/lint rules. The doc
   permits test execution; the unsolved part is enumerating those commands per
   repo, which Handoff cannot know. `--allow-all-tools` leaves Copilot's path
   and URL verification on; `allow-all` turns those off.

### Stated limitations

- **No OS-level sandbox for claude `default`.** The research doc also asks for
  enforced sandboxing, workspace-only IO, and denied network. This release
  changes the permission-rule layer only. The Darwin ratchet requires one
  dimension per change, and network denial interacts with installing and testing.
- **Codex denials are not counted.** The scanner counts Claude-shaped
  `subtype: permission_denied` and Copilot `error.code: "denied"` events.
  Codex sandbox refusals match neither, and result extraction discards command
  outcomes. The shared denial test's Claude-shaped event proves nothing about
  Codex denial detection. Implementing that detection is out of scope here.
- **`permission_denied` is advisory, not enforced.** Job state derives DONE
  from the exit code, and status warns but still succeeds. The prompt-shaped
  guard stays prompt-shaped; zero denials cannot prove the checks ran.
