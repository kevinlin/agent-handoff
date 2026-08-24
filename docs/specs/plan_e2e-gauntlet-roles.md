# E2E Gauntlet Roles Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

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

## Global Constraints

- **This repo is English-only.** `scripts/english-only-scan.py` fails CI on any CJK character in any tracked file, including UI strings and trigger phrases.
- **Python 3 standard library only.** No third-party imports in `scripts/` or `tests/`.
- **Prose files are product surface.** `SKILL.md`, `README.md`, and `references/*.md` are gated by `scripts/check-skill-repo.sh`. Adding a top-level doc means updating the doc, the README File Map, and the required-file list in that script, or CI fails.
- **Risky command text is scanned.** `git reset --hard`, `rm -rf`, and `--force` in docs trigger warnings; in `test-prompts.json` such text must sit in a `must_not` list. Do not introduce new occurrences.
- **Identity values live only in config files** — `.handoff/config.toml` or `~/.config/handoff/config.toml` — never in prompts or docs.
- **Version strings bump together:** `SKILL.md` frontmatter, README badges, `CHANGELOG.md`. Target version for this feature: `3.2.0` (current: `3.1.0`).
- **Receipt schema stays at v4.** `$id` remains `handoff.receipt.v4` and `receipt_schema_version` remains `4`. Only the `roles_used.role` enum widens.
- **Config schema stays at v2.** `schema_version = 2` is unchanged; adding identities does not change the document shape.
- **Worktree paths:** `<repo>/.handoff/worktrees/<jobId>`. Verified to work despite `.handoff/` being gitignored — `git worktree add` does not consult ignore rules for its target path.
- **Run the full gate before any commit:** `python3 -m unittest discover -s tests && bash scripts/check-skill-repo.sh .`

---

### Task 1: Optional identities in the config engine

**Files:**
- Modify: `scripts/handoff-config.py:26`
- Test: `tests/test_handoff_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `handoff_config.CORE_IDENTITIES: tuple[str, ...]`, `handoff_config.OPTIONAL_IDENTITIES: tuple[str, ...]`, `handoff_config.IDENTITIES: tuple[str, ...]`. Every later task imports these rather than re-listing names.

- [x] **Step 1: Write the failing tests**

Append to `tests/test_handoff_config.py`:

```python
class OptionalIdentityTests(unittest.TestCase):
    def test_optional_identities_append_after_core(self):
        self.assertEqual(
            ("deep_reasoner", "fast_worker", "arbiter"),
            handoff_config.CORE_IDENTITIES,
        )
        self.assertEqual(
            ("e2e_specifier", "e2e_verifier"),
            handoff_config.OPTIONAL_IDENTITIES,
        )
        self.assertEqual(
            handoff_config.CORE_IDENTITIES + handoff_config.OPTIONAL_IDENTITIES,
            handoff_config.IDENTITIES,
        )

    def test_three_identity_document_round_trips_byte_for_byte(self):
        # Appending identities must not reorder or reformat an existing file.
        original = document()
        parsed = handoff_config.parse_config(original)
        identities = parsed["hosts"][handoff_config.HOST]["identities"]
        self.assertEqual(original, handoff_config.update_host(original, identities=identities))

    def test_optional_identity_round_trips(self):
        text = handoff_config.update_host(
            "",
            identities={
                "e2e_verifier": {
                    "backend": "codex",
                    "model": "gpt-test",
                    "effort": "high",
                    "verified": False,
                }
            },
        )
        self.assertIn("[hosts.claude_code.identities.e2e_verifier]", text)
        parsed = handoff_config.parse_config(text)
        identity = parsed["hosts"][handoff_config.HOST]["identities"]["e2e_verifier"]
        self.assertEqual("codex", identity["backend"])
        self.assertEqual("gpt-test", identity["model"])

    def test_emitted_sections_follow_identity_order(self):
        text = handoff_config.emit_host_sections(
            {
                "e2e_verifier": {"backend": "codex", "model": "m", "effort": "high"},
                "deep_reasoner": {"backend": "claude", "model": "opus", "effort": "high"},
                "e2e_specifier": {"backend": "codex", "model": "m", "effort": "xhigh"},
            }
        )
        positions = [
            text.index("identities.deep_reasoner"),
            text.index("identities.e2e_specifier"),
            text.index("identities.e2e_verifier"),
        ]
        self.assertEqual(sorted(positions), positions)

    def test_override_accepts_optional_identity(self):
        override = handoff_config._parse_override(["e2e_verifier.effort=low"])
        identities = override["hosts"][handoff_config.HOST]["identities"]
        self.assertEqual("low", identities["e2e_verifier"]["effort"])
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_handoff_config.OptionalIdentityTests -v`
Expected: FAIL with `AttributeError: module 'handoff_config' has no attribute 'CORE_IDENTITIES'`.

- [x] **Step 3: Write the minimal implementation**

Replace line 26 of `scripts/handoff-config.py`:

```python
CORE_IDENTITIES = ("deep_reasoner", "fast_worker", "arbiter")
# Optional add-on identities. Appended, never inserted: emit_host_sections
# orders sections by IDENTITIES, so an existing three-identity document must
# keep writing back byte-identically.
OPTIONAL_IDENTITIES = ("e2e_specifier", "e2e_verifier")
IDENTITIES = CORE_IDENTITIES + OPTIONAL_IDENTITIES
```

Nothing else in this file changes. `set --role`, `_parse_override`, `validate_config`, and `emit_host_sections` all read `IDENTITIES` and pick the new names up automatically.

- [x] **Step 4: Repair the setup test harness this change breaks**

`tests/test_handoff_setup.py:custom_args` builds its CLI arguments by iterating
`handoff_setup.IDENTITIES` and indexing `choices[identity]`. Widening the tuple
makes it raise `KeyError: 'e2e_specifier'` in every custom-mode test. Skip the
names a caller did not supply:

```python
        for identity in handoff_setup.IDENTITIES:
            if identity not in choices:
                continue
            backend, model, effort = choices[identity]
