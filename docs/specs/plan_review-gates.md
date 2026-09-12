# Review Gates (v3.8.0) Implementation Plan

> **For agentic workers:** this plan is executed through `/agent-handoff`. Each row in *Handoff split* is one delegated job; a worker implements only the tasks its row names, runs that task's checks, and does not commit. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship v3.8.0: spec review on every plan, a round cap on both review gates enforced by `resume`, and an informed arbiter ruling when a gate runs out of rounds.

**Architecture:** A new `[review]` section in the config engine carries the two caps; setup and the wizard write it; `delegate-codex.sh resume` resolves it and refuses a round past the cap. Everything else (consensus, escalation, recording) is flow prose in `references/` and `SKILL.md`, because no script can judge a dispute. Receipt schema stays v6.

**Tech Stack:** Python 3 stdlib (`unittest`), Bash (`set -euo pipefail`), single-file HTML/JS wizard, Markdown/JSON prose gated by `check-skill-repo.sh`, `run-test-prompts.py`.

**Spec:** `docs/specs/design_agent-handoff.md` (sections *Spec review — every plan, capped, and last*, *Rework is a resume, and it is bounded*, *The two review gates, and where they escalate*) and `docs/specs/design_agent-handoff-evidence.md` (*The review record is kept in the goal file*, *Arbitration rides in `anomalies`*).

## Global Constraints

- Config keys exactly `spec_max_rounds` (default `1`) and `implementation_max_rounds` (default `3`) in a `[review]` section; integers of at least 1; no session override.
- CLI names exactly: `handoff-config.py set-review --spec-max-rounds N --implementation-max-rounds N`; `handoff-setup.py --spec-max-rounds N --implementation-max-rounds N`.
- A round is a review pass: the original job is pass 1, each `resume` adds one. `resume` refuses when the new round number is greater than the cap.
- `auto_review_spec` is retired: dropped on read, never written, `--spec-review`/`--no-spec-review` removed, `--override deep_reasoner.auto_review_spec=…` refused.
- Labels exactly `spec-review` (spec chain root), `arbitrate-<task-id>`, `arbitrate-spec`. Task statuses add `rework-<n>`, `arbitration`, `rejected`; `taken-back` stays for monitoring anomalies.
- `schema_version` stays `2`. `receipt_schema_version` stays `6`.
- The repo is English-only. Risky command text (`git reset --hard`, `rm -rf`, `force push`, `--force`) never appears in new docs, and in `test-prompts.json` only inside `must_not`.
- Python stdlib only; tests are `unittest`. Match surrounding style and comment density.
- Workers do not commit. The driver commits after its review of each row.
- Version strings move to 3.8.0 only in Task 6, all together: `SKILL.md` frontmatter, README badge, `CHANGELOG.md`, `docs/releases/v3.8.0.md`.

## Handoff split

| Row | Plan tasks | Identity | Depends | Why this split |
| --- | --- | --- | --- | --- |
| R1 | Task 1, Task 2 | `fast_worker` | - | Coupled: removing the spec-review constant from the engine breaks setup and the wizard until Task 2 lands. Code and tests are given below. |
| R2 | Task 3 | `fast_worker` | R1 | `resume` reads `review` from `resolve`, which exists only after R1. |
| R3 | Task 4, Task 5 | `fast_worker` | - | Prose and prompt cases; every block is given verbatim below. Files are disjoint from R1, so it runs in parallel. |
| R4 | Task 6 | `fast_worker` | R1, R2, R3 | User-facing docs and the version bump describe shipped behaviour. |
| R5 | Task 7 | `-` (driver) | R4 | Integration, full CI, de-slop pass, final review against the design. |

## File map

| File | Task | Change |
| --- | --- | --- |
| `scripts/handoff-config.py` | 1 | `[review]` parse/validate/resolve/write, `set-review`, retire `auto_review_spec` |
| `tests/test_handoff_config.py` | 1 | replace `SpecReviewFieldTests`; add review-section and retired-field tests |
| `docs/specs/design_agent-identities-and-config.md` | 1 | document `[review]`, ownership, retired key |
| `scripts/handoff-setup.py` | 2 | `--spec-max-rounds`/`--implementation-max-rounds`, write `[review]`, status line, interactive prompts; drop spec-review toggle |
| `scripts/handoff-setup-ui.py` | 2 | Review gates fields replace the spec-review checkbox |
| `tests/test_handoff_setup.py`, `tests/test_handoff_setup_ui.py` | 2 | replace spec-review toggle tests |
| `references/setup.md` | 2 | setup rule for the two caps |
| `scripts/delegate-codex.sh` | 3 | `resume` walks `parent=`, enforces the cap |
| `tests/test_delegate_role.py` | 3 | cap tests on all three backends |
| `references/claude-driven.md`, `references/handoff-template.md`, `references/goal-template.md`, `references/e2e-gauntlet.md`, `references/memory-protocol.md`, `SKILL.md`, `CLAUDE.md`, `docs/receipt-schema.json` | 4 | flow contract |
| `test-prompts.json` | 5 | rewrite spec-review and fix-round cases; add escalation cases |
| `README.md`, `docs/user-guide/agent-handoff.html`, `docs/user-guide/diagrams/*.svg`, `CHANGELOG.md`, `docs/releases/v3.8.0.md`, `SKILL.md` (version line) | 6 | user-facing docs, version 3.8.0 |

---

### Task 1: Config engine — `[review]` section and the retired toggle

**Files:**
- Modify: `scripts/handoff-config.py`
- Modify: `tests/test_handoff_config.py` (replace `class SpecReviewFieldTests` entirely)
- Modify: `docs/specs/design_agent-identities-and-config.md`

**Interfaces:**
- Produces: `REVIEW_FIELDS = ("spec_max_rounds", "implementation_max_rounds")`, `DEFAULT_REVIEW: Dict[str, int]`, `update_review(text: str, review: Mapping[str, int], host: str = HOST, *, path: Optional[Path] = None) -> str`, `write_review_config(path: Path, review: Mapping[str, int]) -> str`. `resolve_config(...)` returns a top-level `"review"` mapping with both keys always present, and raises `ConfigError` when `session_override` carries `review`. No writer (`update_host`, `update_review`, `emit_host_sections`) ever emits `auto_review_spec`. `parse_config`/`validate_config` return `"review"` only when the file has the section.
- Removed: `auto_review_spec` from `IDENTITY_FIELD_ORDER` and `BOOLEAN_FIELDS`; the `set --spec-review/--no-spec-review` flags. `SPEC_REVIEW_IDENTITY` stays in this task (setup and the wizard still import it) and is deleted at the end of Task 2.

- [ ] **Step 1: Write the failing tests.** Delete `class SpecReviewFieldTests` (it asserts the old toggle) and add:

```python
class RetiredSpecReviewFieldTests(unittest.TestCase):
    LEGACY = (
        "schema_version = 2\n"
        "revision = 0\n"
        "[hosts.claude_code.identities.deep_reasoner]\n"
        'backend = "claude"\n'
        'model = "opus"\n'
        'effort = "high"\n'
        "auto_review_spec = true\n"
    )

    def test_dropped_on_read(self):
        parsed = handoff_config.validate_config(self.LEGACY)
        identity = parsed["hosts"][handoff_config.HOST]["identities"]["deep_reasoner"]
        self.assertNotIn("auto_review_spec", identity)

    def test_dropped_even_where_it_used_to_be_invalid(self):
        text = self.LEGACY.replace("deep_reasoner", "fast_worker").replace(
            "auto_review_spec = true", 'auto_review_spec = "yes"'
        )
        parsed = handoff_config.validate_config(text)
        identity = parsed["hosts"][handoff_config.HOST]["identities"]["fast_worker"]
        self.assertNotIn("auto_review_spec", identity)

    def test_next_write_removes_it(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".handoff" / "config.toml"
            path.parent.mkdir()
            path.write_text(self.LEGACY, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                status = handoff_config.main(
                    ["--repo", directory, "set", "--role", "deep_reasoner", "--effort", "max"]
                )
            self.assertEqual(0, status)
            self.assertNotIn("auto_review_spec", path.read_text(encoding="utf-8"))

    def test_set_review_removes_it_too(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".handoff" / "config.toml"
            path.parent.mkdir()
            path.write_text(self.LEGACY, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                status = handoff_config.main(
                    ["--repo", directory, "set-review", "--spec-max-rounds", "2"]
                )
            self.assertEqual(0, status)
            written = path.read_text(encoding="utf-8")
            self.assertNotIn("auto_review_spec", written)
            self.assertIn("[review]\nspec_max_rounds = 2\n", written)

    def test_the_public_writer_never_emits_it(self):
        text = handoff_config.update_host(
            "",
            identities={
                "deep_reasoner": {
                    "backend": "claude",
                    "model": "opus",
                    "effort": "high",
                    "auto_review_spec": True,
                }
            },
        )
        self.assertNotIn("auto_review_spec", text)

    def test_override_and_cli_flag_are_refused(self):
        with self.assertRaisesRegex(handoff_config.ConfigError, "IDENTITY.FIELD"):
            handoff_config._parse_override(["deep_reasoner.auto_review_spec=true"])
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            handoff_config.main(["set", "--role", "deep_reasoner", "--spec-review"])


class ReviewSectionTests(unittest.TestCase):
    BASE = (
        "schema_version = 2\n"
        "revision = 0\n"
        "\n"
        "[hosts.claude_code.identities.fast_worker]\n"
        'backend = "codex"\n'
        'model = "gpt-test"\n'
        'effort = "medium"\n'
        "\n"
        "[routing]\n"
        "always_on_host_rules = false # keep\n"
        "\n"
        "[future]\n"
        'opaque = "preserve me"\n'
    )

    def env_for(self, directory: str) -> dict:
        return {
            "HOME": str(Path(directory) / "home"),
            "XDG_CONFIG_HOME": str(Path(directory) / "xdg"),
        }

    def test_defaults_resolve_without_a_section(self):
        with tempfile.TemporaryDirectory() as directory:
            resolved = handoff_config.resolve_config(Path(directory), env=self.env_for(directory))
        self.assertEqual({"spec_max_rounds": 1, "implementation_max_rounds": 3}, resolved["review"])

    def test_project_and_global_merge_per_field(self):
        with tempfile.TemporaryDirectory() as directory:
            env = self.env_for(directory)
            global_path = handoff_config.global_config_path(env)
            global_path.parent.mkdir(parents=True)
            global_path.write_text(
                "schema_version = 2\nrevision = 0\n[review]\nspec_max_rounds = 2\n", encoding="utf-8"
            )
            project_path = handoff_config.project_config_path(Path(directory))
            project_path.parent.mkdir()
            project_path.write_text(
                "schema_version = 2\nrevision = 0\n[review]\nimplementation_max_rounds = 5\n",
                encoding="utf-8",
            )
            resolved = handoff_config.resolve_config(Path(directory), env=env)
        self.assertEqual({"spec_max_rounds": 2, "implementation_max_rounds": 5}, resolved["review"])

    def test_invalid_values_fail_closed(self):
        for body in (
            "spec_max_rounds = 0",
            "spec_max_rounds = -1",
            "spec_max_rounds = true",
            'spec_max_rounds = "2"',
            "max_rounds = 2",
        ):
            with self.subTest(body=body), self.assertRaises(handoff_config.ConfigValidationError):
                handoff_config.validate_config(f"schema_version = 2\nrevision = 0\n[review]\n{body}\n")

    def test_duplicate_section_fails_closed(self):
        with self.assertRaises(handoff_config.ConfigParseError):
            handoff_config.validate_config("schema_version = 2\nrevision = 0\n[review]\n[review]\n")

    def test_update_review_appends_then_replaces_in_place_idempotently(self):
        once = handoff_config.update_review(
            self.BASE, {"spec_max_rounds": 1, "implementation_max_rounds": 3}
        )
        self.assertTrue(once.startswith(self.BASE))
        self.assertTrue(once.endswith("\n[review]\nspec_max_rounds = 1\nimplementation_max_rounds = 3\n"))
        twice = handoff_config.update_review(
            once, {"spec_max_rounds": 2, "implementation_max_rounds": 3}
        )
        self.assertEqual(once.replace("spec_max_rounds = 1", "spec_max_rounds = 2"), twice)
        self.assertEqual(
            twice,
            handoff_config.update_review(twice, {"spec_max_rounds": 2, "implementation_max_rounds": 3}),
        )

    def test_section_in_the_middle_is_replaced_where_it_stands(self):
        text = self.BASE.replace("[routing]", "[review]\nspec_max_rounds = 4\n\n[routing]")
        updated = handoff_config.update_review(text, {"spec_max_rounds": 2})
        self.assertEqual(text.replace("spec_max_rounds = 4", "spec_max_rounds = 2"), updated)

    def test_identity_writes_keep_the_review_section_byte_for_byte(self):
        text = handoff_config.update_review(
            self.BASE, {"spec_max_rounds": 2, "implementation_max_rounds": 4}
        )
        updated = handoff_config.update_host(
            text,
            identities={"fast_worker": {"backend": "codex", "model": "gpt-other", "effort": "medium"}},
        )
        self.assertIn("\n[review]\nspec_max_rounds = 2\nimplementation_max_rounds = 4\n", updated)
        self.assertIn('opaque = "preserve me"', updated)

    def test_empty_file_gets_a_valid_document(self):
        text = handoff_config.update_review("", {"spec_max_rounds": 1, "implementation_max_rounds": 3})
        self.assertEqual(1, handoff_config.validate_config(text)["review"]["spec_max_rounds"])

    def test_review_caps_have_no_session_override(self):
        with self.assertRaisesRegex(handoff_config.ConfigError, "IDENTITY.FIELD"):
            handoff_config._parse_override(["review.spec_max_rounds=2"])
        # The public resolver is the boundary that matters: resume reads it.
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(handoff_config.ConfigError, "no session override"):
                handoff_config.resolve_config(
                    Path(directory),
                    env=self.env_for(directory),
                    session_override={"review": {"spec_max_rounds": 8}},
                )

    def test_set_review_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            def cli(*arguments):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    status = handoff_config.main(["--repo", directory, *arguments])
                return status, out.getvalue(), err.getvalue()

            self.assertEqual(0, cli("init")[0])
            self.assertEqual(0, cli("set-review", "--spec-max-rounds", "2")[0])
            path = Path(directory) / ".handoff" / "config.toml"
            self.assertIn("[review]\nspec_max_rounds = 2\n", path.read_text(encoding="utf-8"))
            self.assertEqual("2", cli("get", "review.spec_max_rounds")[1].strip())
            self.assertEqual(0, cli("set-review", "--implementation-max-rounds", "4")[0])
            self.assertIn(
                "spec_max_rounds = 2\nimplementation_max_rounds = 4\n", path.read_text(encoding="utf-8")
            )
            before = path.read_text(encoding="utf-8")
            status, _, error = cli("set-review", "--spec-max-rounds", "0")
            self.assertEqual(2, status)
            self.assertIn("review.spec_max_rounds must be an integer of at least 1", error)
            self.assertEqual(before, path.read_text(encoding="utf-8"))
            self.assertEqual(2, cli("set-review")[0])
```

- [ ] **Step 2: Run and confirm they fail.** `python3 -m unittest tests.test_handoff_config -v` → the new classes fail (`update_review` undefined, `auto_review_spec` still present, `review` missing from `resolve_config`).

- [ ] **Step 3: Implement.** In `scripts/handoff-config.py`:

Module docstring: say the engine parses top-level metadata, `[routing]`, `[review]`, and the identity sections.

Constants (replace the current field-order block and `BOOLEAN_FIELDS`; keep `SPEC_REVIEW_IDENTITY` for now):

```python
IDENTITY_FIELD_ORDER = (
    "backend",
    "model",
    "effort",
    "permission_mode",
    "verified",
    "verified_at",
)
SPEC_REVIEW_IDENTITY = "deep_reasoner"  # removed in Task 2 with its last consumer
BOOLEAN_FIELDS = ("verified",)
# Retired in 3.8.0: spec review runs on every plan. Dropped on read, so an old
# config still loads and the next write removes the key.
RETIRED_IDENTITY_FIELDS = ("auto_review_spec",)
# Round caps for the two review gates. A round is a review pass: the original
# job is pass one and every `resume` adds one. `resume` enforces them.
REVIEW_FIELDS = ("spec_max_rounds", "implementation_max_rounds")
DEFAULT_REVIEW = {"spec_max_rounds": 1, "implementation_max_rounds": 3}
```

`DEFAULTS` gains `"review": dict(DEFAULT_REVIEW),`.

`split_sections`: `interpreted = ("routing", "review", f"hosts.{HOST}.identities")`.

`parse_config`: add a branch after `routing`, and drop retired fields in the identity branch:

```python
        elif chunk.name == "review":
            if chunk.name in seen_sections:
                raise ConfigParseError(chunk.start_line, 1, "duplicate [review] section.")
            seen_sections.add(chunk.name)
            result["review"] = _parse_assignments(chunk)
        elif chunk.name.startswith(identity_prefix):
            ...  # unchanged checks
            fields = _parse_assignments(chunk)
            for retired in RETIRED_IDENTITY_FIELDS:
                fields.pop(retired, None)
            result["hosts"][host]["identities"][identity] = fields
```

`_validate_data`: after the `routing` checks add

```python
    review = data.get("review", {})
    if not isinstance(review, Mapping):
        raise ConfigValidationError("review must be a table")
    for key, value in review.items():
        if key not in REVIEW_FIELDS:
            raise ConfigValidationError(
                f"unsupported key in [review]: {key!r}; expected {', '.join(REVIEW_FIELDS)}"
            )
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise ConfigValidationError(f"review.{key} must be an integer of at least 1")
```

and delete the whole `if "auto_review_spec" in fields:` block.

`_host_overlay`: `for key in ("schema_version", "revision", "routing", "review"):`.

`emit_host_sections`: skip retired keys so no caller can emit one — replace `fields = identities[identity]` with `fields = {key: value for key, value in identities[identity].items() if key not in RETIRED_IDENTITY_FIELDS}`.

`resolve_config`: at the top of the `if session_override:` branch add

```python
        if "review" in session_override:
            raise ConfigError(
                "the review caps have no session override; set them with handoff-config.py set-review"
            )
```

New functions after `update_host`:

```python
def emit_review_section(review: Mapping[str, int]) -> str:
    """Return the canonical [review] section."""

    lines = ["[review]"]
    lines.extend(f"{key} = {_format_value(review[key])}" for key in REVIEW_FIELDS if key in review)
    return "\n".join(lines) + "\n"


def _has_retired_fields(text: str, host: str = HOST) -> bool:
    """Whether any identity chunk still carries a retired key, read from the raw text."""

    prefix = f"hosts.{host}.identities."
    pattern = re.compile(r"^[ \t]*(?:%s)[ \t]*=" % "|".join(RETIRED_IDENTITY_FIELDS), re.MULTILINE)
    return any(
        chunk.name and chunk.name.startswith(prefix) and pattern.search(chunk.text)
        for chunk in split_sections(text)
    )


def update_review(
    text: str,
    review: Mapping[str, int],
    host: str = HOST,
    *,
    path: Optional[Path] = None,
) -> str:
    """Replace the [review] chunk in place, or append one, preserving every other chunk."""

    if not text:
        text = update_host("", host, {}, path=path)
    elif _has_retired_fields(text, host):
        # Re-emit the identity sections so this write also drops the retired key.
        # Only then: update_host normalizes blank lines, which would otherwise
        # break byte preservation for files that need no cleanup.
        identities = parse_config(text, host, path=path)["hosts"][host]["identities"]
        text = update_host(text, host, identities, path=path)
    emitted = emit_review_section(review)
    kept: List[str] = []
    replaced = False
    for chunk in split_sections(text):
        if chunk.name != "review":
            kept.append(chunk.text)
            continue
        if not replaced:
            # Keep the blank lines that separated the old section from the next one.
            separator = chunk.text[len(chunk.text.rstrip()):] or "\n"
            kept.append(emitted.rstrip("\n") + separator)
            replaced = True
    candidate = "".join(kept)
    if not replaced:
        if candidate and not candidate.endswith("\n"):
            candidate += "\n"
        candidate += ("\n" if candidate else "") + emitted
    validate_config(candidate, host, path=path)
    return candidate


def write_review_config(path: Path, review: Mapping[str, int]) -> str:
    """Lock, read, replace the [review] section, and atomically persist."""

    with ConfigLock(path):
        current = _read_text(path) if Path(path).exists() else ""
        updated = update_review(current, review, path=Path(path))
        atomic_write(Path(path), updated)
    return updated
```

`build_parser`: delete the `spec_review` mutually exclusive group and `set_defaults(auto_review_spec=None)`; add

```python
    review_parser = subparsers.add_parser(
        "set-review", help="Set the review round caps, preserving every other chunk byte-for-byte."
    )
    review_parser.add_argument("--spec-max-rounds", dest="spec_max_rounds", type=int, metavar="N")
    review_parser.add_argument(
        "--implementation-max-rounds", dest="implementation_max_rounds", type=int, metavar="N"
    )
```

`main`: remove `"auto_review_spec": args.auto_review_spec,` from the `set` updates, and add after the `get` branch:

