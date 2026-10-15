# Agent identities, configuration, and setup

## Context

An identity is a capability tier with a concrete home: a `backend`, a `model`, an `effort`, and a `permission_mode`. When the driver splits work it asks one question per task row: *which capability does this need?* The identity's configuration answers the rest — which CLI executes the job, on which model, at which reasoning effort, under which permission posture, and therefore on which meter.

A task row names a tier, never a vendor. Nothing in a prompt, a packet, or a mid-run judgment call can move work onto a different CLI, because the mapping from tier to CLI lives in one file that a user can read and a script enforces.

This document is the design of record for that layer: the identity model, the configuration document, the engine that reads and writes it, and the setup flow that fills it in. The prose contracts agents load are `references/setup.md` (the wizard) and `references/tryout.md` (the proof pass). The same material drawn for a human reader is the user guide's *Agent identities and setup* section, with `docs/user-guide/diagrams/identities.svg` and `docs/user-guide/diagrams/setup-flow.svg`.

### Scope

Covered: the five identities and what each takes, the resolution chain from a task row to a running job, the configuration schema and its ownership rules, and the setup wizard (detect, preset, preview, apply, smoke, rollback, uninstall).

| Out of scope | Where it lives |
| --- | --- |
| Each CLI behind a `backend` value: binary discovery, command lines, effort enums, what each `permission_mode` maps to, model catalogues, CLI install and update prompts, smoke per backend | `docs/specs/design_cli-backends.md` |
| Plan, split, delegate, monitor, review: what the identities are *used for* | `docs/specs/design_agent-handoff.md` |
| The job primitive itself (`submit`/`status`/`result`/`resume`/`cancel`/`cleanup`) | `scripts/delegate-codex.sh` header comment |
| Session evidence, transcripts, receipts | `docs/specs/design_agent-handoff-evidence.md` |
| What the two round caps *do* once resolved | `docs/specs/design_agent-handoff.md`, *The two review gates, and where they escalate* |
| The acceptance gate the optional identities serve | `references/e2e-gauntlet.md`, `docs/verdict-schema.json` |

### The invariant everything here rests on

**The identity's configured `backend` decides which CLI runs a job — always.** All five values — `claude`, `codex`, `copilot`, `cursor`, `antigravity` — are first-class execution channels. `backend = "claude"` is not a subagent-only marker and `backend = "copilot"` is not a second-class one: `delegate-codex.sh --role <identity>` runs any of them as a background job with the same jobId, job directory, monitoring, fix-round `resume`, worktree lifecycle, and receipt evidence.

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
  → codex exec --json | claude --print --output-format stream-json | copilot -p --output-format json | cursor-agent -p --output-format stream-json | agy -p --output-format stream-json
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
| `model = "auto"` on copilot or cursor, and an empty or `auto` model on antigravity | An identity is a deliberate triple; `auto` hands the model choice back to the vendor per request, so the receipt would record what the vendor picked rather than what the repo configured | `delegate-codex.sh` submit, and `validate_backend_models` at setup; the antigravity message names `handoff-config.py set` |

The effort enums, and why cursor and antigravity take only `model`, are in `docs/specs/design_cli-backends.md`, *Effort and model*.

## Locations and precedence

Values resolve from highest to lowest priority:

1. Session override supplied by the current task (`resolve --override`).
2. `<repo>/.handoff/config.toml`.
3. `${XDG_CONFIG_HOME:-$HOME/.config}/handoff/config.toml`.
4. Built-in defaults.

Project and global files use the same schema. Built-in defaults provide schema metadata and the default review caps, and leave every identity unset. Model presets belong to setup and are not duplicated in the engine. Identity values live in these two files and nowhere else — never in a prompt, a packet, or a doc.

The review caps have no session override, because `delegate-codex.sh resume` enforces them from its own `resolve` call. A cap the driver believed in but the enforcing process never saw would be worse than no cap.

## Key files

