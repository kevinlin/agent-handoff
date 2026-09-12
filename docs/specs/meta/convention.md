---
active_profile: auto
---

<!--
  Specification convention reference used by the spec-lint skill.

  This file is the canonical naming + structure rule set. It lives in two places:

  1. spec-lint/references/convention.md      — the version bundled with the skill.
                                               Updated when the skill itself is updated.
  2. <spec_root>/meta/convention.md          — the local copy seeded by spec-lint into
                                               the project. This is the per-project
                                               source of truth; edit it to customize
                                               folder names, supported artifact types,
                                               allowed exceptions, the active profile,
                                               etc.

  When spec-lint runs:
  - If <spec_root>/meta/convention.md is missing, the bundled version is copied in.
  - If both exist and differ, the local copy wins for linting decisions and a notice
    is added to the report so the human can choose to refresh.

  The frontmatter `active_profile` controls profile selection:
  - `auto`       (default) — spec-lint detects the profile from filesystem signals.
  - `default`    — the house style described in §3.
  - `kiro`       — Kiro IDE convention (§4).
  - `superpowers`— Superpowers plans convention (§5).
  - `openspec`   — OpenSpec specs + changes convention (§6).
  - `spec-kit`   — GitHub spec-kit numbered-feature convention (§7).
  - `bmad`       — BMad Method planning + implementation artefacts (§8).
  - `gsd`        — GSD `.planning/` workspace convention (§9).

  Multiple profiles can coexist in a single repo (e.g., Kiro for new features and the
  default style for legacy specs). When `active_profile: auto` and more than one
  signal matches, spec-lint lints each profile independently and surfaces both in the
  report.

  Edit the local copy freely. Do not edit this bundled copy from inside a project.
-->

# Specification File Structure and Naming Convention

Spec-driven development produces a small, well-known set of artefacts: requirements (or specs), design (or architecture), plans (or tasks), and execution state. **What** each artefact contains is broadly the same across toolkits; **how** they are named and laid out on disk varies a lot. This file describes seven layouts the spec-lint skill recognises out of the box, plus the rules used to detect and lint each one.

The convention defines **file structure, naming, and intent**. It does not prescribe the body content of each spec — feature templates, acceptance-criteria formats, and decision logs are deliberately out of scope.

---

## 1. Profiles Overview

A **profile** is a named bundle of conventions that fit one toolkit or workflow. Picking the right profile lets spec-lint:

- Walk the right filesystem root.
- Recognise the right filenames as `requirements`, `design`, `plan`, `task`, `index`, or `meta`.
- Apply the right naming rules and anti-patterns.
- Trace coverage along the right layers (which design covers which requirements, which plan covers which design).

Seven profiles ship with the skill:

