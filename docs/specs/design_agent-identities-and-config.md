# Agent identities, configuration, and setup

## Context

An identity is a capability tier with a concrete home: a `backend`, a `model`, an `effort`, and a `permission_mode`. When the driver splits work it asks one question per task row: *which capability does this need?* The identity's configuration answers the rest — which CLI executes the job, on which model, at which reasoning effort, under which permission posture, and therefore on which meter.

A task row names a tier, never a vendor. Nothing in a prompt, a packet, or a mid-run judgment call can move work onto a different CLI, because the mapping from tier to CLI lives in one file that a user can read and a script enforces.

This document is the design of record for that layer: the identity model, the configuration document, the engine that reads and writes it, and the setup flow that fills it in. The prose contracts agents load are `references/setup.md` (the wizard) and `references/tryout.md` (the proof pass). The same material drawn for a human reader is the user guide's *Agent identities and setup* section, with `docs/user-guide/diagrams/identities.svg` and `docs/user-guide/diagrams/setup-flow.svg`.

### Scope

Covered: the five identities and what each takes, the resolution chain from a task row to a running job, the configuration schema and its ownership rules, the setup wizard (detect, preset, preview, apply, smoke, rollback, uninstall), permission posture, and the Copilot model catalogue.

| Out of scope | Where it lives |
| --- | --- |
| Plan, split, delegate, monitor, review: what the identities are *used for* | `docs/specs/design_agent-handoff.md` |
| The job primitive itself (`submit`/`status`/`result`/`resume`/`cancel`/`cleanup`) | `scripts/delegate-codex.sh` header comment |
| Session evidence, transcripts, receipts | `docs/specs/design_agent-handoff-evidence.md` |
| What the two round caps *do* once resolved | `docs/specs/design_agent-handoff.md`, *The two review gates, and where they escalate* |
| The acceptance gate the optional identities serve | `references/e2e-gauntlet.md`, `docs/verdict-schema.json` |

### The invariant everything here rests on

**The identity's configured `backend` decides which CLI runs a job — always.** All three values — `claude`, `codex`, `copilot` — are first-class execution channels. `backend = "claude"` is not a subagent-only marker and `backend = "copilot"` is not a second-class one: `delegate-codex.sh --role <identity>` runs any of them as a background job with the same jobId, job directory, monitoring, fix-round `resume`, worktree lifecycle, and receipt evidence.

A per-job `--backend` that contradicts a configured identity is refused rather than honoured. Swapping a row onto a cheaper meter is a change to this file, visible in a diff, not a side effect of delegating.

## The five identities

Three core, two optional. Each is a `backend + model + effort` triple plus a permission posture, and nothing more: a capability tier, never a task assignment.

| Identity | Tier | What it takes |
| --- | --- | --- |
| `deep_reasoner` | core | ambiguous work where a wrong premise in step one is expensive to discover late: architecture, hard diagnosis, config changes that fail quietly; also reads every plan before it reaches the user |
| `fast_worker` | core | mechanical, specification-complete work the acceptance criteria alone can verify: refactors, test writing, batch migrations, doc generation, wide read-only scans. Most delegated rows |
| `arbiter` | core | the blind second solver for a contested call, and the judge a review gate escalates to at its round cap. Never assigned routine rows |
| `e2e_specifier` | optional | turns a frozen spec into Gherkin scenarios with stable IDs plus tests in the repo's own stack |
| `e2e_verifier` | optional | runs those reviewed tests against a pinned commit and returns a validated verdict |

**The driver is not an identity.** Rows the driver keeps take identity `-` in the goal file: the split decision itself, cross-task integration, security- and correctness-critical paths, final acceptance. Those run inline and never resolve through this config.

A config carrying only the three core identities is complete. The optional pair is written only when setup runs `--with-e2e`; `handoff-setup.py --status` prints all five with `<unset>` for the unconfigured ones, and that listing is how a user discovers the add-on exists. The "not configured" gate is scoped to the core three, so an absent optional identity is a deliberate state rather than a broken setup.

Two costs of making these full identities rather than task labels are real and accepted: `role` carries two meanings (capability routing in config, pipeline responsibility in the flow), and a user who wants one model for both e2e stages configures it twice.

## From a task row to a job on a meter