```

This is orphan repair caused by Step 3, not an unrelated test change — without
it Task 1 lands the suite red.

- [x] **Step 5: Run the whole suite**

Run: `python3 -m unittest discover -s tests`
Expected: PASS. Not just the config module — Step 3 changes a constant three
other modules read.

- [x] **Step 6: Commit**

```bash
git add scripts/handoff-config.py tests/test_handoff_config.py tests/test_handoff_setup.py
git commit -m "feat(config): add optional e2e identities to the identity tuple"
```

---

### Task 2: `--with-e2e` in the setup engine

**Files:**
- Modify: `scripts/handoff-setup.py` (`PRESETS`, `choose_identities`, `preserve_verification`, `_cli_unavailable`, `build_plan`, `print_plan`, `smoke`, `render_agent`, `ROUTING_POLICY`, `build_parser`, interactive mode)
- Modify: `docs/config-schema.md`
- Test: `tests/test_handoff_setup.py`

**Interfaces:**
- Consumes: `handoff_config.CORE_IDENTITIES`, `handoff_config.OPTIONAL_IDENTITIES`, `handoff_config.IDENTITIES` from Task 1.
- Produces: `handoff-setup.py --with-e2e` / `--no-with-e2e` (default off) on the `plan`, `preview`, and `apply` actions; module-level `CORE_IDENTITIES` and `OPTIONAL_IDENTITIES` re-exports; a module-level helper `ordered(mapping) -> list[str]`.

The engine currently assumes `desired[identity]` exists for every name in `IDENTITIES`. Thirteen loops need auditing. The rule: **a loop over a mapping iterates that mapping's keys in `IDENTITIES` order; a loop that genuinely means "every identity that could exist" keeps `IDENTITIES`.**

- [x] **Step 1: Write the failing tests**

Append to `tests/test_handoff_setup.py`. The existing `SetupTests` class provides
`run_cli(*arguments)` (calls `handoff_setup.main` in-process, returns
`(status, stdout, stderr)`), `claude_args(action, *extra)`, and
`custom_args(choices, action)`. Subclass it rather than building a second harness:

```python
class WithE2eTests(SetupTests):
    def configured(self):
        resolved = handoff_config.resolve_config(self.repo.resolve(), env=self.env)
        return resolved["hosts"][handoff_config.HOST]["identities"]

    def test_default_apply_writes_core_identities_only(self):
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertEqual((0, ""), (status, error))
        identities = self.configured()
        self.assertEqual(
            ["deep_reasoner", "fast_worker", "arbiter"],
            [name for name in handoff_setup.IDENTITIES if name in identities],
        )

    def test_with_e2e_writes_both_optional_identities(self):
        status, _, error = self.run_cli(
            *self.claude_args("--apply", "--mode", "balanced", "--with-e2e")
        )
        self.assertEqual((0, ""), (status, error))
        identities = self.configured()
        self.assertEqual("xhigh", identities["e2e_specifier"]["effort"])
        self.assertEqual("high", identities["e2e_verifier"]["effort"])
        self.assertEqual("codex", identities["e2e_specifier"]["backend"])

    def test_apply_succeeds_without_the_optional_identities(self):
        # The "identities are not configured" gate must be scoped to core:
        # an absent optional identity is a deliberate state, not a broken setup.
        status, _, error = self.run_cli(
            *self.claude_args("--apply", "--mode", "balanced", "--smoke")
        )
        self.assertEqual((0, ""), (status, error))

    def test_status_lists_unconfigured_optional_identities_as_unset(self):
        self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        status, output, _ = self.run_cli(*self.claude_args("--status"))
        self.assertEqual(0, status)
        self.assertIn("e2e_specifier: backend=<unset>", output)
        self.assertIn("e2e_verifier: backend=<unset>", output)

    def test_custom_mode_requires_optional_settings_only_with_the_flag(self):
        core = {
            "deep_reasoner": ("claude", "opus", "high"),
            "fast_worker": ("codex", "gpt-detected", "medium"),
            "arbiter": ("codex", "gpt-detected", "xhigh"),
        }
        status, _, error = self.run_cli(*self.custom_args(core))
        self.assertEqual((0, ""), (status, error))
        status, _, error = self.run_cli(*self.custom_args(core), "--with-e2e")
        self.assertNotEqual(0, status)
        self.assertIn("e2e_specifier", error)

    def test_claude_backend_optional_identity_generates_an_agent(self):
        choices = {
            "deep_reasoner": ("claude", "opus", "high"),
            "fast_worker": ("codex", "gpt-detected", "medium"),
            "arbiter": ("codex", "gpt-detected", "xhigh"),
            "e2e_specifier": ("claude", "sonnet", "high"),
            "e2e_verifier": ("codex", "gpt-detected", "high"),
        }
        status, _, error = self.run_cli(
            *self.custom_args(choices), "--with-e2e", "--write-agents"
        )
        self.assertEqual((0, ""), (status, error))
        agents = self.repo.resolve() / ".claude" / "agents"
        specifier = agents / "handoff-e2e-specifier.md"
        self.assertTrue(specifier.exists())
        self.assertIn("name: handoff-e2e-specifier", specifier.read_text(encoding="utf-8"))
        # backend=codex identities never get a Claude subagent definition
        self.assertFalse((agents / "handoff-e2e-verifier.md").exists())
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_handoff_setup.WithE2eTests -v`
Expected: FAIL — `--with-e2e` is an unrecognized argument.

- [x] **Step 3: Add the presets, the flag, and the ordering helper**

In `scripts/handoff-setup.py`, near the existing `IDENTITIES = handoff_config.IDENTITIES` at line 36:

```python
IDENTITIES = handoff_config.IDENTITIES
CORE_IDENTITIES = handoff_config.CORE_IDENTITIES
OPTIONAL_IDENTITIES = handoff_config.OPTIONAL_IDENTITIES


def ordered(mapping: Mapping[str, Any]) -> List[str]:
    """Identity names present in ``mapping``, in canonical identity order."""

    return [identity for identity in IDENTITIES if identity in mapping]
```

Extend every preset in `PRESETS` with both optional rows (`None` still means "detect the Codex model"):

```python
    "balanced": {
        "deep_reasoner": ("claude", "opus", "high"),
        "fast_worker": ("codex", None, "high"),
        "arbiter": ("codex", None, "xhigh"),
        "e2e_specifier": ("codex", None, "xhigh"),
        "e2e_verifier": ("codex", None, "high"),
    },
    "quality": {
        "deep_reasoner": ("claude", "opus", "high"),
        "fast_worker": ("claude", "opus", "high"),
        "arbiter": ("codex", None, "xhigh"),
        "e2e_specifier": ("claude", "opus", "high"),
        "e2e_verifier": ("codex", None, "high"),
    },
    "cost": {
        "deep_reasoner": ("codex", None, "xhigh"),
        "fast_worker": ("codex", None, "medium"),
        "arbiter": ("claude", "sonnet", "high"),
        "e2e_specifier": ("codex", None, "high"),
        "e2e_verifier": ("codex", None, "medium"),
    },
```

Add the flag in `build_parser`, on the same parser that already carries `--mode`:

```python
    e2e = parser.add_mutually_exclusive_group()
    e2e.add_argument(
        "--with-e2e",
        dest="with_e2e",
        action="store_true",
        help="Also configure the optional e2e_specifier and e2e_verifier identities.",
    )
    e2e.add_argument("--no-with-e2e", dest="with_e2e", action="store_false")
    parser.set_defaults(with_e2e=False)
```

- [x] **Step 4: Make the identity set depend on the flag**

In `choose_identities`, compute the working set once and iterate it everywhere in that function:

```python
    selected = list(CORE_IDENTITIES)
    if getattr(args, "with_e2e", False):
        selected.extend(OPTIONAL_IDENTITIES)
```

Then in the same function replace `for identity in IDENTITIES:` with `for identity in selected:` at both the custom-mode site (around line 246) and the preset-mode site (around line 270), and change the custom-mode error text from `"for all three identities"` to:

```python
                    "custom mode requires --role-backend, --role-model, and "
                    f"--role-effort for: {', '.join(selected)}"
```

- [x] **Step 5: Switch the mapping loops to `ordered()`**

Each of these iterates a mapping that may now lack the optional keys. Replace `for identity in IDENTITIES:` with the listed expression:

| Site | Replacement |
|---|---|
| `preserve_verification` (~line 316) | `for identity in ordered(desired):` |
| `_cli_unavailable` (~line 486) | `for identity in ordered(desired)` |
| `build_plan` sources loop (~line 514) | `for identity in ordered(desired):` |
| `print_plan` (~line 599) | `for identity in ordered(plan.choices):` |
| `smoke` execution loop (~line 801) | `for identity in ordered(configured):` |
| `smoke` verified write-back (~line 841) | `for identity in ordered(configured):` |

The agent-path map (~line 421) keeps `IDENTITIES` — it must be able to name the path of an agent it is about to remove. The `~/.claude/agents` detection scan (~line 219) keeps `IDENTITIES`. The `--status` printer (~line 723) keeps `IDENTITIES`: it already uses `identities.get(identity, {})` and prints `<unset>`, which is how a user discovers the add-on exists.

- [x] **Step 6: Scope the "not configured" gate to core**

In `smoke` (~line 790):

```python
    missing = [identity for identity in CORE_IDENTITIES if identity not in configured]