```python
        if args.command == "set-review":
            updates = {
                key: getattr(args, key) for key in REVIEW_FIELDS if getattr(args, key) is not None
            }
            if not updates:
                raise ConfigError("set-review needs --spec-max-rounds and/or --implementation-max-rounds")
            review = dict(data.get("review", {}))
            review.update(updates)
            write_review_config(path, review)
            print(path)
            return 0
```

If an existing `ResolveTests` assertion compares the whole resolved mapping, add `"review": {"spec_max_rounds": 1, "implementation_max_rounds": 3}` to its expectation rather than loosening the assertion.

- [ ] **Step 4: Run and confirm they pass.** `python3 -m unittest tests.test_handoff_config -v` → all pass. (`tests.test_handoff_setup` may fail until Task 2; that is expected inside row R1.)

- [ ] **Step 5: Update `docs/specs/design_agent-identities-and-config.md`.**
  - Line 3: the writer owns the `hosts.claude_code` namespace **and the `[review]` section**; comments, `[routing]`, and unknown sections stay raw bytes.
  - Replace the `auto_review_spec` paragraph (line 13) with: "`deep_reasoner` no longer carries a spec-review toggle. Until 3.8.0 it had `auto_review_spec`; spec review now runs on every plan, and the parser drops the retired key on read, so an old config still loads and the next write removes it."
  - Add after it: "The `[review]` section holds the round caps for the two review gates (`docs/specs/design_agent-handoff.md`, *The two review gates, and where they escalate*). Both keys are optional integers of at least 1; absent values resolve to the built-in defaults, 1 and 3. They merge per field across project, global, and defaults. There is no session override, because `delegate-codex.sh resume` enforces the caps from its own `resolve` call. A pre-3.8 engine ignores the section rather than refusing it."
  - Example: delete the `auto_review_spec = true` line; add before `[routing]`:
    ```toml
    [review]
    spec_max_rounds = 1
    implementation_max_rounds = 3
    ```
  - Fields table: delete the `auto_review_spec` row; add
    `| review.spec_max_rounds | integer ≥ 1 | no | Spec review passes (deep_reasoner reads the plan) before a still-declined blocking finding goes to the arbiter. Default 1. |` and
    `| review.implementation_max_rounds | integer ≥ 1 | no | Driver review passes on a delegated diff (the original job plus each resume) before open findings go to the arbiter. Default 3. |` (with the code spans the table already uses).
  - Ownership: the writer may rewrite `[hosts.claude_code.identities.*]` sections and `[review]`; drop `auto_review_spec` from the field order; add "`[review]` is emitted as `spec_max_rounds`, then `implementation_max_rounds`, replaced where it stands, or appended at the end of the file when absent."
  - Supported subset: the engine parses top-level metadata, `[routing]`, `[review]`, and the identity sections; the array-of-tables guard also names `[review]`.
  - CLI block: delete the `set --role deep_reasoner --spec-review` and `--override deep_reasoner.auto_review_spec=true` lines; add `python3 scripts/handoff-config.py --scope project set-review --spec-max-rounds 1 --implementation-max-rounds 3`. Replace the `--spec-review / --no-spec-review` sentence with: "`set-review` writes the `[review]` section; pass either flag or both, and a value below 1 is refused without writing. `--override` targets identity fields only; the review caps cannot be overridden per call."

---

### Task 2: Setup engine and wizard

**Files:**
- Modify: `scripts/handoff-setup.py`, `scripts/handoff-setup-ui.py`, `references/setup.md`
- Modify: `tests/test_handoff_setup.py` (replace `class SpecReviewToggleTests`), `tests/test_handoff_setup_ui.py` (replace the spec-review tests at lines ~400–455)
- Modify: `scripts/handoff-config.py` (delete `SPEC_REVIEW_IDENTITY` once nothing imports it)

**Interfaces:**
- Consumes: `handoff_config.REVIEW_FIELDS`, `handoff_config.DEFAULT_REVIEW`, `handoff_config.update_review`, `resolve_config(...)["review"]` from Task 1.
- Produces: `handoff-setup.py --spec-max-rounds N --implementation-max-rounds N` (argparse dests `spec_max_rounds`, `implementation_max_rounds`, default `None`); `desired_review(args, current) -> Dict[str, int]`; `--status` prints `review: spec_max_rounds=<n> implementation_max_rounds=<n>`; wizard state key `initial_review`; normalized payload key `review`.

- [ ] **Step 1: Write the failing setup tests.** Replace `class SpecReviewToggleTests` in `tests/test_handoff_setup.py` (add `import contextlib`, `import io` if missing):

```python
class ReviewCapsSetupTests(SetupTests):
    def resolved_review(self):
        return handoff_setup.handoff_config.resolve_config(self.repo, env=self.env)["review"]

    def config_text(self):
        return (self.repo / ".handoff" / "config.toml").read_text(encoding="utf-8")

    def test_default_apply_writes_both_defaults(self):
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertEqual((0, ""), (status, error))
        self.assertIn("[review]\nspec_max_rounds = 1\nimplementation_max_rounds = 3\n", self.config_text())

    def test_flags_set_the_caps_and_a_bare_reapply_keeps_them(self):
        status, _, error = self.run_cli(*self.claude_args(
            "--apply", "--mode", "balanced", "--spec-max-rounds", "2", "--implementation-max-rounds", "4"
        ))
        self.assertEqual((0, ""), (status, error))
        self.assertEqual({"spec_max_rounds": 2, "implementation_max_rounds": 4}, self.resolved_review())
        first = self.config_text()
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertEqual((0, ""), (status, error))
        self.assertEqual(first, self.config_text())

    def test_a_cap_below_one_is_refused_without_writing(self):
        status, _, error = self.run_cli(*self.claude_args(
            "--apply", "--mode", "balanced", "--spec-max-rounds", "0"
        ))
        self.assertNotEqual(0, status)
        self.assertIn("--spec-max-rounds must be at least 1", error)
        self.assertFalse((self.repo / ".handoff" / "config.toml").exists())

    def test_the_retired_toggle_is_dropped_on_the_next_apply(self):
        self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        path = self.repo / ".handoff" / "config.toml"
        header = "[hosts.claude_code.identities.deep_reasoner]\n"
        path.write_text(
            path.read_text(encoding="utf-8").replace(header, header + "auto_review_spec = true\n"),
            encoding="utf-8",
        )
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertEqual((0, ""), (status, error))
        self.assertNotIn("auto_review_spec", self.config_text())

    def test_the_spec_review_flag_is_gone(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            handoff_setup.main(
                list(self.claude_args("--apply", "--mode", "balanced", "--spec-review")), env=self.env
            )

    def test_status_reports_the_caps(self):
        self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        status, output, _ = self.run_cli(*self.claude_args("--status"))
        self.assertEqual(0, status)
        self.assertIn("review: spec_max_rounds=1 implementation_max_rounds=3", output)
        self.assertNotIn("spec_review=", output)

    def test_a_bare_project_apply_keeps_inherited_global_caps(self):
        global_path = handoff_setup.handoff_config.global_config_path(self.env)
        global_path.parent.mkdir(parents=True, exist_ok=True)
        global_path.write_text(
            "schema_version = 2\nrevision = 0\n"
            "[review]\nspec_max_rounds = 2\nimplementation_max_rounds = 5\n",
            encoding="utf-8",
        )
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertEqual((0, ""), (status, error))
        self.assertEqual({"spec_max_rounds": 2, "implementation_max_rounds": 5}, self.resolved_review())
        self.assertIn("[review]\nspec_max_rounds = 2\nimplementation_max_rounds = 5\n", self.config_text())
```

Also update the existing `test_terminal_custom_wizard_asks_each_permission` (the terminal wizard now asks two cap questions where it asked the spec-review question): its answer list `["4", "1", "n", "n", "n", "n"]` becomes `["4", "1", "n", "n", "", "", "n"]` — mode, scope, agents, routing, spec cap (Enter keeps), implementation cap (Enter keeps), e2e.

- [ ] **Step 2: Write the failing wizard tests.** In `tests/test_handoff_setup_ui.py`, add `"review": dict(state["initial_review"]),` to the dict returned by the `payload` helper, delete the spec-review tests (`…spec_review…`, `test_engine_arguments_always_state_the_toggle`, `test_state_seeds_the_checkbox_from_the_written_config`, `test_the_page_wires_the_checkbox`) and add:

```python
    def test_payload_carries_both_review_caps(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        normalized = handoff_setup_ui.normalize_payload(
            self.payload(controller), repo=self.repo, env=self.env
        )
        self.assertEqual({"spec_max_rounds": 1, "implementation_max_rounds": 3}, normalized["review"])

    def test_payload_rejects_bad_caps(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        for bad in (0, -1, True, "2", None, 2.5):
            raw = self.payload(controller)
            raw["review"]["spec_max_rounds"] = bad
            with self.subTest(bad=bad), self.assertRaisesRegex(handoff_setup_ui.UIError, "spec_max_rounds"):
                handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)
        raw = self.payload(controller)
        del raw["review"]
        with self.assertRaisesRegex(handoff_setup_ui.UIError, "review"):
            handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)

    def test_engine_arguments_state_both_caps(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        raw = self.payload(controller)
        raw["review"] = {"spec_max_rounds": 2, "implementation_max_rounds": 5}
        args = handoff_setup_ui.engine_arguments(
            handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env), "apply"
        )
        self.assertEqual("2", args[args.index("--spec-max-rounds") + 1])
        self.assertEqual("5", args[args.index("--implementation-max-rounds") + 1])
        self.assertNotIn("--spec-review", args)
        self.assertNotIn("--no-spec-review", args)

    def test_state_seeds_the_caps_from_the_written_config(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        self.assertEqual(
            {"spec_max_rounds": 1, "implementation_max_rounds": 3}, controller.state()["initial_review"]
        )
        with contextlib.redirect_stdout(io.StringIO()):
            handoff_setup_ui.engine.main(
                ["--apply", "--repo", str(self.repo), "--exclude-choice", "track",
                 "--no-write-agents", "--spec-max-rounds", "2"],
                env=self.env,
            )
        seeded = handoff_setup_ui.SetupController(self.repo, self.env).state()
        self.assertEqual({"spec_max_rounds": 2, "implementation_max_rounds": 3}, seeded["initial_review"])

    def test_the_page_wires_the_caps(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn('id="specMaxRounds"', source)
        self.assertIn('id="implementationMaxRounds"', source)
        self.assertIn("spec_max_rounds: Number($('specMaxRounds').value)", source)
        self.assertIn("implementation_max_rounds: Number($('implementationMaxRounds').value)", source)
        self.assertNotIn("specReview", source)
```

- [ ] **Step 3: Run and confirm they fail.** `python3 -m unittest tests.test_handoff_setup tests.test_handoff_setup_ui -v`.