```
task row names an identity
  → delegate-codex.sh --role <identity>
  → handoff-config.py resolve      (session → project → global → defaults, per field)
  → backend + model + effort + permission_mode
  → codex exec --json | claude --print --output-format stream-json | copilot -p --output-format json
  → .handoff/jobs/<jobId>/meta     (every value, and where it came from)
```

Resolution is per field, so an override for one identity field does not erase unrelated lower-layer fields. Whatever each field resolves to is recorded in the job's `meta` alongside its source (`config:<layer>`, `explicit`, `env`, `read-only`, `parent`, `default`), so a later question about why a job ran on a given model has a written answer rather than a recollection.

`config_source` names the top participating layer, not per-field provenance. The same is true of `permission_posture_source`.

### Four refusals, each before a job directory exists

| Refusal | Why it fails closed | Enforced in |
| --- | --- | --- |
| No Handoff config at all | A default model would bill someone silently; the error names `/agent-handoff config` | `delegate-codex.sh` submit |
| `--backend` contradicting a named role | Moving work onto another vendor is a config change, and the message names the `handoff-config.py set` that would make it legitimate | `delegate-codex.sh` submit |
| An effort the chosen CLI does not accept | The enums are per vendor, never one shared list | `validate_effort` in `delegate-codex.sh`, `validate_backend_efforts` in `handoff-setup.py` |
| `model = "auto"` on copilot | An identity is a deliberate triple; `auto` hands the model choice back to the vendor per request, so the receipt would record what Copilot picked rather than what the repo configured | `delegate-codex.sh` submit, and `validate_backend_models` at setup |

The effort enums, for reference: codex takes `minimal|low|medium|high|xhigh|max|ultra`, claude takes `low|medium|high|xhigh|max`, copilot takes `none|minimal|low|medium|high|xhigh|max`. Copilot's is the CLI's superset. Which efforts a given Copilot model actually accepts is narrower, and the wizard reads that per-model list from the entitlement catalogue.

## Locations and precedence

Values resolve from highest to lowest priority:

1. Session override supplied by the current task (`resolve --override`).
2. `<repo>/.handoff/config.toml`.
3. `${XDG_CONFIG_HOME:-$HOME/.config}/handoff/config.toml`.
4. Built-in defaults.

Project and global files use the same schema. Built-in defaults provide schema metadata, empty identity maps, and `always_on_host_rules = false`; model presets belong to setup and are not duplicated in the engine. Identity values live in these two files and nowhere else — never in a prompt, a packet, or a doc.

The review caps have no session override, because `delegate-codex.sh resume` enforces them from its own `resolve` call. A cap the driver believed in but the enforcing process never saw would be worse than no cap.

## Key files

| File | What it owns | What it must not do |
| --- | --- | --- |
| `scripts/handoff-config.py` | the pure read/write/resolve/validate engine; owns `hosts.claude_code.identities.*` and `[review]` | reformat or drop anything else: `[routing]`, comments, and unknown sections (including stale `hosts.codex.*` blocks from the dual-host era) round-trip byte-for-byte |
| `scripts/handoff-setup.py` | the plan/preview/apply/smoke/rollback/uninstall engine; presets, detection, agent generation, the managed routing block | write non-atomically, or write without a backup |
| `scripts/handoff-setup-ui.py` | the localhost-only single-page wizard; probes the CLIs for model and effort lists | implement a second write path; every preview and write delegates to `handoff-setup.py` |
| `scripts/delegate-codex.sh` | turning `--role` into a running job on the configured CLI | accept a per-job override that contradicts the config |
| `scripts/handoff_runtime.py` | `clean_claude_env()`, stripping `ANTHROPIC_*`/`CLAUDE_CODE_*` before spawning a child CLI | apply it to the copilot branch, which authenticates through `gh auth` and `~/.copilot` and reads none of those variables |
| `references/setup.md` | the wizard contract an agent loads | ask the setup matrix through repeated chat questions when a browser is available |
| `references/tryout.md` | the post-install proof pass | substitute a model or backend to make a row pass |

The `hosts.*` nesting in the schema is historical and retained so configs written by earlier versions keep loading.

## Setup

`/agent-handoff config` opens a page bound to `127.0.0.1` with a per-run token. Six steps, and everything before apply is reversible.