| # | Profile | Origin | One-line summary |
|---|---|---|---|
| §3 | `default` | This project's house style ("zapac") | One folder per module under `docs/specs/`; files named `<artifact>_<topic>.md`. |
| §4 | `kiro` | [Kiro IDE](https://kiro.dev/docs/specs/) | One folder per feature under `.kiro/specs/`; fixed filenames (`requirements.md`, `design.md`, `tasks.md`, `bugfix.md`). |
| §5 | `superpowers` | [obra/superpowers](https://github.com/obra/superpowers) | Single date-prefixed plan files under `docs/superpowers/plans/`. |
| §6 | `openspec` | [Fission-AI/OpenSpec](https://github.com/Fission-AI/OpenSpec) | Source-of-truth specs at `openspec/specs/<domain>/spec.md` plus change folders at `openspec/changes/<change>/`. |
| §7 | `spec-kit` | [github/spec-kit](https://github.com/github/spec-kit) | Numbered feature folders `specs/###-feature-slug/`, fixed artefact names, `.specify/memory/constitution.md` for project rules. |
| §8 | `bmad` | [BMAD-METHOD](https://github.com/bmad-code-org/BMAD-METHOD) | Planning vs implementation split under `_bmad-output/`; `PRD.md`, `architecture.md`, `epics/`, `sprint-status.yaml`. |
| §9 | `gsd` | [Get Shit Done](https://gsd-build-get-shit-done.mintlify.app) | `.planning/` workspace with uppercase core docs (`PROJECT.md`, `REQUIREMENTS.md`, `ROADMAP.md`, `STATE.md`) and numbered phase/task files. |

Profiles are **not mutually exclusive**. Spec-lint can detect and lint several at once if a project is mid-migration or pairs (say) Kiro for product specs with `default` for ADRs.

---

## 2. Detecting the Active Profile

When `active_profile: auto`, spec-lint scans the repository root and matches each profile's signal set. The first signal listed for a profile is enough to confirm a match.

| Profile | Primary signal (existence implies a match) | Secondary signals |
|---|---|---|
| `kiro` | `.kiro/specs/` directory | `.kiro/steering/{product,tech,structure}.md`, `bugfix.md` files |
| `openspec` | `openspec/specs/` or `openspec/changes/` directory | `openspec/AGENTS.md`, `openspec/project.md` |
| `spec-kit` | `.specify/memory/constitution.md` | `specs/###-*/spec.md` numbered folders, `specs/*/contracts/` |
| `bmad` | `_bmad-output/` directory | `_bmad-output/planning-artifacts/PRD.md`, `_bmad-output/implementation-artifacts/sprint-status.yaml` |
| `gsd` | `.planning/PROJECT.md` | `.planning/{REQUIREMENTS,ROADMAP,STATE}.md`, `.planning/phases/##-*/` |
| `superpowers` | `docs/superpowers/plans/` directory containing date-prefixed `.md` files | `skills/` directory at repo root with `SKILL.md` files |
| `default` | `docs/specs/`, `docs/spec/`, `specs/`, or `spec/` directory | Files matching `<artifact>_<topic>.md` patterns |

**Resolution rules:**

1. If exactly one profile matches, use it.
2. If multiple profiles match, lint each one independently and tag findings with the profile name. Surface the multi-profile state as an `info` finding so the human can decide whether to consolidate.
3. If no profile matches, ask the user. Do not invent a location.
4. To override detection, set `active_profile:` in the local `<spec_root>/meta/convention.md` frontmatter to a specific profile name (or to `auto` to re-enable detection).

Project-level overrides:

- `README.md` or `CLAUDE.md` may name the spec root explicitly (e.g., "design docs in `docs/specs/`"). An explicit pointer always wins over auto-detection.
- A README that names a different folder than the detected profile's canonical root takes precedence; the report should note the mismatch as `info`.

---

## 3. `default` Profile — House Style

This is the project's home-rolled convention. Use it when no toolkit has been adopted, or when migrating off one.

### 3.1 Core rules

1. All specification files live under a single `<spec_root>` directory. Most projects use `docs/specs/`; spec-lint resolves the actual root automatically.
2. The root contains **project-level** specs (overall requirements, overall design, navigation index, and tooling artefacts).
3. Each module or feature has **one folder** under `<spec_root>`.
4. Module and feature folder names use **kebab-case**.
5. Module-level spec files follow the pattern `<artifact-type>_<topic>.md` where `<artifact-type>` ∈ {`requirements`, `design`, `plan`}.
6. Use exactly **one underscore** between the artifact type and the topic. Use **kebab-case** inside the topic.
7. Plan files should link back to the requirements (by ID or section) and design they implement.
8. Do not use generic or transient names (`notes.md`, `todo.md`, `draft.md`, `final.md`, `wip.md`, `temp.md`, `v2.md`).
9. Do not include dates in filenames except in `meta/` artefacts (e.g., dated lint reports).
10. Generated tooling artefacts (lint reports, indices, conventions copies) live under `<spec_root>/meta/`.

### 3.2 Artifact types and intent

| Artifact | Purpose | Where it lives |
|---|---|---|
| **`requirements.md`** (root) | Project-level acceptance criteria, numbered. The contract for what the product does. | `<spec_root>/requirements.md` |
| **`design.md`** (root) | Project-level architecture: tech stack, multi-process layout, cross-cutting decisions. | `<spec_root>/design.md` |
| **`index.md`** (root) | Navigation map of every design and plan grouped by module. The single jump-off point. | `<spec_root>/index.md` |
| **`requirements_<topic>.md`** | Module-internal full requirement spec when the root summary is too coarse. Maps each section back to root requirement IDs. | `<spec_root>/<module>/` |
| **`design_<topic>.md`** | Module-level architecture: components, data model, decisions, resolved issues. | `<spec_root>/<module>/` |
| **`plan_<topic>.md`** | Implementation plan for one module *or* one sub-feature. Multiple plans per module are normal. | `<spec_root>/<module>/` |
| **Supporting files** (`*.json`, `*.yaml`, `*.png`) | API contracts, schemas, mock-ups referenced from a spec. Same folder as the spec that consumes them. | `<spec_root>/<module>/` |
| **`meta/`** | Generated tooling artefacts (lint reports, conventions copies, generated indices). Safe to delete and regenerate. | `<spec_root>/meta/` |

A module always has at least one of {`design_<topic>.md`, `plan_<topic>.md`}. A folder containing only `requirements_<topic>.md` is a sign the design has not been written yet.

### 3.3 Naming decision table

| Need | Location | Filename | Example |
|---|---|---|---|
| Spec navigation index | `<spec_root>/` | `index.md` | `docs/specs/index.md` |
| Project requirements | `<spec_root>/` | `requirements.md` | `docs/specs/requirements.md` |
| Project design | `<spec_root>/` | `design.md` | `docs/specs/design.md` |
| Module requirements (full spec) | `<spec_root>/<module>/` | `requirements_<module>.md` | `workspace-as-folder/requirements_workspace-as-folder.md` |
| Module design | `<spec_root>/<module>/` | `design_<module>.md` | `app-ux/design_app-ux.md` |
| Module implementation plan | `<spec_root>/<module>/` | `plan_<module>.md` | `workspace-packs/plan_workspace-packs.md` |
| Sub-feature plan inside a module | `<spec_root>/<module>/` | `plan_<sub-feature>.md` | `app-ux/plan_keyboard-shortcuts.md` |
| API contract / schema | `<spec_root>/<module>/` | `<contract-name>.{json,yaml}` | `opencode-integration/opencode-api.json` |
| Generated lint report | `<spec_root>/meta/` | `lint-report-YYYY-MM-DD.md` | `meta/lint-report-2026-05-09.md` |
| Local convention reference | `<spec_root>/meta/` | `convention.md` | `meta/convention.md` |

### 3.4 Folder naming rules

Each module or feature lives in exactly one folder: `<spec_root>/<module-or-feature>/`.

Allowed: lowercase letters, hyphens between words (`workspace-as-folder/`, `chat-ux/`), stable product or architecture names.

Disallowed: spaces, underscores, dots, camelCase, PascalCase, dates (`2026-05-feature/`), trailing-version markers (`feature-v2/`, `feature-old/`). Iterate by editing the existing folder; preserve history through git.

### 3.5 File naming rules

Pattern: `<artifact-type>_<topic>.md`.

- `<artifact-type>` is exactly one of `requirements`, `design`, `plan`.
- `<topic>` describes the module, feature, or implementation slice.
- Exactly one underscore between type and topic; kebab-case inside the topic.

**Anti-patterns**

| Anti-pattern | Example | Fix |
|---|---|---|
| Hyphen between type and topic | `plan-keyboard-shortcuts.md` | `plan_keyboard-shortcuts.md` |
| Underscore inside the topic | `plan_keyboard_shortcuts.md` | `plan_keyboard-shortcuts.md` |
| Multiple underscores as separator | `plan__keyboard-shortcuts.md` | `plan_keyboard-shortcuts.md` |
| Generic / transient name | `notes.md`, `todo.md`, `draft.md` | `<artifact>_<topic>.md` |
| Phase or version in filename | `plan_phase1.md`, `plan_v2.md` | Meaningful topic; preserve history via git |
| Bare `plan.md` in a module folder | `workspace-packs/plan.md` | `workspace-packs/plan_workspace-packs.md` |
| Typo in topic | `plan_todo-panel-in-sidebard.md` | `plan_todo-panel-in-sidebar.md` |
| Mixed casing | `plan_KeyboardShortcuts.md` | `plan_keyboard-shortcuts.md` |

Existing legacy files that already use underscores inside the topic may remain temporarily; flag as `warn`, not `error`, so cleanup can be batched.

### 3.6 Layer mapping (for reverse consistency)

| Layer | File |
|---|---|
| Requirements | Closest `requirements_<topic>.md` walking up to `<spec_root>/requirements.md`. |
| Design | Closest `design_<topic>.md` walking up to `<spec_root>/design.md`. |
| Plan | Each `plan_<topic>.md`. |
| Tasks | Inline checkboxes inside the matching plan. |
| State | Task checkboxes; no separate state file. |

### 3.7 Example tree

```text
docs/specs/
├── index.md                              # navigation map
├── requirements.md                       # project-level ACs
├── design.md                             # project-level architecture
├── meta/
│   ├── convention.md                     # local copy of this file
│   └── lint-report-2026-05-09.md         # generated by spec-lint
├── app-ux/                               # module with many sub-feature plans
│   ├── design_app-ux.md
│   ├── plan_about-panel.md
│   ├── plan_arena.md
│   ├── plan_keyboard-shortcuts.md
│   ├── plan_theme-support.md
│   └── plan_user-feedback.md
├── workspace-packs/                      # module with one design + one plan
│   ├── design_workspace-packs.md
│   └── plan_workspace-packs.md
├── workspace-as-folder/                  # module with module-internal requirements
│   ├── requirements_workspace-as-folder.md
│   ├── design_workspace-as-folder.md
│   ├── plan_workspace-as-folder.md
│   ├── plan_file-preview-panel.md
│   └── plan_unify-file-click-behavior.md
└── opencode-integration/                 # module with a supporting API contract
    ├── design_opencode-integration.md
    ├── opencode-api.json
    └── plan_sidecar-opencode-rewrite.md
```

---

<!-- The othe 7 styles are removed to keep this doc lean -->

## 10. Cross-Profile Layer Mapping

This is the same table viewed across all profiles, useful when a project mixes them or when migrating between them.

| Layer | `default` | `kiro` | `superpowers` | `openspec` | `spec-kit` | `bmad` | `gsd` |
|---|---|---|---|---|---|---|---|
| **Project context** | `requirements.md` (root) | `.kiro/steering/{product,tech,structure}.md` | — | `openspec/project.md` | `.specify/memory/constitution.md` | `_bmad-output/project-context.md` | `.planning/PROJECT.md` |
| **Requirements** | `requirements.md` / `requirements_<topic>.md` | `requirements.md` (or `bugfix.md`) | "Goal" section in plan file | `specs/<domain>/spec.md`, change `proposal.md` | `spec.md` | `PRD.md` | `REQUIREMENTS.md` |
| **Design / architecture** | `design.md` / `design_<topic>.md` | `design.md` | "Approach" section in plan file | `changes/<change>/design.md` | `plan.md`, `data-model.md`, `contracts/` | `architecture.md` | phase `*-PLAN.md` |
| **Plan / tasks** | `plan_<topic>.md` | `tasks.md` | "Tasks" section in plan file | `changes/<change>/tasks.md` | `tasks.md` | story files under `epics/` | numbered `*-PLAN.md` |
| **Execution state** | task checkboxes | task checkboxes inside `tasks.md` | task checkboxes inside plan file | archive folder `changes/archive/YYYY-MM-DD-<change>/` | task checkboxes inside `tasks.md` | `sprint-status.yaml` | `STATE.md`, `*-SUMMARY.md`, `*-VERIFICATION.md` |
| **Index / registry** | `index.md` (or `requirements.md` / `README.md`) | folder listing under `.kiro/specs/` | filename listing under `plans/` | `openspec/AGENTS.md` + folder listings | folder listing under `specs/` | bundle layout under `_bmad-output/` | `ROADMAP.md` + `STATE.md` |

When linting a multi-profile repo, run each profile's checks against its own root and emit findings tagged with the profile.

---

## 11. Customizing for a Project

The copy at `<spec_root>/meta/convention.md` is the **per-project source of truth**. Edit it freely:

- **Lock the active profile.** Set `active_profile:` in the frontmatter (e.g., `kiro`) to skip auto-detection.
- **Change spec root.** Spec-lint resolves the root from the README first; tighten this in your local copy if the default probe order is wrong.
- **Add allowed artifact types.** For the `default` profile, add (e.g.) `runbook_<topic>.md` or `adr_<topic>.md` to §3.2 / §3.3.
- **Tighten or relax topic-naming rules.** For example, ban abbreviations or require a glossary entry per topic.
- **Restrict module folder names.** For the `default` profile, fix the allowed list of module folders if you want a closed set.
- **Add project-specific anti-patterns.** Patterns you have seen in past reviews ("never put a screenshot inline; use `assets/`").
- **Drop profiles you do not use.** Trim §4-9 down to the profile(s) you want to enforce; spec-lint will only consider what the local copy describes.

When `spec-lint` runs and detects that the local copy differs from the bundled version, it surfaces an `info` finding so you can decide whether to refresh from the skill or keep your customizations.