```

An unconfigured optional identity is a deliberate state, not an incomplete setup.

- [x] **Step 7: Add the agent bodies and routing lines**

Replace the `if/elif/else` chain in `render_agent` with an explicit mapping so five identities do not become a five-branch chain:

```python
AGENT_TEXT = {
    "deep_reasoner": (
        "Handles reasoning-intensive architecture, diagnosis, and trade-off work.",
        "Investigate constraints deeply, challenge faulty premises, and return a concise conclusion with evidence and risks.",
    ),
    "fast_worker": (
        "Handles mechanical, well-scoped implementation and verification work.",
        "Execute the given specification precisely, verify the result, and report changed files, checks, and deviations.",
    ),
    "arbiter": (
        "Independent blind arbiter. Every question must be solved "
        "independently; the packet carries no one else's answer.",
        "Independently solve the received problem. Treat any packet containing another answer, conclusion, or hint as contaminated and report it instead of using it.",
    ),
    "e2e_specifier": (
        "Turns a frozen specification into Gherkin acceptance scenarios and repo-native executable tests.",
        "Write Gherkin scenarios with stable IDs and executable tests tagged by those IDs, in the repo's detected e2e stack. Keep launch scaffolding out of spec files. Commit on this worktree branch; do not merge, push, or remove the worktree.",
    ),
    "e2e_verifier": (
        "Executes reviewed acceptance tests and produces a validated PASS, FAIL, or BLOCKED verdict.",
        "Execute the reviewed tests against the pinned commit and write the verdict artifact. Repair launch and runner scaffolding only. Never change scenario meaning, expected values, or product code; report a needed semantic change instead of making it.",
    ),
}
```

and in `render_agent`:

```python
    description, body = AGENT_TEXT[identity]
```

Extend `ROUTING_POLICY` so a configured optional identity gets a routing line. Build it from the desired set rather than as a constant string:

```python
ROUTING_LINES = {
    "deep_reasoner": "Route reasoning-intensive, ambiguous work to handoff-deep-reasoner.",
    "fast_worker": "Route mechanical, well-scoped execution to handoff-fast-worker.",
    "arbiter": "Route independent blind-solve arbitration to handoff-arbiter.",
    "e2e_specifier": "Route acceptance-test authoring to handoff-e2e-specifier.",
    "e2e_verifier": "Route acceptance-test execution to handoff-e2e-verifier.",
}


def routing_policy(identities: Mapping[str, Any]) -> str:
    return "".join(f"{ROUTING_LINES[name]}\n" for name in ordered(identities))
```

Replace each use of the `ROUTING_POLICY` constant with a `routing_policy(desired)` call. Delete the now-unused constant.

- [x] **Step 8: Add the interactive prompt**

In the interactive custom-mode block (~line 940), prompt for core identities, then ask once:

```python
    answer = input("Also configure the optional e2e identities? [y/N]: ").strip().lower()
    with_e2e = answer in ("y", "yes")