- [ ] **Step 4: Implement `scripts/handoff-setup.py`.**
  - Delete `SPEC_REVIEW_IDENTITY = handoff_config.SPEC_REVIEW_IDENTITY`, the whole `apply_spec_review` function, and both `apply_spec_review(identities, args)` calls in `choose_identities`.
  - `AGENT_TEXT["arbiter"]` (the generated `handoff-arbiter` agent) becomes:
    ```python
    "arbiter": (
        "Independent arbiter: the blind second solver for contested calls, and the judge "
        "of a review dispute that ran out of rounds.",
        "For a blind solve, solve the received problem independently and treat any packet containing another answer, conclusion, or hint as contaminated; report it instead of using it. For an escalation ruling the packet carries both sides on purpose: rule on the open findings, and start your answer with exactly `verdict: approve` or `verdict: reject`.",
    ),
    ```
  - Add near `choose_identities`:
    ```python
    def desired_review(
        args: argparse.Namespace, env: Mapping[str, str], current: Mapping[str, Any]
    ) -> Dict[str, int]:
        """Explicit flags win, then this file's values, then what it inherits, then the default.

        A project file inherits the global caps, the same resolution the wizard
        seeds from, so a bare terminal apply never pins the defaults over them.
        """

        review = dict(handoff_config.DEFAULT_REVIEW)
        if args.scope == "project":
            global_path = handoff_config.global_config_path(env)
            if global_path.is_file():
                inherited = handoff_config.validate_config(read_text(global_path), path=global_path)
                review.update(inherited.get("review", {}))
        review.update(current)
        for key in handoff_config.REVIEW_FIELDS:
            value = getattr(args, key, None)
            if value is None:
                continue
            if value < 1:
                raise SetupError(f"--{key.replace('_', '-')} must be at least 1")
            review[key] = value
        return review
    ```
  - `build_plan`: declare `current_review: Mapping[str, Any] = {}` next to `current_identities`; in the `if old_config:` branch set `current_review = parsed.get("review", {})`; after that `if legacy / else` block and before the first `update_host` call, compute `review = desired_review(args, env, current_review)` (`build_plan` writes nothing, so a bad flag still fails before any write); after the second `update_host` call add `new_config = handoff_config.update_review(new_config, review, path=path)`.
  - `show_status`: delete the `spec_review` suffix; after the `config_source=` line print
    ```python
    review = resolved["review"]
    print(
        f"review: spec_max_rounds={review['spec_max_rounds']} "
        f"implementation_max_rounds={review['implementation_max_rounds']}"
    )
    ```
  - `interactive`: replace the `spec_review = input(...)` prompt with
    ```python
    def review_passes(gate: str) -> Optional[int]:
        answer = input(f"{gate} review passes before the arbiter rules (Enter keeps the current value): ").strip()
        if not answer:
            return None
        if not answer.isdigit():
            raise SetupError(f"{gate} review passes must be a whole number")
        return int(answer)

    spec_max_rounds = review_passes("Spec")
    implementation_max_rounds = review_passes("Implementation")
    ```
    and replace `selected.spec_review = spec_review` with `selected.spec_max_rounds, selected.implementation_max_rounds = spec_max_rounds, implementation_max_rounds`.
  - `build_parser`: replace the `--spec-review/--no-spec-review` group and `set_defaults(spec_review=False)` with
    ```python
    parser.add_argument("--spec-max-rounds", type=int, metavar="N", help="Spec review passes before the arbiter rules on a declined blocking finding (default: keep the current value, else 1).")
    parser.add_argument("--implementation-max-rounds", type=int, metavar="N", help="Driver review passes on a delegated diff before the arbiter rules (default: keep the current value, else 3).")
    ```

- [ ] **Step 5: Implement `scripts/handoff-setup-ui.py`.**
  - `build_state`: replace `"initial_spec_review": …` and `"spec_review_identity": …` with `"initial_review": dict(resolved["review"]),` (`resolved` is the `resolve_config` result `build_state` already reads `identities` from).
  - `normalize_payload`: before the `return`, add
    ```python
    review = raw.get("review")
    if not isinstance(review, dict):
        raise UIError("Missing review gate settings")
    caps: Dict[str, int] = {}
    for key in engine.handoff_config.REVIEW_FIELDS:
        value = review.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise UIError(f"{key} must be a whole number of at least 1")
        caps[key] = value
    ```
    and replace `"spec_review": bool(raw.get("spec_review")),` with `"review": caps,`.
  - `engine_arguments`: replace the `--spec-review/--no-spec-review` line with
    ```python
    for key in engine.handoff_config.REVIEW_FIELDS:
        args.extend((f"--{key.replace('_', '-')}", str(payload["review"][key])))
    ```
  - Markup: directly after the closing `</details>` of `<details id="e2eAddOn" …>`, add
    ```html
          <div class="review-gates">
            <h3>Review gates</h3>
            <p>Every plan gets one read from deep_reasoner before you see it, and every delegated diff gets the driver's review. When a gate runs out of passes without agreement, the arbiter rules.</p>
            <div class="review-caps">
              <div class="field"><label for="specMaxRounds">Spec review passes</label><input id="specMaxRounds" type="number" min="1" step="1" inputmode="numeric"></div>
              <div class="field"><label for="implementationMaxRounds">Implementation review passes</label><input id="implementationMaxRounds" type="number" min="1" step="1" inputmode="numeric"></div>
            </div>
          </div>
    ```
    CSS next to the `.e2e-toggle` rules: `.review-gates { margin:18px 0 0; } .review-caps { display:flex; flex-wrap:wrap; gap:12px; } .review-caps .field { flex:1 1 180px; }`. Remove any `.review-addon` rules.
  - JS: delete the `${identity === state.spec_review_identity ? reviewAddon() : ''}` line in `renderCards`, the `reviewAddon` function, and `$('specReview').addEventListener('change', invalidate);`. In the function that ends with `$('configWorkspace').setAttribute('aria-busy', 'false');`, before that line, add `$('specMaxRounds').value = state.initial_review.spec_max_rounds; $('implementationMaxRounds').value = state.initial_review.implementation_max_rounds;`. After the `$('withE2e').addEventListener(…)` block add `['specMaxRounds', 'implementationMaxRounds'].forEach(id => $(id).addEventListener('input', invalidate));`. In `payload()` replace `spec_review: $('specReview').checked,` with
    ```js
        review: {
          spec_max_rounds: Number($('specMaxRounds').value),
          implementation_max_rounds: Number($('implementationMaxRounds').value),
        },
    ```
- [ ] **Step 6: Delete `SPEC_REVIEW_IDENTITY`** from `scripts/handoff-config.py`. `rg -n "SPEC_REVIEW_IDENTITY|spec_review|specReview|auto_review_spec" scripts tests` → only the `RETIRED_IDENTITY_FIELDS` line and the tests that assert the removal remain.

- [ ] **Step 7: Update `references/setup.md`.** Replace the rule bullet that starts "The page carries one more optional toggle" with: "- The page carries one more group, Review gates: two whole numbers, spec review passes (`--spec-max-rounds`, default 1) and implementation review passes (`--implementation-max-rounds`, default 3). Seed both from the resolved config so re-running setup never silently resets them. They are not routing values; changing them leaves `verified` alone."

- [ ] **Step 8: Run.** `python3 -m unittest tests.test_handoff_config tests.test_handoff_setup tests.test_handoff_setup_ui -v` → all pass. `python3 -m unittest discover -s tests` → all pass. `python3 scripts/handoff-setup-ui.py --help` exits 0.

- [ ] **Step 9 (driver, after review): commit** `feat: [review] round caps in config, setup, and the wizard; retire auto_review_spec`.

---

### Task 3: `resume` enforces the cap

**Files:**
- Modify: `scripts/delegate-codex.sh` (`resume` function and usage text)
- Modify: `tests/test_delegate_role.py` (`BackendLifecycle`, so every case runs on codex, claude, and copilot)

**Interfaces:**
- Consumes: `handoff-config.py resolve` JSON with `review.spec_max_rounds` and `review.implementation_max_rounds` (Task 1).
- Produces: `resume` exits 1 with `ERROR: review cap reached: <rootId> has had <cap> review passes (<spec|implementation> gate, …); escalate to the arbiter …` before any job directory exists; resumed job ids are `<rootId>-r<round>` where the round is counted from the `parent=` chain.

- [ ] **Step 1: Write the failing tests.** Add `import shutil` to the imports and these methods to `BackendLifecycle`:

```python
    def resume(self, job_id: str):
        return self.delegate(
            "resume", job_id, "--repo", str(self.repo), "--prompt-file", str(self.prompt)
        )

    def configure_review(self, spec: int, implementation: int):
        path = self.repo / ".handoff" / "config.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "schema_version = 2\nrevision = 0\n"
            f"[review]\nspec_max_rounds = {spec}\nimplementation_max_rounds = {implementation}\n",
            encoding="utf-8",
        )

    def test_implementation_chain_stops_at_the_default_cap(self):
        job_id = self.submit("--label", "T1")
        self.finish(job_id)
        second = self.resume(job_id)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(f"{job_id}-r2", second.stdout.strip())
        self.finish(f"{job_id}-r2")
        third = self.resume(f"{job_id}-r2")
        self.assertEqual(0, third.returncode, third.stderr)
        self.assertEqual(f"{job_id}-r3", third.stdout.strip())
        self.finish(f"{job_id}-r3")
        refused = self.resume(f"{job_id}-r3")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("review cap reached", refused.stderr)
        self.assertIn("implementation gate", refused.stderr)
        self.assertIn("escalate to the arbiter", refused.stderr)
        self.assertFalse(self.job_dir(f"{job_id}-r4").exists())

    def test_spec_review_chain_uses_the_spec_cap(self):
        job_id = self.submit("--label", "spec-review", "--read-only")
        self.finish(job_id)
        refused = self.resume(job_id)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("spec gate", refused.stderr)
        self.assertFalse(self.job_dir(f"{job_id}-r2").exists())

    def test_configured_caps_are_honoured(self):
        self.configure_review(spec=2, implementation=1)
        spec = self.submit("--label", "spec-review", "--read-only")
        self.finish(spec)
        allowed = self.resume(spec)
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        implementation = self.submit("--label", "T2")
        self.finish(implementation)
        self.assertIn("implementation gate", self.resume(implementation).stderr)

    def test_a_fresh_label_ending_in_a_round_suffix_is_round_one(self):
        job_id = self.submit("--label", "fix-r2")
        self.finish(job_id)
        result = self.resume(job_id)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"{job_id}-r2", result.stdout.strip())

    def test_a_missing_ancestor_fails_closed(self):
        job_id = self.submit("--label", "T3")
        self.finish(job_id)
        child = self.resume(job_id).stdout.strip()
        self.finish(child)
        shutil.rmtree(self.job_dir(job_id))
        refused = self.resume(child)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("ancestor job", refused.stderr)

    def test_a_resume_with_no_parent_fails_closed(self):
        job_id = self.submit("--label", "T5")
        self.finish(job_id)
        child = self.resume(job_id).stdout.strip()
        self.finish(child)
        meta = self.job_dir(child) / "meta"
        meta.write_text(
            "".join(
                line for line in meta.read_text(encoding="utf-8").splitlines(True)
                if not line.startswith("parent=")
            ),
            encoding="utf-8",
        )
        refused = self.resume(child)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("no parent", refused.stderr)

    def test_a_parent_cycle_fails_closed(self):
        job_id = self.submit("--label", "T6")
        self.finish(job_id)
        child = self.resume(job_id).stdout.strip()
        self.finish(child)
        root_meta = self.job_dir(job_id) / "meta"
        root_meta.write_text(
            root_meta.read_text(encoding="utf-8").replace("mode=fresh", "mode=resume")
            + f"parent={child}\n",
            encoding="utf-8",
        )
        refused = self.resume(child)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("loops", refused.stderr)

    def test_an_invalid_config_blocks_a_fix_round(self):
        job_id = self.submit("--label", "T4")
        self.finish(job_id)
        self.configure_review(spec=0, implementation=3)
        refused = self.resume(job_id)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("review caps", refused.stderr)
```