1. **Detect.** Which CLIs are installed, their versions, the existing config, and each backend's model and effort lists. Codex models and their per-model effort values come from the account-aware CLI `model/list`. Claude aliases come from `claude --help` plus the stable `fable`/`opus`/`sonnet`/`haiku` aliases, with `[1m]`/`1M` context variants normalized and deduplicated. Copilot models come from the account's entitlement API (see *Copilot model catalogue*). Nothing is a hardcoded guess. Copilot availability is decided by reading `--version` for `GitHub Copilot CLI`, because AWS Copilot CLI shares the binary name.
2. **Pick a preset.** `balanced` (default), `quality`, or `cost`. Changing any control switches the matrix to `custom` internally; `custom` mode requires an explicit backend, model, and effort for every selected identity. A codex preset model is detected from `${CODEX_HOME:-$HOME/.codex}/config.toml` rather than guessed. If detection fails, setup says so and names the flag instead of inventing a model name.
3. **Tune.** Per identity: backend → model → effort → permission. Switching backend or model immediately constrains effort to that selection's advertised values. The same page carries the two review caps, because they belong to the configuration and not to a prompt.
4. **Preview.** `handoff-setup.py --preview` prints exact target paths and unified diffs. Any control change invalidates the preview, and the UI cannot apply a payload that no longer matches its latest one.
5. **Apply.** One atomic write with a backup under `.handoff/backups/`, which `--rollback` restores. Beginner-safe defaults are fixed rather than asked: current project scope, `.git/info/exclude`, no persistent routing block, generated Claude agents, automatic smoke. Advanced callers use `handoff-setup.py` directly for global scope or explicit overrides.
6. **Smoke.** Codex identities verify through the delegate dry-run chain. Claude identities start a fresh, tool-free, non-persistent session on the selected model and effort. Copilot identities take the dry-run chain plus one no-tool `copilot -p` run that asks the CLI whether the account can use the configured pair. Only a passing check writes `verified = true` with a shared `verified_at`. A failed check leaves `verified = false` and says so; apply stays applied.

Then `/agent-handoff tryout` runs one small real task per identity and reports which are live on the models chosen. Smoke is the installation probe; tryout is the proof that an identity can complete its intended work.

### Preset matrices

`None` means the codex model must be detected or supplied.

| Identity | balanced | quality | cost |
| --- | --- | --- | --- |
| `deep_reasoner` | claude / opus / high | claude / opus / high | codex / detected / xhigh |
| `fast_worker` | codex / detected / high | claude / opus / high | codex / detected / medium |
| `arbiter` | codex / detected / xhigh | codex / detected / xhigh | claude / sonnet / high |
| `e2e_specifier` | codex / detected / xhigh | claude / opus / high | codex / detected / high |
| `e2e_verifier` | codex / detected / high | codex / detected / high | codex / detected / medium |

### Rules the setup surfaces hold to

- **No silent fallback.** Effort may be adjusted to an advertised value only while the user is changing backend or model in the UI. Apply and smoke surface an unsupported combination and never swap a model quietly.
- **`verified` means a CLI answered**, not that a file was written. Re-applying the same backend, model, and effort preserves an existing verification; changing any of the three clears it. Changing only the permission posture does not, because smoke checks a model-and-effort pair, not a posture.
- **User-owned files are read-only to this flow.** Generated Claude agents stay namespaced (`handoff-deep-reasoner`, …) and never overwrite a user's own `deep-reasoner.md`. The managed routing block writes only inside its own markers, and only when explicitly enabled.
- **Uninstall removes only what Handoff generated.** `--uninstall` drops `handoff-*` agent files whose hash still matches `.handoff/.generated-manifest` — a file the user hand-edited since generation is left in place and reported as skipped — plus a structurally valid managed routing block. Config is untouched unless `--remove-config`, which clears only the identity sections.

## The configuration document

### Example

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

### Fields