```

and extend the identity loop to the optional names only when `with_e2e` is true.

- [x] **Step 9: Update the config schema document**

`docs/config-schema.md` says `<identity>` is `deep_reasoner`, `fast_worker`, or
`arbiter`. Widen that sentence to all five, mark the last two optional, add both
to the example config with a comment saying setup writes them only under
`--with-e2e`, and note in the CLI section that `set --role` accepts them. State
that `schema_version` stays `2` because the document shape did not change, and
that a config carrying optional identities fails closed on a pre-3.2 engine —
`validate_config` raises on an unrecognized identity, which is the correct
direction for a version skew.

- [x] **Step 10: Run the whole suite**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS. Every pre-existing `test_handoff_setup` test must pass unchanged — if one needed editing, the change was not backward compatible and belongs back in Step 5.

- [x] **Step 11: Commit**

```bash
git add scripts/handoff-setup.py tests/test_handoff_setup.py docs/config-schema.md
git commit -m "feat(setup): add --with-e2e for the optional e2e identities"
```

---

### Task 3: E2E add-on in the setup UI

**Files:**
- Modify: `scripts/handoff-setup-ui.py` (`IDENTITY_META`, `normalize_payload`, `engine_arguments`, the HTML/JS block)
- Test: `tests/test_handoff_setup_ui.py`

**Interfaces:**
- Consumes: `engine.IDENTITIES`, `engine.CORE_IDENTITIES`, `engine.OPTIONAL_IDENTITIES`, `engine.PRESETS` (Task 2).
- Produces: `normalize_payload(...)` returns a `with_e2e: bool` key; `engine_arguments(payload, action)` emits `--with-e2e` or `--no-with-e2e`. The module is imported in tests as `handoff_setup_ui`, and `IDENTITY_META` is module-level.

- [x] **Step 1: Write the failing tests**

```python
class E2eAddOnTests(SetupUITests):
    def test_identity_meta_covers_every_identity(self):
        self.assertEqual(
            set(handoff_setup_ui.engine.IDENTITIES),
            set(handoff_setup_ui.IDENTITY_META),
        )

    def test_payload_without_the_flag_ignores_optional_identities(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        raw = self.payload(controller)
        normalized = handoff_setup_ui.normalize_payload(
            raw, repo=self.repo, env=self.env
        )
        self.assertFalse(normalized["with_e2e"])
        self.assertNotIn("e2e_specifier", normalized["identities"])

    def test_payload_with_the_flag_keeps_optional_identities(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        raw = self.payload(controller)
        raw["with_e2e"] = True
        normalized = handoff_setup_ui.normalize_payload(
            raw, repo=self.repo, env=self.env
        )
        self.assertTrue(normalized["with_e2e"])
        self.assertIn("e2e_verifier", normalized["identities"])

    def test_payload_with_the_flag_rejects_a_missing_optional_identity(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        raw = self.payload(controller)
        raw["with_e2e"] = True
        del raw["identities"]["e2e_specifier"]
        with self.assertRaisesRegex(handoff_setup_ui.UIError, "e2e_specifier"):
            handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)

    def test_engine_arguments_always_state_the_flag(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        raw = self.payload(controller)
        off = handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)
        self.assertIn("--no-with-e2e", handoff_setup_ui.engine_arguments(off, "preview"))
        raw["with_e2e"] = True
        on = handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)
        self.assertIn("--with-e2e", handoff_setup_ui.engine_arguments(on, "preview"))
```

The existing `SetupUITests.payload(controller, mode)` helper builds its identity
matrix from `state["presets"][mode]`, which Task 2 gave all five rows, so it
already supplies the optional entries — the tests above only flip the toggle.

- [x] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_handoff_setup_ui.E2eAddOnTests -v`
Expected: FAIL — `IDENTITY_META` is missing two keys.

- [x] **Step 3: Add the metadata**

```python
    "e2e_specifier": {
        "label": "Acceptance authoring",
        "hint": "Gherkin scenarios and repo-native executable tests",
    },
    "e2e_verifier": {
        "label": "Acceptance execution",
        "hint": "Runs the reviewed tests and reports a validated verdict",
    },
```

- [x] **Step 4: Make the optional identities conditional in the payload**

In `normalize_payload`, read the toggle first and derive the required set from it:

```python
    with_e2e = bool(raw.get("with_e2e"))
    required = list(engine.CORE_IDENTITIES)
    if with_e2e:
        required.extend(engine.OPTIONAL_IDENTITIES)
```

Replace `for identity in engine.IDENTITIES:` in the identity-collection loop (around line 434) with `for identity in required:`, and do the same in the preset-comparison block (around line 472) so `comparable` covers only what the payload carries. Extra identity keys the page sent while the toggle is off are simply not read. Add `"with_e2e": with_e2e` to the returned dict.

In `engine_arguments`, change the custom-mode loop to `for identity in payload["identities"]:` and append the flag unconditionally:

```python
    args.append("--with-e2e" if payload["with_e2e"] else "--no-with-e2e")
```

`_preset_matrices` needs no change: Task 2 gave every preset all five rows, so `preset[identity]` always resolves.

In `engine_arguments`, the custom-mode loop at line 501 also iterates `engine.IDENTITIES` — change it to iterate `payload["identities"]` so it cannot index a key the payload omitted.

- [x] **Step 5: Add the UI block**

In the page HTML, after the three core identity cards, add a collapsed section:

```html
<details id="e2eAddOn">
  <summary>E2E acceptance testing (optional)</summary>
  <p>Adds two identities that write and run acceptance tests for
     user-observable changes. Leave off if you do not run end-to-end tests.</p>
  <label><input type="checkbox" id="withE2e"> Configure e2e identities</label>
  <div id="e2eCards"></div>
</details>
```

Wire `withE2e`'s `change` event to `invalidate()` so toggling it forces a new preview, include `with_e2e: $('withE2e').checked` in the payload the page posts, and render the two identity cards into `e2eCards` only while the box is checked. Extend `syncHeroMap`'s `targets` with `e2e_specifier:'heroE2eSpec'` and `e2e_verifier:'heroE2eVerify'`, adding those two spans to the hero summary; the existing `if (!values) continue;` guard already handles the unconfigured case.

- [x] **Step 6: Run the tests**

Run: `python3 -m unittest tests.test_handoff_setup_ui -v`
Expected: PASS.

- [x] **Step 7: Commit**

```bash
git add scripts/handoff-setup-ui.py tests/test_handoff_setup_ui.py
git commit -m "feat(setup-ui): add the optional e2e add-on block"
```

---

### Task 4: Worktree lifecycle in `delegate-codex.sh`

**Files:**
- Modify: `scripts/delegate-codex.sh` (usage, `cmd_submit`, `write_run_script`, `cmd_resume`, `cmd_cancel`, new `cmd_cleanup`, dispatcher)
- Test: `tests/test_delegate_role.py`

**Interfaces:**
- Consumes: the widened `--role` set from Task 1.
- Produces:
  - `delegate-codex.sh submit --worktree <branch> [--base <commit-ish>]`
  - `meta` keys `worktree=<abs path>`, `branch=<name>`, `base_commit=<40-char sha>`, written only when a worktree was created
  - `delegate-codex.sh cleanup <jobId> --repo <path>` — exit 0 when removed or already absent, exit 1 when the worktree has uncommitted changes

- [x] **Step 1: Write the failing tests**

Append to `tests/test_delegate_role.py`. The existing `run_submit` helper supports `init_git=True` and `--dry-run`; worktree tests need a real submit, so add a `run_submit_live` variant that omits `--dry-run` and uses a fake `codex` that exits immediately.

```python
class WorktreeTests(unittest.TestCase):
    def test_e2e_roles_are_accepted(self):
        for role in ("e2e_specifier", "e2e_verifier"):
            with self.subTest(role=role):
                result, _ = self.run_submit(self.config_with(role), "--role", role)
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertEqual(role, self.parse_dry_run(result.stdout)["role"])

    def test_worktree_is_created_and_recorded_with_a_base_sha(self):
        job_id, repo = self.run_submit_live("--worktree", "e2e/T1")
        meta = self.read_meta(repo, job_id)
        self.assertEqual(str(repo / ".handoff" / "worktrees" / job_id), meta["worktree"])
        self.assertEqual("e2e/T1", meta["branch"])
        self.assertRegex(meta["base_commit"], r"^[0-9a-f]{40}$")
        self.assertTrue((repo / ".handoff" / "worktrees" / job_id / "tracked.txt").exists())

    def test_base_is_resolved_to_an_immutable_sha_before_the_branch_moves(self):
        job_id, repo = self.run_submit_live("--worktree", "e2e/T2", "--base", "main")
        pinned = self.read_meta(repo, job_id)["base_commit"]
        self.commit_more(repo)  # advances main
        head = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "main"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
        self.assertNotEqual(head, pinned)

    def test_invalid_base_fails_before_the_job_directory_is_created(self):
        result, repo = self.run_submit_live_raw("--worktree", "e2e/T3", "--base", "no-such-ref")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not a valid commit", result.stderr)
        self.assertFalse(any((repo / ".handoff" / "jobs").glob("job-*")))

    def test_run_script_uses_the_worktree_as_working_directory(self):
        job_id, repo = self.run_submit_live("--worktree", "e2e/T4")
        run_sh = (repo / ".handoff" / "jobs" / job_id / "run.sh").read_text(encoding="utf-8")
        self.assertIn(str(repo / ".handoff" / "worktrees" / job_id), run_sh)
        self.assertIn('-C "$WORKDIR"', run_sh)

    def test_cleanup_removes_a_clean_worktree_and_is_idempotent(self):
        job_id, repo = self.run_submit_live("--worktree", "e2e/T5")
        first = self.run_cleanup(repo, job_id)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertFalse((repo / ".handoff" / "worktrees" / job_id).exists())
        second = self.run_cleanup(repo, job_id)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertIn("no worktree", second.stdout)

    def test_cleanup_refuses_a_dirty_worktree(self):
        job_id, repo = self.run_submit_live("--worktree", "e2e/T6")
        (repo / ".handoff" / "worktrees" / job_id / "tracked.txt").write_text("dirty\n")
        result = self.run_cleanup(repo, job_id)
        self.assertEqual(1, result.returncode)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertTrue((repo / ".handoff" / "worktrees" / job_id).exists())

    def test_submit_without_worktree_records_no_worktree_keys(self):
        job_id, repo = self.run_submit_live()
        meta = self.read_meta(repo, job_id)
        self.assertNotIn("worktree", meta)
        self.assertNotIn("base_commit", meta)
```

`read_meta` parses the `key=value` lines of `<repo>/.handoff/jobs/<jobId>/meta` into a dict. `run_cleanup` invokes `bash scripts/delegate-codex.sh cleanup <jobId> --repo <repo>`. `commit_more` writes a file and commits it on `main`.

- [x] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_delegate_role.WorktreeTests -v`
Expected: FAIL — `unknown submit argument: --worktree`.

- [x] **Step 3: Widen the role list and parse the new flags**

In `cmd_submit`, add to the local declarations and the argument loop:

```bash
  local WORKTREE_BRANCH="" WORKTREE_BASE="" BASE_COMMIT=""
```

```bash
      --worktree) WORKTREE_BRANCH="${2:-}"; shift 2 ;;
      --base) WORKTREE_BASE="${2:-}"; shift 2 ;;
```

and widen the role guard:

```bash
  case "$ROLE" in ""|deep_reasoner|fast_worker|arbiter|e2e_specifier|e2e_verifier) ;; *) die "invalid --role: $ROLE" ;; esac
```

Update the `usage` heredoc to show `--worktree <branch> [--base <commit-ish>]` on `submit`, the widened `--role` list, and the new `cleanup` line.

- [x] **Step 4: Resolve the base before creating anything**

In `cmd_submit`, immediately after the role guard and before `resolve_codex_bin`, so an invalid base fails before a job directory exists:

```bash
  if [ -n "$WORKTREE_BRANCH" ]; then
    is_git_repo "$REPO" || die "--worktree requires --repo to be a git repository"
    # Pin to an immutable SHA: a branch name can advance between cutting the
    # worktree and reading the verdict, and then the verdict names a commit
    # nobody tested.
    BASE_COMMIT="$(git -C "$REPO" rev-parse --verify "${WORKTREE_BASE:-HEAD}^{commit}" 2>/dev/null)" \
      || die "--base is not a valid commit: ${WORKTREE_BASE:-HEAD}"
  fi
```

- [x] **Step 5: Create the worktree and record it**

After `mkdir -p "$JOB"` and the `cp "$PROMPT_FILE"` line, before the `meta` block:

```bash
  local WORKDIR="$REPO"
  if [ -n "$WORKTREE_BRANCH" ]; then
    WORKDIR="$REPO/.handoff/worktrees/$JOB_ID"
    mkdir -p "$REPO/.handoff/worktrees"
    git -C "$REPO" worktree add --quiet -b "$WORKTREE_BRANCH" "$WORKDIR" "$BASE_COMMIT" \
      || die "git worktree add failed for branch: $WORKTREE_BRANCH"
  fi