If an existing test resumes one chain past round 3, call `self.configure_review(spec=1, implementation=<enough>)` at its start instead of weakening it.

- [ ] **Step 2: Run and confirm they fail.** `python3 -m unittest tests.test_delegate_role -v` → the new cases fail (`-r4` is created; `fix-r2` resumes as `…-fix-r3`).

- [ ] **Step 3: Implement.** In the `resume` function of `scripts/delegate-codex.sh`, directly after `[ "$(job_state)" = "RUNNING" ] && die "parent job still running; wait or cancel first"` and `local PARENT_JOB="$JOB"`, add:

```bash
  # The review cap counts real resume ancestry. The -r<n> suffix is label text
  # a fresh job can carry too, so walk parent= back to the chain's first job,
  # failing closed on anything that would make the count wrong.
  local ROOT_JOB="$PARENT_JOB" ROUND=2 ANCESTOR SEEN=" $PARENT_ID "
  while :; do
    [ -f "$ROOT_JOB/meta" ] || die "cannot count review rounds: $(basename "$ROOT_JOB") has no meta"
    ANCESTOR="$(meta_value parent "$ROOT_JOB")"
    if [ -z "$ANCESTOR" ]; then
      [ "$(meta_value mode "$ROOT_JOB")" != "resume" ] \
        || die "cannot count review rounds: $(basename "$ROOT_JOB") is a resume with no parent"
      break
    fi
    case "$SEEN" in *" $ANCESTOR "*) die "cannot count review rounds: the parent chain loops at $ANCESTOR" ;; esac
    SEEN="$SEEN$ANCESTOR "
    ROOT_JOB="$(job_dir "$ANCESTOR")"
    [ -d "$ROOT_JOB" ] || die "cannot count review rounds: ancestor job $ANCESTOR is missing"
    ROUND=$((ROUND + 1))
  done
  local ROOT_ID GATE="implementation" CAP
  ROOT_ID="$(basename "$ROOT_JOB")"
  if [ "$(meta_value label "$ROOT_JOB")" = "spec-review" ]; then
    GATE="spec"
  fi
  if ! CAP="$(python3 "$SCRIPT_DIR/handoff-config.py" --repo "$REPO" resolve \
      | python3 -c 'import json, sys; print(json.load(sys.stdin)["review"][sys.argv[1] + "_max_rounds"])' "$GATE")"; then
    die "failed to resolve the review caps from Handoff config; run 'python3 scripts/handoff-config.py validate'"
  fi
  if [ "$ROUND" -gt "$CAP" ]; then
    die "review cap reached: $ROOT_ID has had $CAP review passes ($GATE gate, [review] ${GATE}_max_rounds = $CAP); escalate to the arbiter instead of resuming (references/claude-driven.md, Phase 4)"
  fi
```

Then replace the old round arithmetic

```bash
  local ROUND=2
  case "$PARENT_ID" in *-r[0-9]*) ROUND=$(( ${PARENT_ID##*-r} + 1 )) ;; esac
  local JOB_ID="${PARENT_ID%-r[0-9]*}-r${ROUND}"
```

with `local JOB_ID="${ROOT_ID}-r${ROUND}"`.

In `usage()`, after the `resume` line's block of explanation (next to the `--role` paragraph), add: "resume refuses a round past the review cap in Handoff config: [review] spec_max_rounds for a chain whose first job is labelled spec-review, implementation_max_rounds for any other. A round is a review pass; the first job is round 1. Escalate to the arbiter instead."

- [ ] **Step 4: Run.** `bash -n scripts/delegate-codex.sh` → no output. `python3 -m unittest tests.test_delegate_role -v` → all pass on the codex, claude, and copilot subclasses. `python3 -m unittest discover -s tests` → all pass.

- [ ] **Step 5 (driver, after review): commit** `feat: resume enforces the review round cap`.

---

### Task 4: Flow contract prose

**Files:** `references/claude-driven.md`, `references/handoff-template.md`, `references/goal-template.md`, `references/e2e-gauntlet.md`, `references/memory-protocol.md`, `SKILL.md`, `CLAUDE.md`, `docs/receipt-schema.json`

**Interfaces:** Uses the names in *Global Constraints* verbatim. Produces the Arbitration Packet and the `## Arbitration` goal block that Task 5's prompt cases refer to.

- [ ] **Step 1: `references/claude-driven.md`.**

  (a) Phase 0, first bullet: after "…so a resumed run keeps the start it already had." add: " Reopening a row the arbiter rejected is the exception: that run already emitted its receipt, so reopening it is a new run and stamps a new start. On `/agent-handoff resume`, present rows marked `rejected` first, with their `## Arbitration` line, the handover in Notes, and the saved patch or kept worktree, and never reopen one yourself."

  (b) Phase 1: replace everything from the bullet starting `- Spec review (optional, once):` through the paragraph ending "…exactly as the arbiter protocol does." with:

~~~~markdown
- Spec review (every plan): before the plan reaches the user, give it one read from `deep_reasoner`. There is no toggle. Build the Spec Review Packet from `references/handoff-template.md` and send it through the identity's configured backend:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label spec-review \
  --role deep_reasoner --read-only
```

  `--read-only` becomes `-s read-only` on codex, `--permission-mode plan` on claude, and `--mode plan` on copilot, so the review is a real job with a jobId on any of the three backends. Keep the label exactly `spec-review`: `resume` reads it off the chain's first job to pick the cap.

  The reviewer tags each finding `blocking` or `advisory`. Accept or decline each one and write down why. Consensus means no blocking finding was declined; a declined advisory finding is recorded and goes no further. Under the cap (`[review] spec_max_rounds`, default 1, from `handoff-config.py resolve`), `resume` the same spec-review job with the revised plan, the declined blocking findings, and your reasons; the reviewer withdraws or keeps each one. At the cap with a blocking finding still declined, escalate (Arbiter Protocol — Escalation Ruling). An approval sends the plan to the user as you revised it. A rejection is final: no task row is delegated, the run stops before Phase 2, the user gets the plan with the ruling, and wrap up still emits a receipt with phase `planning`.

  A spec-review job that fails, stalls, or returns neither findings nor a one-line "sound" verdict is not a round and never counts as consensus. Retry it once as a fresh job with the same packet. A second failure sets the status to `failed`, and Phase 2 waits for the user.

  Track the review in the goal file's `## Spec Review` block: a `status:` line (`not run`, `round <n> running`, `awaiting arbitration`, or one of the terminal `consensus`, `approved`, `rejected`, `failed`) and one line per round with its jobId and your disposition of each blocking finding. Any status other than `not run` means the automatic review is spent for this run: an adjusted plan, a thin review, and a resumed session never start a new one, and a resumed session that finds a non-terminal status finishes that chain. Only an explicit user request starts a new chain. The reviewer is read-only, and its findings are input to your judgment, never a verdict you apply unread. It is not blind: the plan under review is your own answer, so the contamination rule does not apply. When `deep_reasoner` resolves to the driver's own vendor the review still runs, and the receipt notes `same-vendor`.
~~~~

  (c) Blind arbitration: append to the paragraph "This is distinct from the Phase 1 gate: …": " It is also distinct from the escalation ruling below, which sees both sides of a dispute on purpose."

  (d) After the Blind Arbitration section, add:

~~~~markdown
## Arbiter Protocol — Escalation Ruling

When a review gate reaches its cap without consensus (the spec gate with a blocking finding still declined, or the implementation gate with findings still open after the last allowed pass), escalate to `arbiter`. Always: a disputed task is never taken back instead.

1. Build the Arbitration Packet from `references/handoff-template.md` with the whole dispute: the plan or the task brief, the acceptance criteria, the evidence (the revised plan, or the full scoped diff with the results of the checks you ran), and every round's findings with the other side's response. Nothing is withheld. This is not blind arbitration, and the contamination rule does not apply.
2. Submit it as a read-only job: `delegate-codex.sh submit --role arbiter --read-only --label arbitrate-<task-id>` (`arbitrate-spec` for the plan).
3. A verdict is well formed only when the output's first line is exactly `verdict: approve` or `verdict: reject` and the reasons do not contradict it. The ruling binds; do not re-argue it.
   - Spec gate: approve sends the plan to the user as revised; reject stops the run before Phase 2.
   - Implementation gate: approve accepts the diff, overruling your open findings, and the row goes to `done`. An approval never stands in for an e2e PASS: with a validated FAIL among the open findings, approval means the scenario was judged wrong, so the scenario goes back to step 1 of the e2e sequence, a new hash is recorded, and the verifier reruns. Reject is final: the row becomes `rejected` and its diff is set aside (Phase 4).
4. An arbiter job that fails, stalls, is cancelled, or returns anything else is an anomaly. Resubmit one fresh arbiter job with the same packet, and never take the dispute back: a takeback settles it for the side that raised it. A second failure, or a `submit` refused before any job exists, is recorded as `no verdict`. The row, or the plan, stays in `arbitration`, the run wraps up, and only the user moves it.
5. Record every ruling, approve included, as one line in the goal file's `## Arbitration` block: gate, task (`plan` for the spec gate), the arbiter's jobId or the refusal message, the verdict (`approve`, `reject`, or `no verdict`), a one-line reason, and `same-vendor` when the arbiter's backend matches either party's. A rejection also writes a handover into Notes: what the reviewer found, what the arbiter ruled, and what continuing would take (raise the cap, rewrite the brief, or take the task over).
~~~~

  (e) Phase 3, anomaly bullet: after "Record the anomaly for the receipt." add " An arbiter job is the exception: resubmit it once and never take the dispute back (Escalation Ruling)."

  (f) Phase 4: replace the bullet "- Maximum two fix rounds per task. Still failing after that: take the task back … independent third-party gate." with:

~~~~markdown
- The cap is `[review] implementation_max_rounds` review passes (default 3): the original job is pass one and each `resume` adds one, so the default allows two fix rounds. It applies to every delegated implementation row, whatever its identity. `resume` refuses a round past the cap before creating the job; never get around it with a fresh `submit`. An e2e FAIL is a round on the implementation row's chain. A rerun after BLOCKED is a fresh verifier `submit` pinned to the same commit, never a `resume`, so it is not a round.
- With e2e rows, an implementation row's `done` is provisional until the verifier's PASS: a FAIL moves it back to `rework-<n>`, sends the verifier row back to `pending`, and holds every other row that depends on it. A dependent already running may finish; its result is not accepted until the row is `done` again.
- Findings still open after the last allowed pass: escalate (Escalation Ruling). Do not take the task back. On a rejection, set the diff aside so independent rows do not run on top of it: in the main repo, save the row's scoped changes, new files included, to `.handoff/rejected/<task-id>.patch` and return those paths to their state before the job; a worktree row keeps its worktree (skip `cleanup`); a row already integrated onto the feature branch is backed out with a revert commit, never a history rewrite. Rows that depend on it are held; independent rows continue.
- Optionally run the gstack `/codex` review on the final combined diff as an independent third-party gate.
~~~~

  (g) Phase 5: the first bullet "- Mark tasks done in `.handoff/goal.md`; stop any remaining `/loop`." becomes "- Mark accepted tasks done in `.handoff/goal.md`; leave rows in `rejected` or `arbitration` as they are, because dependency holds and the user's decision on resume read them; stop any remaining `/loop`." Receipt bullet: append "Put every `## Arbitration` line into `--anomalies` as `arbitration: <task> approve|reject|no verdict (<jobId>)`, joined with any other anomalies by `; ` on one line; the receipt takes one line per field." In the next bullet, "E2E roles and a spec-review job appear in `roles_used`" becomes "E2E roles, the spec-review job, and any arbiter ruling appear in `roles_used`". Memory-protocol bullet: "rework rounds" becomes "rework rounds, escalations and their verdicts".

- [ ] **Step 2: `references/handoff-template.md`.**
  - Intro: "Three packets, all bounded: …" becomes "Four packets, all bounded: the delegation packet the driver sends to a delegated job, the spec review packet it sends to `deep_reasoner` during planning, the arbitration packet it sends to `arbiter` when a review gate runs out of rounds, and the Goal Packet it sends to the user for authorization."
  - Delegation packet rules, fix-round bullet: append " A fix round past `[review] implementation_max_rounds` is refused by `resume`; the dispute goes to the Arbitration Packet instead."
  - Heading `## Spec Review Packet (Phase 1, optional)` becomes `## Spec Review Packet (Phase 1, every plan)`. First paragraph: replace "Use this packet when `deep_reasoner` reviews the plan before it reaches the user — automatically when its config carries `auto_review_spec = true`, or on request. It runs once per run." with "Use this packet on every plan, before it reaches the user. A later round under the cap is a `resume` of the same job carrying the revised plan, the declined blocking findings, and your reasons, not a new packet."
  - Packet Acceptance, first bullet: "- Prioritized findings, worst first, each tagged `blocking` (the plan should not go ahead as written) or `advisory`, each naming what breaks and where."
  - Spec review rules: "**The driver rules.**" bullet: append " A declined blocking finding that survives the cap is the one call you do not make alone: it goes to the arbiter." Replace the "**Once per run.**" bullet with "- **Once per run, capped.** Record every round in the goal file's `## Spec Review` block: a status line and one line per round. Any status other than `not run` means the automatic review is spent. Declined blocking findings go back to the same session while under `[review] spec_max_rounds`, and to the Arbitration Packet at the cap."
  - Add before `## Goal Packet …`:

~~~~markdown
## Arbitration Packet (escalation ruling)

Use this packet when a review gate reaches its cap without consensus. Send it as a read-only job to `arbiter`: `delegate-codex.sh submit --role arbiter --read-only --label arbitrate-<task-id>` (`arbitrate-spec` for the plan). Unlike blind arbitration, nothing is withheld: the arbiter judges a dispute and needs both sides of it.

```markdown
# Handoff Arbitration

## Context
I'm working on [the larger task] for [who it's for]. A review gate ran out
of rounds without agreement, and your ruling decides what happens next.
Gate: [spec | implementation]. Task: [task id, or "plan"].

## Brief
[The plan, or the task brief with its acceptance criteria, verbatim.]

## Evidence
[Implementation: the full scoped diff and the results of the checks the
driver ran. Spec: the plan as the driver revised it.]

## Dispute
[Every round in order: each finding with its tag, and the other side's
response, whether the driver's disposition and reason or the worker's report.]

## Task
Rule on the dispute: does the [plan | diff] satisfy the brief, given the
findings still open?

## Acceptance
- The first line of your answer is exactly `verdict: approve` or
  `verdict: reject`.
- Then one reason per disputed point, citing the evidence above.
- Approve only if every open finding is wrong or does not block the brief.

## Constraints
- Read-only. Do not edit any file, write the goal file, or change product
  code.
- Rule on the open findings. Do not propose your own solution, and do not
  reopen points both sides already agreed on.

## Output
- DO NOT send optional commentary. Answer only what was asked.
- End with at most 3 lines of lessons learned.
```

Arbitration rules:

- **The ruling binds.** Record it whatever it says, and do not re-argue it.
- **Only a well-formed verdict counts.** A first line other than the two above, or reasons that contradict it, is no verdict. Resubmit once; a second failure is recorded as `no verdict`, and the user decides.
- **Not blind.** Blind arbitration (`references/claude-driven.md`) withholds each solver's answer from the other; this packet carries both sides on purpose.
~~~~

- [ ] **Step 3: `references/goal-template.md`.**
  - Replace the `## Spec Review` block inside the template with:
    ```
    ## Spec Review
    [Runs on every plan (Phase 1 in references/claude-driven.md). One status line, then one line per round.]
    status: not run
    [statuses: not run | round <n> running | awaiting arbitration | consensus | approved | rejected | failed]
    [round <n>: <jobId> — each blocking finding accepted or declined, with the reason for each decline]
    ```
  - Status legend: `status: pending | in_progress | delegated | review | rework-<n> | arbitration | rejected | taken-back | done`.
  - After `## Anomalies` add:
    ```
    ## Arbitration
    [none, or one line per escalation ruling, approve included: <gate> · <task id or plan> · <arbiter jobId or refusal message> · approve | reject | no verdict · <one-line reason> · [same-vendor]]
    ```
  - `## Notes` placeholder: "[Integration decisions, takebacks after monitoring anomalies, and the handover for any rejected row: what the reviewer found, what the arbiter ruled, what continuing would take.]"
  - `deep_reasoner` bullet: "It also carries the optional Phase 1 spec review, which is a responsibility rather than a row in this table." becomes "It also reviews every plan in Phase 1, a responsibility rather than a row in this table."
  - `arbiter` bullet: "the blind second solver for contentious or high-stakes calls, and the judge a review gate escalates to when its cap runs out; invoked by the arbiter protocols in `references/claude-driven.md`, not assigned routine rows of its own."
  - Replace the last rule (`## Spec Review` is the once-per-run marker …) with two rules: "- `## Spec Review` tracks the `deep_reasoner` review of the plan (Phase 1 in `references/claude-driven.md`). Any status other than `not run` means the automatic review is spent; a non-terminal status is finished, not restarted; only an explicit user request starts a new chain. This file is rewritten per run, so the marker resets by itself." and "- `## Arbitration` records every escalation ruling, approve included. A `rejected` row is never reopened automatically: `/agent-handoff resume` shows it first, and reopening it is a new run."

- [ ] **Step 4: `references/e2e-gauntlet.md`.** In the flow block, the `FAIL` and `BLOCKED` lines become:
  ```
  FAIL    -> original implementer fixes product (a review round on its chain) -> new combined sha -> rerun
  BLOCKED -> driver resolves the prerequisite -> fresh verifier submit on the same sha; never treated as PASS
  ```
  Append to the paragraph "A FAIL goes to the original implementer, never to the verifier: …": " A FAIL counts as a review round on the implementation row's chain and reopens that row: its `done` was provisional, it goes back to `rework-<n>`, and the verifier row goes back to `pending`. A BLOCKED rerun is a fresh `submit`, never a `resume`, so it is not a round. When the implementation cap runs out with a FAIL still open, an arbiter approval cannot replace a PASS: it means the scenario was judged wrong, so the scenario goes back to the driver's review, a new hash is recorded, and the verifier reruns."

- [ ] **Step 5: `references/memory-protocol.md`.** "- per delegated task type: quality outcome (accepted first pass / rework rounds / taken back)" becomes "- per delegated task type: quality outcome (accepted first pass / rework rounds / escalated, with the arbiter's verdict / taken back after an anomaly)".

- [ ] **Step 6: `SKILL.md`.**
  - Configuration paragraph: "arbiter (the blind second solver for contentious calls)" becomes "arbiter (the blind second solver for contentious calls, and the judge a review gate escalates to)". Replace the sentence starting "`deep_reasoner` also carries one responsibility toggle, `auto_review_spec`" through "packet in `references/handoff-template.md`." with: "`deep_reasoner` also reviews every plan before it reaches the user: read-only, findings tagged blocking or advisory. Both review gates carry a round cap in the config's `[review]` section (`spec_max_rounds`, default 1; `implementation_max_rounds`, default 3; setup: `--spec-max-rounds`, `--implementation-max-rounds`), and when a gate runs out of rounds without agreement the driver escalates to `arbiter` for a binding ruling. Full step in `references/claude-driven.md`, packets in `references/handoff-template.md`."
  - Receipt template: `anomalies: <none | job stalled | job failed | takeback | failed check | arbitration | other>`.
  - After the paragraph starting "Generate the receipt with", add: "Each escalation ruling goes into `anomalies` as `arbitration: <task> approve|reject|no verdict (<jobId>)`, approve included; all entries share the one line, joined with `; `."

- [ ] **Step 7: `CLAUDE.md`.** In the `scripts/handoff-config.py` bullet, "It owns only `hosts.claude_code.identities.*` and must preserve" becomes "It owns `hosts.claude_code.identities.*` and `[review]` and must preserve".