| File | What it owns | What it must not do |
| --- | --- | --- |
| `scripts/handoff-config.py` | the pure read/write/resolve/validate engine; owns the bare identity sections (`[deep_reasoner]` and the other four) and `[review]`, and reads the pre-3.9.2 `[hosts.claude_code.identities.*]` headers | reformat or drop anything else: comments and unknown sections (including stale `hosts.codex.*` blocks from the dual-host era and a `[routing]` section written before 3.9.5) round-trip byte-for-byte |
| `scripts/handoff-setup.py` | the plan/preview/apply/smoke/rollback/uninstall engine; presets, detection, agent generation, the managed routing block | write non-atomically, or write without a backup |
| `scripts/handoff-setup-ui.py` | the localhost-only single-page wizard, whose page is `assets/setup-ui.html`; probes the CLIs for model and effort lists, and reads each worker CLI's version and newest release | implement a second write path; every preview and write delegates to `handoff-setup.py` |
| `scripts/delegate-codex.sh` | turning `--role` into a running job on the configured CLI | accept a per-job override that contradicts the config |
| `references/setup.md` | the wizard contract an agent loads | ask the setup matrix through repeated chat questions when a browser is available |
| `references/tryout.md` | the post-install proof pass | substitute a model or backend to make a row pass |

Configs written before 3.9.2 use a longer header for identity sections. They still load, and *Legacy section headers (3.9.2)* says what happens to them.

## Setup

`/agent-handoff config` opens a page bound to `127.0.0.1` with a per-run token. Six steps, and everything before apply is reversible.

1. **Detect.** Which CLIs are installed, their versions, the existing config, and each backend's model and effort lists. Every list is read from the CLI or the account itself, and nothing is a hardcoded guess. A CLI that is missing or outdated gets an install or update prompt. Each backend's source, and how a CLI is told apart from a binary that shares its name, are in `docs/specs/design_cli-backends.md`.
2. **Pick a preset.** `balanced` (default), `quality`, or `cost`. Changing any control switches the matrix to `custom` internally; `custom` mode requires an explicit backend, model, and effort for every selected identity. A codex preset model is detected from `${CODEX_HOME:-$HOME/.codex}/config.toml` rather than guessed. If detection fails, setup says so and names the flag instead of inventing a model name.
3. **Tune.** Per identity: backend → model → effort → permission. Switching backend or model immediately constrains effort to that selection's advertised values. The same page carries the two review caps, because they belong to the configuration and not to a prompt.
4. **Preview.** `handoff-setup.py --preview` prints exact target paths and unified diffs. Any control change invalidates the preview, and the UI cannot apply a payload that no longer matches its latest one.
5. **Apply.** One atomic write with a backup under `.handoff/backups/`, which `--rollback` restores. Beginner-safe defaults are fixed rather than asked: current project scope, `.git/info/exclude`, no persistent routing block, generated Claude agents, automatic smoke. Advanced callers use `handoff-setup.py` directly for global scope or explicit overrides.
6. **Smoke.** Each identity gets its backend's check: the delegate dry-run chain on codex, a fresh tool-free session on claude, and on copilot, cursor, and antigravity the dry-run chain plus one small real run on the configured model (*Smoke* in `docs/specs/design_cli-backends.md`). Only a passing check writes `verified = true` with a shared `verified_at`. A failed check leaves `verified = false` and says so; apply stays applied.

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
- **Uninstall removes only what Handoff generated.** `--uninstall` drops `handoff-*` agent files whose hash still matches `.handoff/.generated-manifest` — a file the user hand-edited since generation is left in place and reported as skipped — plus a structurally valid managed routing block. Config is untouched unless `--remove-config`, which clears only the identity sections, in either header form.

## The configuration document

### Example

```toml
schema_version = 2
revision = 0

[deep_reasoner]
backend = "claude"          # claude | codex | copilot | cursor | antigravity — which CLI executes
model = "opus"
effort = "high"
permission_mode = "default" # default | allow-all
verified = false

[fast_worker]
backend = "codex"
model = "gpt-5.6-sol"
effort = "medium"
verified = false

[arbiter]
backend = "codex"
model = "gpt-5.6-sol"
effort = "xhigh"
verified = false

# Optional. Setup writes the two sections below only under --with-e2e.
[e2e_specifier]
backend = "codex"
model = "gpt-5.6-sol"
effort = "xhigh"
verified = false

[e2e_verifier]
backend = "codex"
model = "gpt-5.6-sol"
effort = "high"
verified = false

[review]
spec_max_rounds = 1
implementation_max_rounds = 3
```