```

Append the three keys to `meta` only when a worktree exists, after the existing `printf` block:

```bash
  if [ -n "$WORKTREE_BRANCH" ]; then
    printf 'worktree=%s\nbranch=%s\nbase_commit=%s\n' \
      "$WORKDIR" "$WORKTREE_BRANCH" "$BASE_COMMIT" >>"$JOB/meta"
  fi
```

and pass the working directory through:

```bash
  write_run_script "$JOB" "$EFFORT" "$MODEL" "$READ_ONLY" "" "$WORKDIR"
```

- [x] **Step 6: Teach `write_run_script` about the working directory**

The generated script currently pins `REPO` and uses it for both `-C` and `cd`. Replace that with `WORKDIR`, which defaults to `$REPO` so every existing call site keeps its behaviour:

```bash
write_run_script() {
  local job="$1" effort="$2" model="$3" read_only="$4" session_id="$5" workdir="${6:-$REPO}"
  {
    echo '#!/usr/bin/env bash'
    echo 'set -uo pipefail'
    printf 'JOB=%q\n' "$job"
    printf 'WORKDIR=%q\n' "$workdir"
    printf 'CODEX_BIN=%q\n' "$CODEX_BIN"
```

Then in the resume branch replace `echo 'cd "$REPO"'` with `echo 'cd "$WORKDIR"'`, and in the fresh branch replace `-C \"\$REPO\"` with `-C \"\$WORKDIR\"` and `is_git_repo "$REPO"` with `is_git_repo "$workdir"`. The `REPO=` line is now unused inside the generated script and is removed by this edit — that is the intended orphan cleanup, not an unrelated change.

- [x] **Step 7: Make `resume` land in the same tree**

`codex exec resume` takes its cwd from the shell, so a fix round would otherwise land in the main repo. In `cmd_resume`, after `local PARENT_JOB="$JOB"`:

```bash
  local PARENT_WORKDIR
  PARENT_WORKDIR="$(sed -n 's/^worktree=//p' "$PARENT_JOB/meta")"
  if [ -n "$PARENT_WORKDIR" ]; then
    [ -d "$PARENT_WORKDIR" ] || die "parent job worktree is missing: $PARENT_WORKDIR"
  else
    PARENT_WORKDIR="$REPO"
  fi
```

Carry it into the child's `meta` (append after the existing `printf`, when non-empty and not equal to `$REPO`) and pass it as the sixth argument:

```bash
  write_run_script "$JOB" "${EFFORT:-high}" "" "$READ_ONLY" "$SESSION_ID" "$PARENT_WORKDIR"
```

- [x] **Step 8: Add `cleanup` and wire `cancel` to it**

`cleanup` must `return`, never `die`, on the dirty path — `die` calls `exit`, which would abort `cancel` mid-way:

```bash
cmd_cleanup() {
  local JOB_ID="$1"; shift
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --repo) REPO="${2:-}"; shift 2 ;;
      *) die "unknown cleanup argument: $1" ;;
    esac
  done
  require_repo
  require_job "$JOB_ID"
  local WT
  WT="$(sed -n 's/^worktree=//p' "$JOB/meta")"
  if [ -z "$WT" ] || [ ! -d "$WT" ]; then
    echo "no worktree: $JOB_ID"
    return 0
  fi
  if [ -n "$(git -C "$WT" status --porcelain 2>/dev/null)" ]; then
    echo "worktree has uncommitted changes: $WT" >&2
    return 1
  fi
  git -C "$REPO" worktree remove "$WT" || { echo "git worktree remove failed: $WT" >&2; return 1; }
  echo "removed worktree: $WT"
}
```

In `cmd_cancel`, after `touch "$JOB/cancelled"`:

```bash
  cmd_cleanup "$JOB_ID" --repo "$REPO" || echo "worktree kept for inspection: $JOB_ID"
```

Add `cleanup` to the dispatcher's jobId-taking group:

```bash
  status|result|resume|cancel|cleanup)
```

- [x] **Step 9: Run the tests**

Run: `python3 -m unittest tests.test_delegate_role -v`
Expected: PASS, including every pre-existing test in the module.

- [x] **Step 10: Commit**

```bash
git add scripts/delegate-codex.sh tests/test_delegate_role.py
git commit -m "feat(delegate): add e2e roles and a pinned worktree lifecycle"
```

---

### Task 5: Verdict artifact and validator

**Files:**
- Create: `scripts/validate-verdict.py`
- Create: `docs/verdict-schema.json`
- Test: `tests/test_validate_verdict.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `python3 scripts/validate-verdict.py <path|-> [--expect-scenarios-sha256 <sha>]` — exit 0 when valid, exit 1 with one `FAIL: ` line per problem on stderr. Importable as a module: `validate(verdict: dict, expected_sha: str | None) -> list[str]`.

- [x] **Step 1: Write the failing tests**

Create `tests/test_validate_verdict.py`:

```python
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-verdict.py"
SPEC = importlib.util.spec_from_file_location("validate_verdict", SCRIPT)
validate_verdict = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = validate_verdict
SPEC.loader.exec_module(validate_verdict)


def verdict(**overrides):
    base = {
        "result": "PASS",
        "base_commit": "a" * 40,
        "tested_commit": "b" * 40,
        "scenarios_sha256": "c" * 64,
        "target": "http://localhost:5173 (local dev server)",
        "command": "npx cypress run",
        "exit_code": 0,
        "scenarios": {"total": 2, "passed": 2, "ids": ["CHK-01", "CHK-02"]},
        "harness_edits": [],
        "findings": [],
        "evidence": ["cypress/videos/checkout.mp4"],
    }
    base.update(overrides)
    return base


class VerdictValidatorTests(unittest.TestCase):
    def test_valid_pass_has_no_failures(self):
        self.assertEqual([], validate_verdict.validate(verdict(), None))

    def test_missing_field_fails(self):
        broken = verdict()
        del broken["tested_commit"]
        self.assertIn("missing field: tested_commit", "\n".join(validate_verdict.validate(broken, None)))

    def test_unknown_result_fails(self):
        failures = validate_verdict.validate(verdict(result="GREEN"), None)
        self.assertIn("result must be one of", "\n".join(failures))

    def test_pass_with_nonzero_exit_fails(self):
        failures = validate_verdict.validate(verdict(exit_code=1), None)
        self.assertIn("PASS requires exit_code 0", "\n".join(failures))

    def test_pass_with_unrun_scenarios_fails(self):
        failures = validate_verdict.validate(
            verdict(scenarios={"total": 3, "passed": 2, "ids": ["CHK-01", "CHK-02", "CHK-03"]}), None
        )
        self.assertIn("PASS requires every scenario to pass", "\n".join(failures))

    def test_pass_with_findings_fails(self):
        failures = validate_verdict.validate(verdict(findings=["checkout total is wrong"]), None)
        self.assertIn("PASS requires an empty findings list", "\n".join(failures))

    def test_id_count_must_match_total(self):
        failures = validate_verdict.validate(
            verdict(scenarios={"total": 3, "passed": 3, "ids": ["CHK-01"]}), None
        )
        self.assertIn("scenarios.total must equal the number of ids", "\n".join(failures))

    def test_fail_requires_a_finding(self):
        failures = validate_verdict.validate(
            verdict(result="FAIL", exit_code=1, scenarios={"total": 2, "passed": 1, "ids": ["CHK-01", "CHK-02"]}),
            None,
        )
        self.assertIn("FAIL requires at least one finding", "\n".join(failures))

    def test_fail_without_evidence_of_failure_fails(self):
        failures = validate_verdict.validate(
            verdict(result="FAIL", findings=["broken"], exit_code=0), None
        )
        self.assertIn("FAIL requires a non-zero exit_code", "\n".join(failures))

    def test_blocked_requires_zero_passed_and_a_reason(self):
        ok = verdict(
            result="BLOCKED",
            command=None,
            exit_code=None,
            scenarios={"total": 0, "passed": 0, "ids": []},
            findings=["dev server would not start: port 5173 in use"],
        )
        self.assertEqual([], validate_verdict.validate(ok, None))
        failures = validate_verdict.validate(
            verdict(result="BLOCKED", scenarios={"total": 2, "passed": 2, "ids": ["CHK-01", "CHK-02"]}),
            None,
        )
        self.assertIn("BLOCKED requires scenarios.passed to be 0", "\n".join(failures))

    def test_hash_mismatch_is_rejected(self):
        failures = validate_verdict.validate(verdict(), "d" * 64)
        self.assertIn("scenarios_sha256 does not match the reviewed scenarios", "\n".join(failures))

    def test_hash_match_is_accepted(self):
        self.assertEqual([], validate_verdict.validate(verdict(), "c" * 64))

    def test_cli_exits_nonzero_on_an_invalid_verdict(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as handle:
            json.dump(verdict(exit_code=1), handle)
            path = handle.name
        result = subprocess.run(
            [sys.executable, str(SCRIPT), path], capture_output=True, text=True, check=False
        )
        self.assertEqual(1, result.returncode)
        self.assertIn("FAIL:", result.stderr)
```

- [x] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_validate_verdict -v`
Expected: FAIL with `FileNotFoundError` for `scripts/validate-verdict.py`.

- [x] **Step 3: Write the validator**

Create `scripts/validate-verdict.py`:

```python
#!/usr/bin/env python3
"""Validate an e2e verdict artifact written by the e2e_verifier role.

The verdict is the input to a merge decision, so its fields are checked
against each other rather than trusted: a PASS must agree with the exit
code, the scenario counts, and the hash of the scenarios the driver
reviewed. Field semantics are documented in docs/verdict-schema.json.

Usage:
    python3 scripts/validate-verdict.py <verdict.json>
    python3 scripts/validate-verdict.py - --expect-scenarios-sha256 <sha>

Exit 0 when valid, 1 with FAIL lines on stderr otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any, List, Mapping, Optional

RESULTS = ("PASS", "FAIL", "BLOCKED")
REQUIRED_FIELDS = (
    "result",
    "base_commit",
    "tested_commit",
    "scenarios_sha256",
    "target",
    "command",
    "exit_code",
    "scenarios",
    "harness_edits",
    "findings",
    "evidence",
)
SHA1 = re.compile(r"^[0-9a-f]{40}$")
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def validate(verdict: Mapping[str, Any], expected_sha: Optional[str]) -> List[str]:
    failures: List[str] = []
    for field in REQUIRED_FIELDS:
        if field not in verdict:
            failures.append(f"missing field: {field}")
    if failures:
        return failures

    result = verdict["result"]
    if result not in RESULTS:
        failures.append(f"result must be one of {', '.join(RESULTS)}; got {result!r}")

    for field in ("base_commit", "tested_commit"):
        if not isinstance(verdict[field], str) or not SHA1.match(verdict[field]):
            failures.append(f"{field} must be a 40-character commit sha")

    sha = verdict["scenarios_sha256"]
    if not isinstance(sha, str) or not SHA256.match(sha):
        failures.append("scenarios_sha256 must be a 64-character sha256")
    elif expected_sha is not None and sha != expected_sha:
        failures.append(
            "scenarios_sha256 does not match the reviewed scenarios; "
            "the acceptance contract changed after review, so this run is void"
        )

    if not isinstance(verdict["target"], str) or not verdict["target"].strip():
        failures.append("target must name the system under test")

    scenarios = verdict["scenarios"]
    if not isinstance(scenarios, Mapping):
        failures.append("scenarios must be an object with total, passed, and ids")
        return failures
    for key in ("total", "passed"):
        if not isinstance(scenarios.get(key), int) or scenarios[key] < 0:
            failures.append(f"scenarios.{key} must be a non-negative integer")
    ids = scenarios.get("ids")
    if not isinstance(ids, list) or any(not isinstance(item, str) for item in ids):
        failures.append("scenarios.ids must be a list of scenario id strings")
    if failures:
        return failures

    if scenarios["total"] != len(ids):
        failures.append("scenarios.total must equal the number of ids")
    if scenarios["passed"] > scenarios["total"]:
        failures.append("scenarios.passed must not exceed scenarios.total")

    for field in ("harness_edits", "findings", "evidence"):
        if not isinstance(verdict[field], list):
            failures.append(f"{field} must be a list")
    if failures:
        return failures

    exit_code = verdict["exit_code"]
    if exit_code is not None and not isinstance(exit_code, int):
        failures.append("exit_code must be an integer or null")

    if result == "PASS":
        if exit_code != 0:
            failures.append("PASS requires exit_code 0")
        if scenarios["passed"] != scenarios["total"]:
            failures.append("PASS requires every scenario to pass")
        if scenarios["total"] == 0:
            failures.append("PASS requires at least one executed scenario")
        if verdict["findings"]:
            failures.append("PASS requires an empty findings list")
    elif result == "FAIL":
        if not verdict["findings"]:
            failures.append("FAIL requires at least one finding")
        if exit_code == 0 and scenarios["passed"] == scenarios["total"]:
            failures.append("FAIL requires a non-zero exit_code or an unpassed scenario")
    else:  # BLOCKED
        if scenarios["passed"] != 0:
            failures.append("BLOCKED requires scenarios.passed to be 0; no valid execution happened")
        if not verdict["findings"]:
            failures.append("BLOCKED requires a finding naming the blocker")

    return failures


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Validate an e2e verdict artifact.")
    parser.add_argument("path", help="Path to verdict.json, or - for stdin.")
    parser.add_argument(
        "--expect-scenarios-sha256",
        help="Hash of the reviewed .feature files, as recorded by the driver at review time.",
    )
    args = parser.parse_args(argv)

    raw = sys.stdin.read() if args.path == "-" else Path(args.path).read_text(encoding="utf-8")
    try:
        verdict = json.loads(raw)
    except json.JSONDecodeError as error:
        print(f"FAIL: verdict is not valid JSON: {error}", file=sys.stderr)
        return 1
    if not isinstance(verdict, dict):
        print("FAIL: verdict must be a JSON object", file=sys.stderr)
        return 1

    failures = validate(verdict, args.expect_scenarios_sha256)
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(f"OK: {verdict['result']} verdict for {verdict['tested_commit'][:12]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [x] **Step 4: Write the schema document**

Create `docs/verdict-schema.json` as a JSON Schema draft 2020-12 document with `$id: "handoff.verdict.v1"`, the eleven required properties typed as the validator checks them, and a top-level `description` stating that structural validity is necessary but not sufficient — the cross-field rules in `scripts/validate-verdict.py` are the contract, and a verdict must be checked with that script rather than by shape alone.

- [x] **Step 5: Run the tests**

Run: `python3 -m unittest tests.test_validate_verdict -v`
Expected: PASS.

- [x] **Step 6: Commit**

```bash
chmod +x scripts/validate-verdict.py
git add scripts/validate-verdict.py docs/verdict-schema.json tests/test_validate_verdict.py
git commit -m "feat(verdict): add the e2e verdict artifact and its validator"
```

---

### Task 6: Widen the receipt role enum

**Files:**
- Modify: `docs/receipt-schema.json` (`properties.roles_used` role enum and description)
- Modify: `scripts/validate-receipt.py:36`
- Test: `tests/test_receipt.py`

**Interfaces:**
- Consumes: nothing.
- Produces: receipts whose `roles_used` entries may carry `role: "e2e_specifier"` or `"e2e_verifier"`. `receipt_schema_version` stays `4`.

- [x] **Step 1: Write the failing test**

Append to `ValidateReceiptTests` in `tests/test_receipt.py`. That class already
provides `assert_one_failure(needle, **overrides)` and a module-level
`fields(**overrides)` builder; use them rather than adding a second helper:

```python
    def test_roles_used_accepts_e2e_roles(self):
        self.assertEqual(
            [],
            validate_receipt.validate(
                fields(
                    roles_used='[{"role": "e2e_verifier", "host": "codex", '
                    '"model": "gpt-x", "effort": "high", "verified": true}]'
                )
            ),
        )

    def test_roles_used_still_rejects_an_unknown_role(self):
        self.assert_one_failure(
            "roles_used is invalid",
            roles_used='[{"role": "cleaner", "host": "codex", '
            '"model": "gpt-x", "effort": "high", "verified": true}]',
        )
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python3 -m unittest tests.test_receipt -v`
Expected: FAIL — `e2e_verifier` is not in `ROLES`.

- [x] **Step 3: Widen both definitions**

In `scripts/validate-receipt.py` line 36:

```python
ROLES = {"deep_reasoner", "fast_worker", "arbiter", "e2e_specifier", "e2e_verifier"}
```

In `docs/receipt-schema.json`, extend `properties.roles_used.oneOf[1].items.properties.role.enum` with both names, and append one sentence to the `roles_used` description:

> The optional e2e roles appear only on runs that used them; widening this enum is additive, so v4 receipts written before the e2e identities existed remain valid.

Do not change `$id` or the `receipt_schema_version` const.

- [x] **Step 4: Run the tests and the roundtrip**

```bash
python3 -m unittest tests.test_receipt -v
python3 scripts/make-receipt.py --start --repo .
python3 scripts/make-receipt.py --repo . --phase review --claude-session x \
  --checks "ci" --codex-jobs 1 \
  --scope project --config-source project --roles-used '[]' \
  | python3 scripts/validate-receipt.py -
```

Expected: tests PASS; the roundtrip exits 0.

- [x] **Step 5: Commit**

```bash
git add scripts/validate-receipt.py docs/receipt-schema.json tests/test_receipt.py
git commit -m "feat(receipt): allow e2e roles in roles_used"
```

---

### Task 7: The `references/e2e-gauntlet.md` flow document

**Files:**
- Create: `references/e2e-gauntlet.md`

**Interfaces:**
- Consumes: the CLI surface from Tasks 4 and 5 — `submit --worktree <branch> --base <sha>`, `cleanup <jobId>`, `validate-verdict.py --expect-scenarios-sha256`.
- Produces: the document `references/claude-driven.md` will point at in Task 8, and the required file `check-skill-repo.sh` will check for in Task 9.

- [x] **Step 1: Write the document**

Create `references/e2e-gauntlet.md` covering these sections, in this order. Every command shown must be one that exists after Tasks 4 and 5 — no invented flags.

1. **When to use** — the Phase 1 criterion: add e2e rows when the change alters user-observable behaviour at a real interface (UI, API surface, mobile screen); skip for internal refactors, docs, config, pure library work. When the criterion fires but the identities are unconfigured, say so once — "this change is user-observable; `/agent-handoff config --with-e2e` would add acceptance coverage" — then continue without them.
2. **Worktree protocol** — the four driver-owned rules: resolve routing against the main repo; cut from an immutable SHA; the worker commits but never merges, pushes, or removes; the driver reviews, integrates, and cleans up. Then the two mechanisms: `delegate-codex.sh submit --worktree <branch> --base <sha>` for a codex backend, and the driver running the same two Git commands and passing the path for a claude backend. State plainly that the Task tool's `isolation: "worktree"` is not used, because it provides no base pinning, no metadata, and no cleanup contract.
3. **Packet self-containment** — a worktree receives tracked content only, so `.handoff/goal.md` and `.handoff/config.toml` are absent inside one. The frozen goal excerpt, spec, acceptance criteria, and artifact paths go in the prompt. `--repo` is always the main repo, never a worktree. Workers return artifact paths; only the driver writes the goal file, with `goal-sync.py`.
4. **Specifier packet** — full markdown packet following `references/handoff-template.md`'s shape, with the constraint line **replaced** by: "Commit your work on this worktree branch. Do not merge, push, rebase, remove the worktree, or touch secrets or `.env` files." Requires stable scenario IDs (`CHK-01`), executable tests tagged by those IDs, scaffolding kept out of spec files, stack detected from the repo, and a stop-and-propose response when the repo has no e2e stack.
5. **Verifier packet** — same replaced constraint line. States the frozen / repairable / forbidden split: frozen is scenario text and IDs, expected values, and the subject of an assertion; repairable is launch and runner scaffolding; forbidden is product code. A needed semantic change is reported, not made — it voids the run. Names the approved target and says a remote, production, destructive, or billable target needs explicit user authorization.
6. **Verdict artifact** — the `verdict.json` example from the design spec, where it is written (`.handoff/e2e/<jobId>/verdict.json`), and the driver's check:

```bash
python3 "$HANDOFF_DIR/scripts/validate-verdict.py" \
  "$REPO/.handoff/e2e/$JOB_ID/verdict.json" \
  --expect-scenarios-sha256 "$REVIEWED_SHA"
```

with `REVIEWED_SHA` computed at review time over the reviewed `.feature` files in path order:

```bash
REVIEWED_SHA=$(find features -name '*.feature' | sort | xargs cat | shasum -a 256 | cut -d' ' -f1)
```

7. **Review-the-tests-first gate** — the driver reads the specifier's scenarios against `.handoff/goal.md`'s acceptance column *before* the verdict counts for anything, and records `REVIEWED_SHA` at that moment. State the limit honestly: the hash locks the `.feature` files only; behavioural meaning inside executable spec files is guarded by diffing the files named in `harness_edits`, not by the hash.
8. **Ordering and integration** — the sequence diagram from the design spec: both jobs cut from `<sha0>`, integration onto the feature branch at `<sha1>`, `T_verify` depending on both rows and pinned to `<sha1>`, and `main` reached only after PASS plus final driver review. PASS goes to the merge decision; FAIL goes to the original implementer, never to the verifier; BLOCKED is resolved by the driver and never read as PASS.

- [x] **Step 2: Verify the gates accept it**

```bash
python3 scripts/english-only-scan.py
bash scripts/check-skill-repo.sh .
```

Expected: English-only PASS. `check-skill-repo.sh` still reports `fail=0` (it does not yet require this file — Task 9 adds that) with only the pre-existing `--force` warning from `handoff-setup.py`. If a new high-risk-command warning appears, it came from text you just wrote; reword it.

- [x] **Step 3: Commit**

```bash
git add references/e2e-gauntlet.md
git commit -m "docs(references): add the e2e gauntlet flow document"
```

---

### Task 8: Wire the flow prose

**Files:**
- Modify: `references/claude-driven.md`
- Modify: `references/goal-template.md`
- Modify: `references/handoff-template.md`

**Interfaces:**
- Consumes: `references/e2e-gauntlet.md` (Task 7).
- Produces: the `depends` column in the goal-file task table, which the `/loop` monitor reads to sequence submissions.

- [x] **Step 1: Add the `depends` column to the goal template**

In `references/goal-template.md`, change the task table to:

```markdown
| id | identity | task | acceptance | depends | effort | status | jobId |
|----|----------|------|------------|---------|--------|--------|-------|
| T1 | deep_reasoner | ... | ... | - | - | in_progress | - |
| T2 | fast_worker | ... | [check command that must pass] | - | high | delegated | job-... |
| T3 | e2e_verifier | ... | verdict.json validates as PASS | T1,T2 | high | pending | - |
```

Add to the rules list:

- `depends` is a comma-separated list of task ids that must reach `done` before this row is submitted, or `-`. The `/loop` monitor reads it; a row with unmet dependencies is not submitted.

Add the two identity definitions after `arbiter`:

- **`e2e_specifier`** — optional. Turns a frozen specification into Gherkin scenarios with stable IDs plus repo-native executable tests. Runs in parallel with implementation; depends only on the spec.
- **`e2e_verifier`** — optional. Executes the reviewed tests against a pinned commit and produces a validated verdict. Depends on both the specifier row and the implementation row.

Note in one line that e2e rows are optional and appear only when the Phase 1 criterion fires and the identities are configured — see `references/e2e-gauntlet.md`.

- [x] **Step 2: Note the packet override in the shared template**

In `references/handoff-template.md`, after the "Delegation packet rules" list, add:

- The e2e packets in `references/e2e-gauntlet.md` **replace** the "Do not commit" constraint line with a commit-on-this-worktree-branch rule. They are the only packets that do; do not append a commit permission to this template.

- [x] **Step 3: Wire the five phases**

In `references/claude-driven.md`:

- **Phase 1** — after the adversarial gate bullet, add the criterion, the unconfigured-but-applicable notice, and a pointer to `references/e2e-gauntlet.md`.
- **Phase 2** — add the worktree submit form and the rule that `--repo` is always the main repo:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label <task-id> \
  --role e2e_specifier --worktree "e2e/<task-id>" --base "$SHA0"
```

  plus one line saying the packet carries the frozen goal inline because a worktree has no `.handoff/`.
- **Phase 3** — the monitor loop reads `depends` and does not submit a row whose dependencies are unmet; `T_verify` waits on both `T_spec` and `T_impl`.
- **Phase 4** — insert the e2e sequence before the fix-round paragraph: review the specifier's scenarios against the goal's acceptance column, record `REVIEWED_SHA`, integrate onto the feature branch, submit the verifier pinned to the combined SHA, validate the verdict with `validate-verdict.py`, route FAIL to the original implementer, resolve BLOCKED without treating it as PASS, and run `delegate-codex.sh cleanup <jobId>` once a worktree is merged. State that `main` is reached only after PASS plus final review, which keeps merge inside the existing hard-stop rule.
- **Phase 5** — one line: e2e roles appear in `roles_used` like any other role when the run used them.

- [x] **Step 4: Verify the gates**

```bash
python3 scripts/english-only-scan.py
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
```

Expected: all three pass with only the pre-existing warning.

- [x] **Step 5: Commit**

```bash
git add references/claude-driven.md references/goal-template.md references/handoff-template.md
git commit -m "docs(flow): wire the e2e phases, depends column, and packet override"
```

---

### Task 9: Skill surface, gates, and version bump

**Files:**
- Modify: `SKILL.md` (frontmatter version, Configuration section, Output Contract)
- Modify: `README.md` (version badge, identity table, File Map)
- Modify: `CHANGELOG.md`
- Modify: `scripts/check-skill-repo.sh`
- Modify: `test-prompts.json`
- Modify: `CLAUDE.md` (Commands section)

**Interfaces:**
- Consumes: every file created in Tasks 1-8.
- Produces: a repo that passes `.github/workflows/checks.yml` end to end at version `3.2.0`.

- [x] **Step 1: Add the required-file checks**

In `scripts/check-skill-repo.sh`, beside the existing `check_file` calls:

```bash
check_file "references/e2e-gauntlet.md"
check_file "docs/verdict-schema.json"
```

- [x] **Step 2: Run the gate and record the baseline**

Run: `bash scripts/check-skill-repo.sh .`
Expected: the two files pass (they exist from Tasks 5 and 7). Note the current `fail=0 warn=1` baseline so you can tell a new warning from the pre-existing `--force` one.

- [x] **Step 3: Update `SKILL.md`**

- Frontmatter: `version: 3.1.0` → `3.2.0`.
- Configuration section: five identities, two optional. State that `deep_reasoner`, `fast_worker`, and `arbiter` are always configured, and that `e2e_specifier` and `e2e_verifier` are an opt-in add-on written only when setup runs `--with-e2e`. Point at `references/e2e-gauntlet.md` for when to use them.
- Output Contract: one sentence that `roles_used` may carry the two e2e roles, and that `receipt_schema_version` stays `4` because the enum widening is additive.

Do not put model or effort values in this file — identity values live only in config.

- [x] **Step 4: Update `README.md`**

- Version badge to `3.2.0`.
- Identity table: two rows, marked optional.
- File Map: `references/e2e-gauntlet.md` and `docs/verdict-schema.json`.

- [x] **Step 5: Update `CHANGELOG.md` and `CLAUDE.md`**

Add a `## v3.2.0` entry covering: two optional identities behind `--with-e2e`; the pinned worktree lifecycle and `cleanup` in `delegate-codex.sh`; the validated verdict artifact; the widened receipt role enum with no schema bump; the new `depends` column.

In `CLAUDE.md`'s Commands block, add the verdict check next to the receipt roundtrip:

```bash
python3 scripts/validate-verdict.py .handoff/e2e/<jobId>/verdict.json
```

and update the Architecture section: three identities becomes five, two optional.

- [x] **Step 6: Add the test prompts**

Append six cases to `test-prompts.json`, matching the existing `id` / `prompt` / `expected_behavior` / `must_not` shape:

| id | prompt | must produce |
|---|---|---|
| `e2e-added-for-user-observable-change` | a request to add a checkout screen | e2e rows added; specifier parallel with implementation |
| `e2e-skipped-for-internal-refactor` | a request to rename an internal helper | no e2e rows; the criterion cited |
| `e2e-unconfigured-says-so-once` | a UI change in a repo with no e2e identities configured | one notice naming `--with-e2e`, then continue without them |
| `e2e-verifier-never-edits-product-code` | verifier finds a real product bug | reports a finding, routes the fix to the original implementer |
| `e2e-verifier-never-merges` | verifier finishes green on its worktree | commits on its branch, leaves merge to the driver |
| `e2e-semantic-edit-voids-the-run` | verifier wants to change an expected value | reports it and stops; the run restarts after re-review |

Put any risky command text these cases need in a `must_not` list, per the repo's scanning rule.

- [x] **Step 7: Run the full CI gate locally**

```bash
python3 -m unittest discover -s tests
bash scripts/check-skill-repo.sh .
python3 scripts/english-only-scan.py
python3 scripts/run-test-prompts.py
bash install.sh --dry-run
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

Expected: every command exits 0. `check-skill-repo.sh` reports `fail=0` with only the pre-existing `--force` warning.

- [x] **Step 8: Commit**

```bash
git add SKILL.md README.md CHANGELOG.md CLAUDE.md scripts/check-skill-repo.sh test-prompts.json
git commit -m "feat: release v3.2.0 with the optional e2e gauntlet roles"
```

---

## Verification

After Task 9, confirm the feature end to end in a scratch repo rather than trusting the unit suite alone:

```bash
cd "$(mktemp -d)" && git init -q . && git config user.email t@t && git config user.name t
printf '.handoff/\n' > .gitignore && echo hi > app.txt && git add -A && git commit -qm init
REPO="$PWD"
python3 ~/.claude/skills/agent-handoff/scripts/handoff-setup.py \
  --repo "$REPO" --scope project --mode balanced --with-e2e --apply
python3 ~/.claude/skills/agent-handoff/scripts/handoff-config.py --repo "$REPO" resolve
```

Expected: `resolve` reports five identities. Then submit a worktree job with a trivial prompt and confirm `meta` carries `worktree=`, `branch=`, and a 40-character `base_commit=`, that the worktree exists at `.handoff/worktrees/<jobId>`, and that `cleanup <jobId>` removes it and is safe to run twice.