- [ ] **Step 8: `docs/receipt-schema.json`.** The `anomalies` description becomes: "'none' or a short description: job stalled, job failed, takeback, failed check, arbitration, other. Several entries share this one line, joined with '; '; an escalation ruling reads 'arbitration: <task> approve|reject|no verdict (<jobId>)'." No other change; the schema stays v6.

- [ ] **Step 9: Check.** `python3 -m json.tool docs/receipt-schema.json >/dev/null`; `bash scripts/check-skill-repo.sh .` → `fail=0`; `rg -n "auto_review_spec|--spec-review|Maximum two fix rounds|take the task back and finish" references SKILL.md CLAUDE.md --glob '!references/setup.md'` → no hits. (`references/setup.md` belongs to R1, which runs in parallel; the new Phase 4 text says "the default allows two fix rounds" on purpose, so that phrase is not searched for.)

---

### Task 5: Prompt cases

**Files:** `test-prompts.json`

- [ ] **Step 1: Rewrite the three spec-review entries.** Replace the entry with id `spec-review-runs-once-when-configured` by:

```json
{
  "id": "spec-review-runs-on-every-plan",
  "prompt": "Agent handoff: plan the subscription cancellation flow and split it.",
  "should_trigger": true,
  "expected_behavior": [
    "Write the plan and attack the split first, then send the Spec Review Packet to deep_reasoner before the plan reaches the user, whatever the config says; there is no toggle.",
    "Submit it with delegate-codex.sh submit --role deep_reasoner --read-only --label spec-review, with the plan verbatim in the packet.",
    "Expect each finding tagged blocking or advisory; accept or decline each one and write down why.",
    "Record the status and the round in the goal file's ## Spec Review block, then hand the adjusted plan to the user."
  ],
  "must_not": [
    "Skip the review because deep_reasoner has no auto_review_spec setting.",
    "Let deep_reasoner rewrite the plan, edit the goal file, or touch product code.",
    "Apply the findings unread as if they were a verdict rather than input to the driver's judgment.",
    "Withhold the plan from the reviewer as though this were blind arbitration."
  ]
}
```

In the entry `spec-review-not-repeated-in-the-same-run`, the prompt becomes "(The Spec Review block in .handoff/goal.md already reads status: consensus, and the plan changed after it.) Agent handoff: keep going with this plan." and its first expected_behavior becomes "Read the goal file's ## Spec Review block and treat any status other than not run as the automatic review already being spent."

Replace the entry `spec-review-on-request-while-the-toggle-is-off` by:

```json
{
  "id": "spec-review-declined-blocking-finding-escalates",
  "prompt": "(deep_reasoner's spec review returned one blocking finding you disagree with, and [review] spec_max_rounds is 1.) Agent handoff: carry on with the plan.",
  "should_trigger": true,
  "expected_behavior": [
    "Record the declined blocking finding and the reason for declining it in the ## Spec Review block.",
    "Recognise the spec cap is reached and submit an escalation ruling with delegate-codex.sh submit --role arbiter --read-only --label arbitrate-spec.",
    "Put the plan, every finding with its tag, and the driver's dispositions and reasons into the Arbitration Packet.",
    "Record the verdict in the ## Arbitration block whatever it is; on approve send the plan to the user, on reject stop before Phase 2 and still emit a receipt with phase planning."
  ],
  "must_not": [
    "Resume the spec-review job past the cap.",
    "Overrule the blocking finding alone and continue into delegation.",
    "Withhold the driver's reasons from the arbiter as if the ruling were blind.",
    "Escalate a declined advisory finding."
  ]
}
```

- [ ] **Step 2: Add three entries** (anywhere in the array; ids must stay unique):

```json
{
  "id": "implementation-cap-escalates-to-arbiter",
  "prompt": "(T3's third review pass still has findings open, and [review] implementation_max_rounds is 3.) Agent handoff: continue.",
  "should_trigger": true,
  "expected_behavior": [
    "Do not resume T3 again: resume refuses a fourth round.",
    "Submit an escalation ruling with delegate-codex.sh submit --role arbiter --read-only --label arbitrate-T3, carrying the brief, the acceptance criteria, the full scoped diff with check results, and every round's findings with the worker's last report.",
    "On approve, accept the diff, mark T3 done, and record which findings were overruled.",
    "On reject, mark T3 rejected, set its diff aside, hold rows that depend on it, keep independent rows going, write a handover into Notes, and add arbitration: T3 reject (<jobId>) to the receipt's anomalies line."
  ],
  "must_not": [
    "Take T3 back into the driving session instead of escalating.",
    "Start a fresh submit to reset the round count.",
    "Raise the cap in config to avoid the ruling.",
    "Leave an approval out of the ## Arbitration block."
  ]
},
{
  "id": "arbiter-failure-is-not-approval",
  "prompt": "(The arbiter job for T3 exited without a verdict line.) Agent handoff: continue.",
  "should_trigger": true,
  "expected_behavior": [
    "Record the anomaly and resubmit one fresh arbiter job with the same Arbitration Packet.",
    "If the second job also produces no well-formed verdict, record no verdict in the ## Arbitration block, leave T3 in arbitration, and wrap up for the user to decide."
  ],
  "must_not": [
    "Read the missing verdict as an approval.",
    "Take the dispute back into the driving session.",
    "Resubmit the arbiter job more than once."
  ]
},
{
  "id": "rejected-row-waits-for-the-user",
  "prompt": "/agent-handoff resume (.handoff/goal.md has T3 marked rejected by the arbiter.)",
  "should_trigger": true,
  "expected_behavior": [
    "Show T3 first, with its ## Arbitration line, the handover in Notes, and the saved patch or kept worktree.",
    "Ask the user which route to take: raise the cap and resume the chain with the work restored, rewrite the brief as a new chain, or have the driver take the task over.",
    "Treat reopening T3 as a new run that stamps a new session start."
  ],
  "must_not": [
    "Resume T3's chain without the user's decision.",
    "Delete the saved patch or clean up the kept worktree.",
    "Re-argue the arbiter's ruling."
  ]
}
```

- [ ] **Step 3: Update the fix-round strings** (exact replacements, whichever entry holds them):
  - "Full-review every Codex diff before accepting it, with at most two bounded fix rounds via delegate-codex.sh resume." becomes "Full-review every Codex diff before accepting it, within the review cap ([review] implementation_max_rounds, default 3 review passes) via delegate-codex.sh resume."
  - The string starting "Send findings back as a bounded fix round to the same Codex session when the work falls short, and take the task back into Claude after two failed" becomes "Send findings back as a bounded fix round to the same Codex session when the work falls short, and escalate to the arbiter when the review cap runs out with findings still open."
  - "Exceed two fix rounds without taking the task back." becomes "Resume past the review cap, or take the task back instead of escalating to the arbiter."
  - "Cap the task at two fix rounds, then take it back into the driving session and record the takeback." becomes "Stop at the review cap, then escalate to the arbiter and record its verdict."
  - "Keep the bound at two fix rounds per task, then take the task back into the driving session." becomes "Keep within the review cap per task, then escalate to the arbiter rather than taking the task back."
  - "Exceed two fix rounds without recording a takeback." becomes "Resume past the review cap, or settle the dispute without an arbiter ruling."

- [ ] **Step 4: Check.** `python3 -m json.tool test-prompts.json >/dev/null`; `python3 scripts/run-test-prompts.py` → PASS; `rg -n "auto_review_spec|two fix rounds|two failed" test-prompts.json` → only the must_not line "Skip the review because deep_reasoner has no auto_review_spec setting."

- [ ] **Step 5 (driver, after review of R3): commit** `docs: flow contract for the review gates and the escalation ruling`.

---

### Task 6: User-facing docs and version 3.8.0

**Files:** `README.md`, `docs/user-guide/agent-handoff.html`, `docs/user-guide/diagrams/{phase1-plan-split,flow-overview,feedback-loop,phase4-review-gate,handoff-packet-anatomy}.svg`, `CHANGELOG.md`, `docs/releases/v3.8.0.md` (create), `SKILL.md` (frontmatter only)

- [ ] **Step 1: Version.** `SKILL.md` frontmatter `version: 3.8.0`. README line 8 badge: `Version: 3.8.0` and `version-3.8.0-ef6f4f`. Leave the "(v3.7.2)" heading of the session-page section alone; it dates that feature.

- [ ] **Step 2: README.**
  - "A diff that fails goes back to the same worker session as a fix round, at most two, and after that the driver finishes the task itself." becomes "A diff that fails goes back to the same worker session as a fix round, within the review cap (three passes by default); if findings are still open after the last pass, the arbiter rules on the dispute."
  - "A task gets two fix rounds at most, then comes back to Claude." becomes "A task gets three review passes by default (`implementation_max_rounds`); if findings are still open after the last one, the arbiter rules, and a rejection stops that task for you to pick up."
  - "- Plan review (`--spec-review`, off by default): `deep_reasoner` reads the plan once, read-only, before it reaches you." becomes "- Plan review (every run): `deep_reasoner` reads the plan, read-only, before it reaches you. A blocking finding the driver declines goes to the arbiter once `spec_max_rounds` (default 1) runs out."
  - Identity table: "| `arbiter` | blind second solve for contested calls | core |" becomes "| `arbiter` | blind second solve for contested calls; rules when a review gate runs out of rounds | core |".

