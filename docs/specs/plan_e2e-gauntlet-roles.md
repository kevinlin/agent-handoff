# E2E Gauntlet Roles Implementation Plan

**Goal:** Add `e2e_specifier` and `e2e_verifier` as optional Handoff identities, with a worktree lifecycle and a validated pass/fail verdict artifact, so a user-observable change can be acceptance-tested end to end before it merges.

**Architecture:** Two new identities append to the existing `IDENTITIES` tuple; a `--with-e2e` flag decides whether setup writes them, so existing three-identity configs stay complete. `delegate-codex.sh` grows a worktree lifecycle pinned to an immutable base commit. A JSON verdict artifact plus a validator with cross-field rules makes the verifier's PASS mechanically checkable instead of self-reported.

**Tech Stack:** Python 3 standard library only (no third-party imports anywhere in this repo), Bash, `unittest`. No new dependencies.

**Spec:** [docs/specs/design_e2e-gauntlet-roles.md](docs/specs/design_e2e-gauntlet-roles.md) — read it alongside this plan. Its "Rejected review findings" section records what an independent review asked for and what was deliberately not done.

## Implementation record

Shipped 2026-08-24 as v3.2.0. All nine tasks complete; full CI gate green. Four deviations from the plan as written:

- **Tasks 1 and 2 landed as one commit.** Widening `IDENTITIES` alone leaves `handoff-setup.py` indexing `PRESETS` rows that do not exist until Task 2, so Task 1 could not be committed green on its own.
- **Task 1's `test_three_identity_document_round_trips_byte_for_byte` was wrong** and was replaced. `update_host` canonicalises identity sections (LF line endings, drops inline field comments) by design, so that test fails on unmodified `main`, independent of the widening. The replacement asserts the invariant the plan's own comment describes: a three-identity mapping emits exactly the three core sections in order, with nothing added for the absent optional ones.
- **Two engine sites the plan's loop audit missed.** The agent-path loop in `build_plan` indexed `desired[identity]` while iterating all five paths, so an unconfigured optional identity raised `KeyError` instead of taking the removal branch; it now reads `desired.get(identity, {})`. `render_managed_block` / `update_managed_block` needed the identity set threaded through them, since the routing block is now built from what is configured rather than from a constant.
- **`--apply` and `--smoke` are mutually exclusive**, so the optional-identity smoke test runs them as two calls rather than one.

Orphan repairs beyond the plan's Step 4: three assertions in `test_handoff_setup.py` iterated `IDENTITIES` over a configured-identities mapping, and `setup.md` / `tryout.md` carried "all three identities" sentences the widening made wrong.

Pre-existing and untouched: `test_receipt.MakeReceiptTests.test_start_marker_is_what_the_receipt_measures` flakes on a second boundary (`0min 00sec` vs `0min 01sec`), unrelated to this feature.

- 2026-09-08 — **Compacted post-implementation.** Removed step-by-step tasks, file-by-file diffs, code snippets, and verification commands now that the feature has shipped. Preserved Goal, Design Decisions, Critical Files summary, and follow-ups. Original plan recoverable via git history.

## Global Constraints

- **This repo is English-only.** `scripts/english-only-scan.py` fails CI on any CJK character in any tracked file, including UI strings and trigger phrases.
- **Python 3 standard library only.** No third-party imports in `scripts/` or `tests/`.
- **Prose files are product surface.** `SKILL.md`, `README.md`, and `references/*.md` are gated by `scripts/check-skill-repo.sh`. Adding a top-level doc means updating the doc, the README File Map, and the required-file list in that script, or CI fails.
- **Risky command text is scanned.** `git reset --hard`, `rm -rf`, and `--force` in docs trigger warnings; in `test-prompts.json` such text must sit in a `must_not` list.
- **Identity values live only in config files** — `.handoff/config.toml` or `~/.config/handoff/config.toml` — never in prompts or docs.
- **Version strings bump together:** `SKILL.md` frontmatter, README badges, `CHANGELOG.md`. Target version for this feature: `3.2.0` (current: `3.1.0`).
- **Receipt schema stays at v4.** `$id` remains `handoff.receipt.v4` and `receipt_schema_version` remains `4`. Only the `roles_used.role` enum widens.
- **Config schema stays at v2.** `schema_version = 2` is unchanged; adding identities does not change the document shape.
- **Worktree paths:** `<repo>/.handoff/worktrees/<jobId>`. Verified to work despite `.handoff/` being gitignored — `git worktree add` does not consult ignore rules for its target path.

---

### Task 1: Optional identities in the config engine

Added `CORE_IDENTITIES`, `OPTIONAL_IDENTITIES`, and the widened `IDENTITIES` tuple to `scripts/handoff-config.py`, giving every downstream module a single import point for identity names. The split between core and optional ensures existing three-identity configs remain complete without any new sections appearing. Fixed the setup test harness (`tests/test_handoff_setup.py`) where `custom_args` iterated the widened tuple and raised `KeyError` on the new names.

**Files:** `scripts/handoff-config.py`, `tests/test_handoff_config.py`, `tests/test_handoff_setup.py`

---

### Task 2: `--with-e2e` in the setup engine