### Fields

| Path | Type | Required | Meaning |
|---|---|---:|---|
| `schema_version` | integer | yes | Must be `2`. |
| `revision` | non-negative integer | yes, reserved | Reserved for later optimistic concurrency checks; the current engine does not compare or increment it. |
| `<identity>` | table | per configured identity | A top-level table. `<identity>` is `deep_reasoner`, `fast_worker`, `arbiter`, `e2e_specifier`, or `e2e_verifier`. The last two are optional. Files written before 3.9.2 head this table `hosts.claude_code.identities.<identity>`; see *Legacy section headers (3.9.2)*. |
| `<identity>.backend` | string enum | per configured identity | Required execution CLI for this identity's delegated jobs: `claude`, `codex`, `copilot`, `cursor`, or `antigravity`. All five are first-class; the value decides which CLI `delegate-codex.sh` invokes. |
| `<identity>.model` | string | per configured identity | Non-empty model name or alias passed to the selected backend. `auto` is refused on `copilot`, `cursor`, and `antigravity`, and `antigravity` needs a whole catalogue id (`docs/specs/design_cli-backends.md`, *Effort and model*). The wizard offers account models rather than a text field. |
| `<identity>.effort` | string | per configured identity | Non-empty reasoning effort the selected backend accepts. Efforts are per CLI, never one shared enum; cursor and antigravity take only `model`, set by the model id (`docs/specs/design_cli-backends.md`, *Effort and model*). A pair written outside the wizard surfaces as the CLI's own error rather than being silently downgraded. |
| `<identity>.permission_mode` | string enum | no | `default` or `allow-all`; absence resolves to `default` after the per-field merge. What each value means on each CLI is in `docs/specs/design_cli-backends.md`, *Permission posture*. |
| `<identity>.verified` | boolean | no | Whether a smoke test or real run verified the identity. |
| `<identity>.verified_at` | string | no | Verification timestamp supplied by the caller. |
| `review.spec_max_rounds` | integer ≥ 1 | no | Spec review passes (`deep_reasoner` reads the plan) before a still-declined blocking finding goes to the arbiter. Default `1`. |
| `review.implementation_max_rounds` | integer ≥ 1 | no | Driver review passes on a delegated diff (the original job plus each `resume`) before open findings go to the arbiter. Default `3`. |

Every configured identity requires `backend`, `model`, and `effort`. Backend is validated by `handoff-config.py`; model and effort compatibility is checked by the setup and smoke layer.

`deep_reasoner` no longer carries a spec-review toggle. Until 3.8.0 it had `auto_review_spec`; spec review now runs on every plan, and the parser drops the retired key on read, so an old config still loads and the next write removes it.

### Version skew

Most rows below fail closed. An older engine refuses a file it cannot honour, so it never runs an identity on the wrong CLI or drops one without a word. The bare-header row is the exception.

| A config carrying | On a pre-release engine | Because |
| --- | --- | --- |
| `e2e_specifier` / `e2e_verifier` | pre-3.2 refuses | `validate_config` raises on an identity name it does not recognise |
| `backend = "copilot"` | pre-3.7 refuses | `validate_config` gates `backend` on the known tuple |
| `backend = "cursor"` | pre-3.9 refuses | `validate_config` gates `backend` on the known tuple |
| `backend = "antigravity"` | pre-3.10 refuses | `validate_config` gates `backend` on the known tuple. The same engine's `make-receipt.py` counts an antigravity job as codex, so scripts and flow prose ship together |
| `[review]` | pre-3.8 ignores it | an unknown section round-trips rather than failing |
| bare `[<identity>]` headers | pre-3.9.2 ignores them | the old engine reads only prefixed headers. To it, a bare section is an unknown section: it round-trips, and its identity is invisible. If no other layer configures the role, `delegate-codex.sh` refuses it. If the global config has a legacy-form identity, the old engine runs the global values, and `config_source` still says `project`. That case does not fail closed. |

`schema_version` stays `2` through all of this. New identity names and a new section left the document shape alone. 3.9.2 renames the identity headers, which is why its row does not fail closed. A pre-3.9.2 engine can also write an identity into a file that already has bare headers. That identity then exists in both forms, and a 3.9.2 engine refuses the file until someone deletes one of the two sections.