- [ ] **Step 3: `docs/user-guide/agent-handoff.html`.**
  - `deep_reasoner` card: "Also the reviewer of the plan itself, when that toggle is on." becomes "Also the reviewer of every plan before it reaches the user."
  - In the `<p>` starting "Everything above is the driver checking its own work.", "The optional spec review is what closes it: before the plan reaches the user, <code>deep_reasoner</code> reads it once on its own configured backend and reports what it would change." becomes "The spec review is what closes it: before any plan reaches the user, <code>deep_reasoner</code> reads it on its own configured backend and reports what it would change, each finding tagged blocking or advisory."
  - Replace the whole `<p>` that starts "Three properties keep an optional extra job from turning into an argument that never ends." with `<p>Three properties keep a mandatory extra job from turning into an argument that never ends. It is <strong>capped</strong>: <code>spec_max_rounds</code> (default 1) bounds how many times the same reviewer reads the plan, and a blocking finding the driver still declines at the cap goes to the arbiter, whose ruling binds. It runs <strong>once per run</strong>: the goal file's <code>## Spec Review</code> block carries a status, any status other than <code>not run</code> means the automatic review is spent, and a resumed session finishes an open chain instead of starting a new one. And it is <strong>read-only</strong>: <code>--read-only</code> on the job, findings as the whole output, no edit to the spec, the goal file, or product code.</p>`
  - Replace the whole `<p>` that starts "One identity carries a sixth field." with `<p>No identity carries a spec-review toggle any more. Until 3.8.0 <code>deep_reasoner.auto_review_spec</code> decided whether the plan got a second read; now every plan does, and the engine drops the old key when it reads a config. The round caps for both review gates live in their own <code>[review]</code> section: <code>spec_max_rounds</code> (default 1) and <code>implementation_max_rounds</code> (default 3).</p>`
  - In the `<p>` starting "A fix round takes the same path at a smaller size.", `<code>&lt;parent&gt;-r2</code>` becomes `<code>&lt;root&gt;-r&lt;n&gt;</code>`, and before its closing `</p>` add " It refuses a round past the review cap before creating anything."
  - Stage 4 figcaption: "Rework is a resume carrying findings only, it is capped at two rounds, and the cap has a named consequence rather than a retry." becomes "Rework is a resume carrying findings only, capped at <code>implementation_max_rounds</code> review passes (default 3), and the cap has a named consequence: the arbiter rules."
  - Replace the `<li>` starting "<strong>Two rounds fail.</strong>" with `<li><strong>The last review pass still has findings open.</strong> The argument goes to the arbiter with both sides of it. An approval accepts the diff over the driver's findings; a rejection is final for the run: the row is marked <code>rejected</code>, its diff is set aside, and the ruling goes into the goal file's <code>## Arbitration</code> block, the notes, and the receipt, which is what lets the memory protocol learn that this task type does not delegate well.</li>`
  - Table cell "Two fix rounds maximum, each carrying only prioritized findings and the failed criteria; then the driver takes the task back" becomes "Review passes capped by <code>implementation_max_rounds</code> (default 3), each fix round carrying only prioritized findings and the failed criteria; then the arbiter rules".
  - `rg -n "toggle is on|optional spec review|optional extra job|two rounds|two fix rounds|at most two" docs/user-guide/agent-handoff.html` → no hits. (`auto_review_spec` stays once, in the retirement paragraph above.)

- [ ] **Step 4: SVG labels** (text content only; keep every element, attribute, and coordinate):
  - `phase1-plan-split.svg`: "Spec review — optional, and only here" → "Spec review — every plan, capped"; "auto_review_spec on, or the user asks for a second reader" → "every plan; a declined blocking finding goes to the arbiter at the cap".
  - `flow-overview.svg`: "optional: deep_reasoner reads it once" → "deep_reasoner reviews every plan"; "max 2 rounds, then take the task back" → "3 review passes by default, then the arbiter"; "takeback noted in goal.md and receipt" → "ruling noted in goal.md and receipt".
  - `feedback-loop.svg`: subtitle "Rework is bounded; the end of the road is a recorded takeback." → "Rework is bounded; the end of the road is a recorded arbiter ruling."; "rework ≤ 2 rounds — resume the same session, send findings + failed criteria only" → "rework within the cap (3 passes) — resume the same session, send findings + failed criteria only"; "max two fix rounds" → "cap: 3 review passes"; "Notes carry takebacks" → "Notes carry rulings"; in the "still failing" row: "after two rounds" → "at the cap"; "the argument ends: the driver takes the task back into" → "the arbiter rules on the dispute, with both sides"; "its own session rather than sending a third round" → "in its packet; a rejection is final for the run"; "driver; status taken-back in goal.md," → "driver; status rejected in goal.md,"; "the takeback repeated in the receipt" → "the ruling repeated in the receipt". The anomaly line "anomaly — cancel, read stderr.log, resubmit or take back" stays.
  - `handoff-packet-anatomy.svg`: "Claude &#8594; deep_reasoner &#183; planning, read-only, optional" → "Claude &#8594; deep_reasoner &#183; planning, read-only, every plan".
  - `phase4-review-gate.svg`: "two rounds maximum per task" → "cap: implementation_max_rounds (3)"; "Two rounds failed → take the task back" → "Cap reached → the arbiter rules"; "finish it in the driver; write the takeback into goal.md's" → "approve accepts; reject is final; record it in goal.md's".
  - Check: `python3 -c "import sys, xml.etree.ElementTree as E; [E.parse(f) for f in sys.argv[1:]]" docs/user-guide/diagrams/*.svg` exits 0; `rg -n "two rounds|two fix rounds|max 2 rounds|auto_review_spec|taken-back" docs/user-guide/diagrams` → no hits.

- [ ] **Step 5: `CHANGELOG.md`.** Add at the top, above `## v3.7.2`:

```markdown
## v3.8.0 (2026-09-10)

### Two review gates, one shape

- **breaking**: spec review runs on every plan. `deep_reasoner.auto_review_spec` is retired: the config reader drops it, so an old config still loads and the next write removes it. `--spec-review`/`--no-spec-review` are gone from `handoff-config.py set` and `handoff-setup.py`, and `--override deep_reasoner.auto_review_spec=…` is refused. Off by default meant most plans were read only by their author.
- feat: a `[review]` config section with two round caps, `spec_max_rounds` (default 1) and `implementation_max_rounds` (default 3). A round is a review pass in both gates: the original job is pass one and each `resume` adds one. Set them with `handoff-config.py set-review`, `handoff-setup.py --spec-max-rounds`/`--implementation-max-rounds`, or the wizard's Review gates fields; `resolve` and `--status` print them. `schema_version` stays 2.
- feat: `delegate-codex.sh resume` enforces the cap. It walks `parent=` back to the chain's first job, applies the spec cap to a `spec-review` chain and the implementation cap to any other, and refuses a round past the cap before creating a job directory. The round count no longer trusts the `-r<n>` suffix, which a fresh label can carry too.
- feat: at the cap without consensus, the driver sends the whole dispute to `arbiter` in the new Arbitration Packet, and the ruling binds. An approval can overrule the driver's findings but never replaces an e2e PASS. A rejection is final for the run: the row is `rejected`, its diff is set aside, and reopening it is a new run. A failed arbiter job gets one resubmit and is never read as approval. The takeback after the last fix round is gone; takeback stays for monitoring anomalies.
- feat: spec review findings are tagged blocking or advisory, and only a declined blocking finding escalates. The goal file's `## Spec Review` block carries explicit states, so a resumed session finishes an open chain instead of reading it as done.
- Every ruling, approve included, is recorded in the goal file's `## Arbitration` block and in the receipt's `anomalies` line. Receipt schema stays 6.
```

- [ ] **Step 6: Create `docs/releases/v3.8.0.md`:**

~~~~markdown
# v3.8.0: Review gates

Both review gates now have the same shape: a maker, a reviewer, a round cap, and the arbiter for an argument that does not settle.

**Spec review runs on every plan.** `deep_reasoner` reads the plan, read-only, before it reaches you and tags each finding blocking or advisory. The driver accepts or declines each one with a reason. The `auto_review_spec` toggle is retired: the config reader drops it, and `--spec-review` is gone from both CLIs.

**Two caps, one unit.** A new `[review]` config section holds `spec_max_rounds` (default 1) and `implementation_max_rounds` (default 3). A round is a review pass in both gates: the original job is pass one and each `resume` adds one.

```bash
python3 scripts/handoff-config.py --scope project set-review --spec-max-rounds 1 --implementation-max-rounds 3
```

Setup takes `--spec-max-rounds` and `--implementation-max-rounds`, and the wizard shows both as Review gates fields.

**`resume` enforces the cap.** It walks `parent=` back to the chain's first job, applies the spec cap to a `spec-review` chain and the implementation cap to any other, and refuses a round past the cap before creating a job directory.

**At the cap, the arbiter rules.** The driver sends the whole dispute to `arbiter` in the new Arbitration Packet, and the ruling binds. An approval can overrule the driver's findings but never replaces an e2e PASS. A rejection is final for the run: the row is marked `rejected`, its diff is set aside, and reopening it is a new run. A failed arbiter job gets one resubmit and is never read as approval.

**Every ruling is recorded**, approve included, in the goal file's `## Arbitration` block and on the receipt's `anomalies` line. Receipt schema stays 6.

Limits: `resume` is the only script that counts rounds, so a fresh `submit` starts a chain it never sees, and escalating instead of quietly finishing a task remains a rule in the flow prose.
~~~~

- [ ] **Step 7: Check.** `bash scripts/check-skill-repo.sh .` → `fail=0`; `rg -n "3\.7\.2" SKILL.md README.md | head` shows only the session-page section heading and its release link.
- [ ] **Step 8: Behaviour audit beyond the listed phrases.** `rg -n -i "optional|toggle|take.{0,20}back|takeback|two rounds|once per run|second pair of eyes" README.md docs/user-guide` and read every hit. Fix any that still describe spec review as optional or the post-cap takeback, in the same voice. Leave the optional e2e pair and the monitoring-anomaly takeback alone; both are still true.

- [ ] **Step 9 (driver, after review): commit** `feat: Agent Handoff 3.8.0 -- review gates`.

---

### Task 7 (driver): integrate and verify

- [ ] Review each row's diff against its task and the design sections named in *Spec*, before its commit. Read the whole diff, never a sample.
- [ ] Run the full CI set locally:
  ```bash
  bash scripts/check-skill-repo.sh .
  bash -n install.sh && bash -n scripts/check-skill-repo.sh && bash -n scripts/delegate-codex.sh
  python3 -m unittest discover -s tests
  node --test tests/test_transcript_viewer.mjs
  python3 scripts/run-test-prompts.py
  python3 scripts/make-receipt.py --start --repo . && python3 scripts/make-receipt.py --repo . --phase review --claude-session ci-test --checks "ci" --codex-jobs 0 --cc-jobs 0 --copilot-jobs 0 --scope project --config-source project --roles-used '[]' | python3 scripts/validate-receipt.py -
  SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown && git diff --exit-code -- examples/showcase-cost-ledger.json
  bash install.sh --dry-run
  ```
  Note the receipt roundtrip restamps `.handoff/session-start`; run it after this run's own receipt is saved, or pass `--started-at` to this run's receipt.
- [ ] Manual check: `python3 scripts/handoff-setup-ui.py --repo <scratch repo>` opens; the Review gates fields show 1 and 3; preview shows a `[review]` section in the diff.
- [ ] De-slop pass (the `declawed` skill) on the prose that ships: `CHANGELOG.md` entry, `docs/releases/v3.8.0.md`, the README and user-guide edits, and the new text in `references/` and `SKILL.md`.
- [ ] Final check that nothing outside history still describes the old behaviour: `rg -n "auto_review_spec|--spec-review|Maximum two fix rounds|take the task back and finish|toggle is on|at most two, and after that" --glob '!docs/specs/**' --glob '!CHANGELOG.md' --glob '!docs/releases/**' --glob '!docs/research/**' --glob '!examples/**' .` → only the retired-field code, the tests that assert its removal, the one `must_not` prompt line, and the "retired in 3.8.0" notes in `docs/specs/design_agent-identities-and-config.md` and the user guide.