Extended `scripts/handoff-setup.py` with `--with-e2e` / `--no-with-e2e` (default off) on `plan`, `preview`, and `apply` actions. Presets gained both optional identity rows. Thirteen mapping loops were audited: loops over a mapping now iterate that mapping's keys in `IDENTITIES` order via a new `ordered()` helper; the "not configured" gate was scoped to core identities only so an absent optional identity is a deliberate state, not a broken setup. The `ROUTING_POLICY` constant was replaced with a `routing_policy(identities)` function that builds routing lines from what is configured rather than from a constant. Updated `docs/config-schema.md` to document the five-identity set while keeping `schema_version = 2`.

**Files:** `scripts/handoff-setup.py`, `tests/test_handoff_setup.py`, `docs/config-schema.md`

---

### Task 3: E2E add-on in the setup UI

Added the two optional identity cards to `scripts/handoff-setup-ui.py` behind a collapsed "E2E acceptance testing" section with a checkbox toggle. `normalize_payload` strips optional identities when the toggle is off so the engine never receives settings the user did not configure; `engine_arguments` always states the flag explicitly for reproducibility.

**Files:** `scripts/handoff-setup-ui.py`, `tests/test_handoff_setup_ui.py`

---

### Task 4: Worktree lifecycle in `delegate-codex.sh`

Added `submit --worktree <branch> [--base <commit-ish>]` to pin jobs to an immutable base SHA in a dedicated worktree under `.handoff/worktrees/<jobId>`. The base is resolved to an immutable SHA before anything is created — a branch name can advance between cutting the worktree and reading the verdict, and then the verdict names a commit nobody tested. Added `cleanup <jobId>` (exit 0 when removed or absent, exit 1 on uncommitted changes) to prevent worktree accumulation. `resume` inherits the parent's worktree so fix rounds land in the same tree; `cancel` calls `cleanup`. The role guard was widened to accept `e2e_specifier` and `e2e_verifier`.

**Files:** `scripts/delegate-codex.sh`, `tests/test_delegate_role.py`

---

### Task 5: Verdict artifact and validator

Created `scripts/validate-verdict.py` with cross-field rules that make the verifier's result mechanically checkable: PASS requires exit 0, all scenarios passed, and empty findings; FAIL requires at least one finding and evidence of failure; BLOCKED requires zero passed and a blocker reason. An optional `--expect-scenarios-sha256` check ties the run to the reviewed scenario set so a post-review change voids the run. Created `docs/verdict-schema.json` (JSON Schema draft 2020-12, `$id: handoff.verdict.v1`) documenting that structural validity is necessary but not sufficient — the cross-field rules in the validator are the contract.

**Files:** `scripts/validate-verdict.py`, `docs/verdict-schema.json`, `tests/test_validate_verdict.py`

---

### Task 6: Widen the receipt role enum

Added `e2e_specifier` and `e2e_verifier` to `ROLES` in `scripts/validate-receipt.py` and to the enum in `docs/receipt-schema.json`. `receipt_schema_version` stayed at `4` — the widening is additive, so v4 receipts written before the e2e identities existed remain valid.

**Files:** `scripts/validate-receipt.py`, `docs/receipt-schema.json`, `tests/test_receipt.py`

---

### Task 7: The `references/e2e-gauntlet.md` flow document

Authored the e2e gauntlet flow document covering: the when-to-use criterion (user-observable change at a real interface), worktree protocol (immutable base SHA, worker commits but never merges/pushes/removes), packet self-containment (frozen goal inline because worktrees lack `.handoff/`), specifier and verifier packets with the constraint line replaced by a commit-on-worktree-branch rule, the verdict artifact and its validation, the review-the-tests-first gate with `REVIEWED_SHA`, and the ordering/integration sequence (both jobs cut from `<sha0>`, integration onto the feature branch, verifier pinned to the combined SHA, `main` reached only after PASS plus final driver review).

**Files:** `references/e2e-gauntlet.md`

---

### Task 8: Wire the flow prose

Updated `references/claude-driven.md` to wire the e2e sequence across all five phases: Phase 1 criterion and unconfigured notice, Phase 2 worktree submit form, Phase 3 `depends`-aware monitor loop, Phase 4 review-then-verify sequence with FAIL routed to the original implementer and BLOCKED resolved by the driver, Phase 5 e2e roles in `roles_used`. Added the `depends` column to `references/goal-template.md` so the `/loop` monitor can sequence submissions. Noted the packet constraint override in `references/handoff-template.md` — e2e packets are the only ones that replace the "Do not commit" line.

**Files:** `references/claude-driven.md`, `references/goal-template.md`, `references/handoff-template.md`

---

### Task 9: Skill surface, gates, and version bump

Version bump to 3.2.0 across `SKILL.md`, `README.md`, `CHANGELOG.md`. Added required-file checks for `references/e2e-gauntlet.md` and `docs/verdict-schema.json` to `scripts/check-skill-repo.sh`. Added six e2e test prompts covering the user-observable criterion, internal-refactor skip, unconfigured notice, no-product-code rule, no-merge rule, and semantic-edit void. Updated `CLAUDE.md` architecture section (three identities becomes five, two optional).

**Files:** `SKILL.md`, `README.md`, `CHANGELOG.md`, `CLAUDE.md`, `scripts/check-skill-repo.sh`, `test-prompts.json`

---

## Verification

Confirmed end to end in a scratch repo: `handoff-setup.py --with-e2e --apply` wrote five identities; `resolve` reported all five; a worktree submit created `.handoff/worktrees/<jobId>` with `worktree=`, `branch=`, and 40-character `base_commit=` in meta; `cleanup` removed the worktree and was safe to run twice.