### Ownership and deterministic writes

The writer rewrites identity sections in either header form, plus `[review]`. Everything else round-trips byte-for-byte. Owned identity sections are emitted bare, in identity order (`deep_reasoner`, `fast_worker`, `arbiter`, `e2e_specifier`, `e2e_verifier`) and field order: `backend`, `model`, `effort`, `permission_mode`, `verified`, then `verified_at`. `[review]` is emitted as `spec_max_rounds`, then `implementation_max_rounds`, replaced where it stands or appended at the end of the file when absent. Strings are double-quoted. Repeating the same write produces identical bytes.

Comments and formatting inside an owned section are intentionally not retained. A comment line just before the next header belongs to the owned chunk above it, so it is not kept either. All unowned chunks keep their original order and bytes, including comments and line endings.

### Schema v1 migration

Schema v1 is never converted silently. A file with `schema_version = 1`, or with any `hosts.<host>.roles.*` section even if its version says otherwise, fails closed in `resolve`, `get`, `set`, and `validate`. The error includes the configuration path and this instruction:

> Detected a schema v1 config. Rerun `/agent-handoff config` to upgrade (setup replaces it with a schema v2 document and backs up the old file).

The setup wizard treats a v1 file as a blank starting point: the preview shows the full replacement, the apply backs the original up under `.handoff/backups/`, and `--rollback` restores it. Old v1 values are not carried into the new document; pick them again in the wizard if you still want them.

### Legacy section headers (3.9.2)

Before 3.9.2, identity sections carried a leftover dual-host prefix: `[hosts.claude_code.identities.deep_reasoner]`. From 3.9.2 they are bare top-level tables: `[deep_reasoner]`. Fields, field order, `[review]`, `[routing]` and `schema_version = 2` are unchanged.

- **On read.** A file with prefixed headers loads with the same values, and a read never rewrites it. That covers `resolve`, `get`, `validate`, delegation and opening the wizard.
- **On write.** The next write re-emits every identity section bare, in the place where the first one stood. The writes are `handoff-config.py set` and `set-review`, setup apply, smoke's verification write, and `--uninstall --remove-config`, which clears both forms. Every other section keeps its bytes, including the preamble and stale `hosts.codex.*` blocks.
- **Keys.** `get` and `resolve` keys drop the prefix too: `deep_reasoner.backend`. The old key `hosts.claude_code.identities.deep_reasoner.backend` no longer resolves, and no alias exists. Validation messages read `deep_reasoner.backend must be one of ...`.
- **Duplicates.** A file that defines one identity twice is refused, and the error names both headers. That includes one identity defined in both forms. The usual cause is a pre-3.9.2 engine writing to an already migrated file. Delete one of the two sections by hand. Two different identities in different forms (`[deep_reasoner]` plus `[hosts.claude_code.identities.fast_worker]`) load fine.
- **Typos.** A legacy header that names an unknown identity, such as `[hosts.claude_code.identities.deep_reasonr]`, is refused. A misspelt bare header such as `[deep_reasonr]` is an unknown section. It round-trips untouched, and that identity is absent from that file. Resolution uses the next layer instead: if the global config defines the identity, the global values run. `delegate-codex.sh` refuses the role only when no layer configures it. Before 3.9.2, `validate` caught every misspelt identity header. Now it catches only the legacy form.
- **Older engines.** An engine older than 3.9.2 cannot see the bare sections. See *Version skew*.
- **Schema v1.** `[hosts.<host>.roles.*]` is still refused exactly as in *Schema v1 migration*.

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
- array-of-tables headers (`[[...]]`) naming `[review]` or an owned identity section (elsewhere they are treated as an unowned section and preserved);
- dotted-key assignments (`a.b = ...`).

The engine parses only top-level schema metadata, `[review]`, and the owned identity sections. This boundary lets any future unknown section round-trip without reformatting.

### CLI

Run from the repository root:

```sh
python3 scripts/handoff-config.py --scope project init
python3 scripts/handoff-config.py --scope project validate
python3 scripts/handoff-config.py --scope project get deep_reasoner.backend
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