| Path | Type | Required | Meaning |
|---|---|---:|---|
| `schema_version` | integer | yes | Must be `2`. |
| `revision` | non-negative integer | yes, reserved | Reserved for later optimistic concurrency checks; the current engine does not compare or increment it. |
| `hosts.claude_code` | table | per configured identity | The owned namespace. The `hosts.*` nesting is retained so configs written by earlier versions keep loading. |
| `hosts.claude_code.identities.<identity>` | table | per configured identity | `<identity>` is `deep_reasoner`, `fast_worker`, `arbiter`, `e2e_specifier`, or `e2e_verifier`. The last two are optional. |
| `hosts.claude_code.identities.<identity>.backend` | string enum | per configured identity | Required execution CLI for this identity's delegated jobs: `claude`, `codex`, or `copilot`. All three are first-class; the value decides which CLI `delegate-codex.sh` invokes. |
| `hosts.claude_code.identities.<identity>.model` | string | per configured identity | Non-empty model name or alias passed to the selected backend. On `copilot`, `auto` is refused at submit — name a concrete model. The wizard offers the account's entitled models rather than a text field. |
| `hosts.claude_code.identities.<identity>.effort` | string | per configured identity | Non-empty reasoning effort passed to the selected backend. Efforts are per CLI, never one shared enum: codex takes `minimal|low|medium|high|xhigh|max|ultra`, claude takes `low|medium|high|xhigh|max`, copilot takes `none|minimal|low|medium|high|xhigh|max`. A pair written outside the wizard surfaces as the CLI's own error rather than being silently downgraded. |
| `hosts.claude_code.identities.<identity>.permission_mode` | string enum | no | `default` or `allow-all`; absence resolves to `default` after the per-field merge. |
| `hosts.claude_code.identities.<identity>.verified` | boolean | no | Whether a smoke test or real run verified the identity. |
| `hosts.claude_code.identities.<identity>.verified_at` | string | no | Verification timestamp supplied by the caller. |
| `routing.always_on_host_rules` | boolean | no | Whether setup writes a persistent routing block; default `false`. |
| `review.spec_max_rounds` | integer ≥ 1 | no | Spec review passes (`deep_reasoner` reads the plan) before a still-declined blocking finding goes to the arbiter. Default `1`. |
| `review.implementation_max_rounds` | integer ≥ 1 | no | Driver review passes on a delegated diff (the original job plus each `resume`) before open findings go to the arbiter. Default `3`. |

Every configured identity requires `backend`, `model`, and `effort`. Backend is validated by `handoff-config.py`; model and effort compatibility is checked by the setup and smoke layer.

`deep_reasoner` no longer carries a spec-review toggle. Until 3.8.0 it had `auto_review_spec`; spec review now runs on every plan, and the parser drops the retired key on read, so an old config still loads and the next write removes it.

### Version skew

Each of these fails closed in the safe direction: an older engine refuses a file it cannot honour rather than running an identity on the wrong CLI or silently ignoring one.

| A config carrying | On a pre-release engine | Because |
| --- | --- | --- |
| `e2e_specifier` / `e2e_verifier` | pre-3.2 refuses | `validate_config` raises on an identity name it does not recognise |
| `backend = "copilot"` | pre-3.7 refuses | `validate_config` gates `backend` on the known tuple |
| `[review]` | pre-3.8 ignores it | an unknown section round-trips rather than failing |

`schema_version` stays `2` through all of this: adding identity names or a section does not change the document shape.

### Ownership and deterministic writes

The writer may rewrite only `[hosts.claude_code.identities.*]` sections and `[review]`. Everything else round-trips byte-for-byte. Owned identity sections are emitted in identity order (`deep_reasoner`, `fast_worker`, `arbiter`, `e2e_specifier`, `e2e_verifier`) and field order: `backend`, `model`, `effort`, `permission_mode`, `verified`, then `verified_at`. `[review]` is emitted as `spec_max_rounds`, then `implementation_max_rounds`, replaced where it stands or appended at the end of the file when absent. Strings are double-quoted. Repeating the same write produces identical bytes.

Comments and formatting inside an owned section are intentionally not retained. All unowned chunks keep their original order and bytes, including comments and line endings.

### Schema v1 migration

Schema v1 is never converted silently. A file with `schema_version = 1`, or with any `hosts.<host>.roles.*` section even if its version says otherwise, fails closed in `resolve`, `get`, `set`, and `validate`. The error includes the configuration path and this instruction:

> Detected a schema v1 config. Rerun `/agent-handoff config` to upgrade (setup replaces it with a schema v2 document and backs up the old file).

The setup wizard treats a v1 file as a blank starting point: the preview shows the full replacement, the apply backs the original up under `.handoff/backups/`, and `--rollback` restores it. Old v1 values are not carried into the new document; pick them again in the wizard if you still want them.

### Concurrency and atomicity

A write creates `.config.lock` in the directory containing `config.toml` using atomic `os.mkdir`. A lock directory older than 15 seconds is treated as abandoned and reclaimed. While holding the lock, the writer reads the latest file, changes its owned sections, writes a same-directory temporary file, and commits with `os.replace`.

A lock younger than that fails closed straight away, naming the lock directory to remove if no writer is actually active.

`revision` remains reserved for a later defense-in-depth optimistic concurrency check and has no concurrency behavior in schema v2.

### Supported TOML subset

The parser supports bare keys, double-quoted strings, integers, booleans, standard table headers, and `#` comments. It is not a general TOML parser. Sections it does not own are never parsed, only carried across a write byte-for-byte, so they may use syntax outside this subset.

The following constructs fail closed with a line number, character position, and a pointer back to this section:

- inline tables (`value = { ... }`);
- multiline strings;
- datetime values;
- array-of-tables headers (`[[...]]`) naming `[routing]`, `[review]`, or an owned identity section (elsewhere they are treated as an unowned section and preserved);
- dotted-key assignments (`a.b = ...`).

The engine parses only top-level schema metadata, `[routing]`, `[review]`, and the owned identity sections. This boundary lets any future unknown section round-trip without reformatting.

### CLI

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

The `set` command retains `--role` as its identity selector and accepts the optional e2e identities alongside the core three. `set-review` writes the `[review]` section; pass either flag or both, and a value below 1 is refused without writing. `--override` targets identity fields only. `--backend` is required when creating an identity and may be omitted on update to preserve the current value. `get` and `resolve` include `backend` in each configured identity.

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

## Copilot model catalogue (v3.8.1)

A `copilot` identity's model is chosen from a list, not typed. `/agent-handoff config`
reads the models the authenticated account is entitled to and offers those, the way it
already offers Codex models from `model/list` and Claude aliases from `claude --help`.

**Source.** `gh auth token` for the bearer, then a read of the Copilot entitlement
endpoint at `api.githubcopilot.com`. The Copilot CLI publishes no catalogue of its own:
it has no model-list subcommand, and a wrong `--model` is refused without naming the
alternatives. The entitlement API is the only list available without adding a Node
dependency for the Copilot SDK.

**Filter.** An entry is offered when `model_picker_enabled` is exactly `true` and it
carries either no `policy` or `policy.state = "enabled"`. The endpoint also returns
embedding and legacy chat models that no coding session can use. The reader fails closed
on anything it cannot parse: a field present in an unexpected shape drops that entry,
because an unreadable constraint is not an absent one. A single bad entry costs that entry
and nothing else — this runs during controller construction, so an exception escaping here
would take the Claude and Codex lists down with it.

**Effort.** Each entry's `capabilities.supports.reasoning_effort` becomes that model's
effort list, intersected with the CLI enum, so the effort control offers only what the
chosen model takes. This is the same per-model narrowing Codex entries already carry, and
the served list is checked again before a preview is built. An entry reporting no
reasoning efforts at all keeps the full CLI superset rather than becoming unconfigurable.
An entry that reports efforts none of which Handoff can pass is dropped instead: reporting
a constraint nothing satisfies is not the same as reporting none.

**When the catalogue cannot be read** (no `gh`, no network, or a data-residency tenant
serving its catalogue from a per-tenant host), the wizard offers no Copilot model and
names the fix, which says to start the wizard again rather than to refresh: the page holds
the snapshot it was opened with. It does not fall back to a typed name. A Copilot identity
already in the config keeps its model and stays selectable, so reopening the page offline
cannot quietly rewrite a working config — but it offers only the effort already
configured, because nothing available knows what else that model accepts. Preserving a
working pair is not permission to configure an unchecked one.

**What this replaced.** Apply used to run each configured Copilot pair past the real CLI
and refuse to write a config the CLI rejected. The catalogue answers the same question
without starting a session, so that gate is gone and `validate_copilot_pair` now runs on
the smoke path only. On copilot the served catalogue is now the list of valid model names,
and a request naming anything else is refused before a preview is built, so the wizard's
own API cannot write a pair nothing checked. `auto` is exempt from that refusal only so
the engine's reasoned message is what the user reads; it is rejected either way. The pair
check itself remains on the smoke path, which is what sets `verified`, and
`/agent-handoff tryout` still runs a real task per identity.

**Limits.** The endpoint is not a documented GitHub API contract, and Handoff does not
discover a tenant-hosted one. The token it reads with never reaches the served page state
or a log line. A failed read is not fatal to the rest of the wizard, and the read spawns no
`copilot` process, so opening the page still costs no premium request.
