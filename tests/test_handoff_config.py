from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "handoff-config.py"
SPEC = importlib.util.spec_from_file_location("handoff_config", SCRIPT)
handoff_config = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = handoff_config
SPEC.loader.exec_module(handoff_config)


def document(claude_model: str = "opus", codex_model: str = "gpt-test") -> str:
    return (
        "# keep this top comment\r\n"
        "schema_version = 2\r\n"
        "revision = 7\r\n"
        "\r\n"
        "[hosts.claude_code.identities.deep_reasoner]\r\n"
        'backend = "claude"\r\n'
        f'model = "{claude_model}" # claude comment\r\n'
        'effort = "high"\r\n'
        "\r\n"
        "[hosts.claude_code.identities.fast_worker]\r\n"
        'backend = "codex"\r\n'
        'model = "sonnet"\r\n'
        'effort = "medium"\r\n'
        "\r\n"
        "# codex prefix comment must survive\r\n"
        "[hosts.codex.identities.deep_reasoner]\r\n"
        'backend = "codex"\r\n'
        f'model = "{codex_model}" # untouched inline\r\n'
        'effort = "xhigh"\r\n'
        "\r\n"
        "[hosts.codex.identities.fast_worker]\r\n"
        'backend = "claude"\r\n'
        'model = "gpt-fast"\r\n'
        'effort = "medium"\r\n'
        "\r\n"
        "[routing]\r\n"
        "always_on_host_rules = false # routing comment\r\n"
        "\r\n"
        "[future]\r\n"
        'opaque = "preserve me" # exact\r\n'
    )


def bare_document(claude_model: str = "opus", codex_model: str = "gpt-test") -> str:
    """The twin of document() with bare identity headers; everything else is identical."""

    return document(claude_model, codex_model).replace("[hosts.claude_code.identities.", "[")


def legacy_document() -> str:
    return (
        "schema_version = 1\n"
        "revision = 4\n"
        "\n"
        "[hosts.codex.roles.deep_reasoner]\n"
        'model = "gpt-legacy"\n'
        'effort = "xhigh"\n'
        "verified = true\n"
        "\n"
        "[hosts.codex.roles.fast_worker]\n"
        'model = "gpt-legacy-fast"\n'
        'effort = "medium"\n'
    )


class ConfigRoundTripTests(unittest.TestCase):
    def test_unowned_host_routing_and_comments_are_byte_preserved(self):
        for form, original in (("legacy", document()), ("bare", bare_document())):
            with self.subTest(form=form):
                before_chunks = {
                    chunk.name: chunk.text
                    for chunk in handoff_config.split_sections(original)
                    if chunk.name and handoff_config._identity_name(chunk.name) is None
                }
                identities = {
                    "deep_reasoner": {"backend": "claude", "model": "new-opus", "effort": "high"},
                    "fast_worker": {"backend": "codex", "model": "new-sonnet", "effort": "low"},
                }
                updated = handoff_config.update_host(original, identities=identities)
                after_chunks = {
                    chunk.name: chunk.text
                    for chunk in handoff_config.split_sections(updated)
                    if chunk.name and handoff_config._identity_name(chunk.name) is None
                }
                self.assertEqual(before_chunks, after_chunks)
                self.assertIn("# keep this top comment\r\n", updated)

    def test_stale_second_host_sections_survive_a_write(self):
        # Configs written by dual-host versions still carry hosts.codex.* blocks.
        # They are unowned now, so a write must leave them byte-identical.
        for form, original in (("legacy", document()), ("bare", bare_document())):
            with self.subTest(form=form):
                stale = "".join(
                    chunk.text for chunk in handoff_config.split_sections(original)
                    if chunk.name and chunk.name.startswith("hosts.codex.")
                )
                self.assertIn("gpt-test", stale)
                parsed = handoff_config.validate_config(original)
                identities = handoff_config.identities_of(parsed)
                identities["fast_worker"]["effort"] = "low"
                updated = handoff_config.update_host(original, identities=identities)
                updated_stale = "".join(
                    chunk.text for chunk in handoff_config.split_sections(updated)
                    if chunk.name and chunk.name.startswith("hosts.codex.")
                )
                self.assertEqual(stale, updated_stale)
                self.assertIn('effort = "low"', updated)

    def test_unknown_array_of_tables_section_is_preserved(self):
        original = document() + "\n[[future.plugins]]\nname = \"x\"\n"
        identities = {"deep_reasoner": {"backend": "claude", "model": "new", "effort": "high"}}
        updated = handoff_config.update_host(original, identities=identities)
        self.assertIn("[[future.plugins]]\nname = \"x\"\n", updated)

    def test_emitter_is_idempotent(self):
        parsed = handoff_config.validate_config(document())
        identities = handoff_config.identities_of(parsed)
        once = handoff_config.update_host(document(), identities=identities)
        twice = handoff_config.update_host(once, identities=identities)
        self.assertEqual(once, twice)
        self.assertLess(once.index("backend ="), once.index("model ="))
        self.assertLess(once.index("model ="), once.index("effort ="))


class UnsupportedSyntaxTests(unittest.TestCase):
    def assert_parse_error(self, assignment: str):
        text = (
            "schema_version = 2\n"
            "revision = 0\n"
            "[deep_reasoner]\n"
            'backend = "codex"\n'
            f"{assignment}\n"
            'effort = "high"\n'
        )
        with self.assertRaises(handoff_config.ConfigParseError) as raised:
            handoff_config.validate_config(text)
        message = str(raised.exception)
        self.assertRegex(message, r"line \d+, column \d+")
        self.assertIn("This parser supports bare keys", message)

    def test_inline_table_fails_closed(self):
        self.assert_parse_error("model = { name = \"x\" }")

    def test_multiline_string_fails_closed(self):
        self.assert_parse_error('model = """x"""')

    def test_datetime_fails_closed(self):
        self.assert_parse_error("model = 2026-07-19T10:00:00Z")

    def test_array_of_tables_fails_closed(self):
        text = "schema_version = 2\n[[hosts.claude_code.identities]]\nmodel = \"x\"\n"
        with self.assertRaises(handoff_config.ConfigParseError) as raised:
            handoff_config.validate_config(text)
        self.assertIn("line 2, column 1", str(raised.exception))

    def test_dotted_key_assignment_fails_closed(self):
        self.assert_parse_error('settings.model = "x"')


class BackendValidationTests(unittest.TestCase):
    def identity_document(self, backend: str = "") -> str:
        return (
            "schema_version = 2\n"
            "revision = 0\n"
            "[deep_reasoner]\n"
            f"{backend}"
            'model = "gpt-test"\n'
            'effort = "high"\n'
        )

    def test_missing_backend_fails_validation(self):
        with self.assertRaises(handoff_config.ConfigValidationError) as raised:
            handoff_config.validate_config(self.identity_document())
        self.assertIn("backend must be one of claude, codex", str(raised.exception))

    def test_invalid_backend_fails_validation(self):
        with self.assertRaises(handoff_config.ConfigValidationError) as raised:
            handoff_config.validate_config(
                self.identity_document('backend = "local"\n')
            )
        self.assertIn("backend must be one of claude, codex", str(raised.exception))

    def test_every_supported_backend_validates(self):
        for backend in ("claude", "codex", "copilot", "cursor"):
            with self.subTest(backend=backend):
                handoff_config.validate_config(
                    self.identity_document(f'backend = "{backend}"\n')
                )

    def test_the_error_names_every_supported_backend(self):
        """A pre-3.7 engine refuses a copilot config rather than mis-running it."""

        with self.assertRaises(handoff_config.ConfigValidationError) as raised:
            handoff_config.validate_config(self.identity_document('backend = "gemini"\n'))
        self.assertIn("backend must be one of claude, codex, copilot, cursor", str(raised.exception))


class LegacyMigrationTests(unittest.TestCase):
    def run_cli(self, *arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = handoff_config.main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def assert_upgrade_error(self, message: str, path: Path):
        self.assertIn(handoff_config.V1_UPGRADE_MESSAGE, message)
        self.assertIn(str(path), message)

    def test_resolve_v1_file_reports_upgrade_guide_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            path = repo / ".handoff" / "config.toml"
            path.parent.mkdir(parents=True)
            path.write_text(legacy_document(), encoding="utf-8")
            env = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg")}
            with self.assertRaises(handoff_config.ConfigValidationError) as raised:
                handoff_config.resolve_config(repo, env=env)
            self.assert_upgrade_error(str(raised.exception), path)

    def test_validate_v1_file_reports_upgrade_guide_and_path(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".handoff" / "config.toml"
            path.parent.mkdir(parents=True)
            path.write_text(legacy_document(), encoding="utf-8")
            status, _, error = self.run_cli(
                "--repo", directory, "validate"
            )
            self.assertEqual(2, status)
            self.assert_upgrade_error(error, path)

    def test_set_v1_file_reports_upgrade_guide_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".handoff" / "config.toml"
            path.parent.mkdir(parents=True)
            path.write_text(legacy_document(), encoding="utf-8")
            before = path.read_bytes()
            status, _, error = self.run_cli(
                "--repo", directory, "set",
                "--role", "deep_reasoner", "--backend", "codex",
                "--model", "new-model", "--effort", "high",
            )
            self.assertEqual(2, status)
            self.assert_upgrade_error(error, path)
            self.assertEqual(before, path.read_bytes())

    def test_schema_v2_with_legacy_role_section_fails_closed(self):
        text = legacy_document().replace("schema_version = 1", "schema_version = 2")
        path = Path("/tmp/schema-v2-with-roles.toml")
        with self.assertRaises(handoff_config.ConfigValidationError) as raised:
            handoff_config.validate_config(text, path=path)
        self.assert_upgrade_error(str(raised.exception), path)


class ResolveTests(unittest.TestCase):
    def test_priority_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            xdg = root / "xdg"
            global_path = xdg / "handoff" / "config.toml"
            project_path = repo / ".handoff" / "config.toml"
            global_path.parent.mkdir(parents=True)
            project_path.parent.mkdir(parents=True)
            global_path.write_text(
                handoff_config.update_host("", identities={
                    "deep_reasoner": {"backend": "codex", "model": "global", "effort": "high"},
                    "fast_worker": {"backend": "claude", "model": "global-fast", "effort": "low"},
                }), encoding="utf-8"
            )
            project_path.write_text(
                handoff_config.update_host("", identities={
                    "deep_reasoner": {"backend": "codex", "model": "project", "effort": "xhigh"},
                    "fast_worker": {"backend": "claude", "model": "project-fast", "effort": "medium"},
                }), encoding="utf-8"
            )
            env = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(xdg)}
            resolved = handoff_config.resolve_config(repo, env=env)
            self.assertEqual("project", resolved["source"])
            self.assertEqual("project", resolved["deep_reasoner"]["model"])
            self.assertEqual("codex", resolved["deep_reasoner"]["backend"])
            override = {"deep_reasoner": {"model": "session"}}
            resolved = handoff_config.resolve_config(repo, session_override=override, env=env)
            self.assertEqual("session", resolved["source"])
            self.assertEqual("session", resolved["deep_reasoner"]["model"])
            project_path.unlink()
            resolved = handoff_config.resolve_config(repo, env=env)
            self.assertEqual("global", resolved["source"])
            global_path.unlink()
            resolved = handoff_config.resolve_config(repo, env=env)
            self.assertEqual("default", resolved["source"])
            self.assertEqual({}, handoff_config.identities_of(resolved))

    def test_higher_layer_identity_change_invalidates_inherited_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            xdg = root / "xdg"
            global_path = xdg / "handoff" / "config.toml"
            project_path = repo / ".handoff" / "config.toml"
            global_path.parent.mkdir(parents=True)
            project_path.parent.mkdir(parents=True)
            global_path.write_text(
                handoff_config.update_host(
                    "",
                    identities={
                        "deep_reasoner": {
                            "backend": "claude",
                            "model": "verified-old",
                            "effort": "high",
                            "verified": True,
                            "verified_at": "2026-07-29T00:00:00Z",
                        }
                    },
                ),
                encoding="utf-8",
            )
            project_path.write_text(
                handoff_config.update_host(
                    "",
                    identities={
                        "deep_reasoner": {
                            "backend": "claude",
                            "model": "unverified-new",
                            "effort": "xhigh",
                        }
                    },
                ),
                encoding="utf-8",
            )
            env = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(xdg)}
            resolved = handoff_config.resolve_config(repo, env=env)
            identity = resolved["deep_reasoner"]
            self.assertEqual("unverified-new", identity["model"])
            self.assertFalse(identity["verified"])
            self.assertNotIn("verified_at", identity)

            override = {"deep_reasoner": {"model": "session-model"}}
            resolved = handoff_config.resolve_config(
                repo, session_override=override, env=env
            )
            identity = resolved["deep_reasoner"]
            self.assertEqual("session-model", identity["model"])
            self.assertFalse(identity["verified"])


class AtomicWriteTests(unittest.TestCase):
    def test_atomic_write_leaves_no_tempfile(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "config.toml"
            handoff_config.atomic_write(path, "one\n")
            handoff_config.atomic_write(path, "two\n")
            self.assertEqual("two\n", path.read_text(encoding="utf-8"))
            self.assertEqual(["config.toml"], sorted(item.name for item in path.parent.iterdir()))


class CliTests(unittest.TestCase):
    def run_cli(self, *arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = handoff_config.main(list(arguments))
        return status, stdout.getvalue(), stderr.getvalue()

    def test_resolve_prints_one_line_per_dotted_key(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ("--repo", directory)
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            self.assertEqual(0, self.run_cli(
                *base, "set-review", "--spec-max-rounds", "4", "--implementation-max-rounds", "5")[0])
            status, output, _ = self.run_cli(
                *base, "resolve", "review.spec_max_rounds", "review.implementation_max_rounds")
            self.assertEqual((0, "4\n5\n"), (status, output))
            status, output, error = self.run_cli(*base, "resolve", "review.spec_max_rounds", "review.missing")
            self.assertEqual((2, ""), (status, output))
            self.assertIn("key not found: review.missing", error)

    def test_init_set_get_validate_and_idempotent_init(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ("--repo", directory)
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            status, _, error = self.run_cli(
                *base, "set", "--role", "deep_reasoner",
                "--backend", "codex", "--model", "chosen-model", "--effort", "high",
            )
            self.assertEqual((0, ""), (status, error))
            self.assertEqual(0, self.run_cli(*base, "validate")[0])
            status, output, error = self.run_cli(
                *base, "get", "deep_reasoner.model"
            )
            self.assertEqual((0, "chosen-model\n", ""), (status, output, error))
            status, _, error = self.run_cli(
                *base, "set", "--role", "deep_reasoner", "--effort", "xhigh"
            )
            self.assertEqual((0, ""), (status, error))
            status, output, error = self.run_cli(
                *base, "get", "deep_reasoner.backend"
            )
            self.assertEqual((0, "codex\n", ""), (status, output, error))
            path = Path(directory) / ".handoff" / "config.toml"
            self.assertIn("schema_version = 2", path.read_text(encoding="utf-8"))
            before = path.read_bytes()
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            self.assertEqual(before, path.read_bytes())

    def test_invalid_new_role_does_not_write(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ("--repo", directory)
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            path = Path(directory) / ".handoff" / "config.toml"
            before = path.read_bytes()
            status, _, error = self.run_cli(
                *base, "set", "--role", "fast_worker", "--backend", "claude",
                "--model", "model-only"
            )
            self.assertEqual(2, status)
            self.assertIn("effort must be a non-empty string", error)
            self.assertEqual(before, path.read_bytes())

    def test_new_identity_requires_backend_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ("--repo", directory)
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            path = Path(directory) / ".handoff" / "config.toml"
            before = path.read_bytes()
            status, _, error = self.run_cli(
                *base, "set", "--role", "fast_worker",
                "--model", "gpt-fast", "--effort", "medium",
            )
            self.assertEqual(2, status)
            self.assertIn("backend must be one of claude, codex", error)
            self.assertEqual(before, path.read_bytes())

    def test_arbiter_full_read_write_chain(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ("--repo", directory)
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            status, _, error = self.run_cli(
                *base, "set", "--role", "arbiter", "--backend", "codex",
                "--model", "gpt-arbiter", "--effort", "xhigh", "--verified",
            )
            self.assertEqual((0, ""), (status, error))
            status, output, error = self.run_cli(
                *base, "get", "arbiter"
            )
            self.assertEqual((0, ""), (status, error))
            self.assertEqual(
                {"backend": "codex", "effort": "xhigh", "model": "gpt-arbiter", "verified": True},
                json.loads(output),
            )

    def test_identity_change_invalidates_existing_verification(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ("--repo", directory)
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            self.assertEqual(
                0,
                self.run_cli(
                    *base,
                    "set",
                    "--role",
                    "deep_reasoner",
                    "--backend",
                    "claude",
                    "--model",
                    "verified-old",
                    "--effort",
                    "high",
                    "--verified",
                    "--verified-at",
                    "2026-07-29T00:00:00Z",
                )[0],
            )
            self.assertEqual(
                0,
                self.run_cli(
                    *base,
                    "set",
                    "--role",
                    "deep_reasoner",
                    "--model",
                    "unverified-new",
                )[0],
            )
            status, output, error = self.run_cli(
                *base, "get", "deep_reasoner"
            )
            self.assertEqual((0, ""), (status, error))
            identity = json.loads(output)
            self.assertEqual("unverified-new", identity["model"])
            self.assertFalse(identity["verified"])
            self.assertNotIn("verified_at", identity)


    def test_a_cursor_identity_is_written_and_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ("--repo", directory)
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            status, _, error = self.run_cli(
                *base, "set", "--role", "fast_worker", "--backend", "cursor",
                "--model", "claude-opus-5-5-high", "--effort", "model",
            )
            self.assertEqual((0, ""), (status, error))
            self.assertEqual(0, self.run_cli(*base, "validate")[0])

class LockTests(unittest.TestCase):
    def test_held_lock_refuses_and_stale_lock_is_reclaimed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "config.toml"
            with handoff_config.ConfigLock(config):
                self.assertTrue((root / ".config.lock").is_dir())
                with self.assertRaises(handoff_config.ConfigLockError) as raised:
                    handoff_config.ConfigLock(config).acquire()
                self.assertIn(str(root / ".config.lock"), str(raised.exception))
            self.assertFalse((root / ".config.lock").exists())

            (root / ".config.lock").mkdir()
            os.utime(root / ".config.lock", (0, 0))
            with handoff_config.ConfigLock(config, stale_after=15.0):
                self.assertTrue((root / ".config.lock").is_dir())
            self.assertFalse((root / ".config.lock").exists())



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

    def test_three_identity_config_is_written_unchanged_by_the_widening(self):
        # Appending identities must not reorder what an existing three-identity
        # config emits, nor add sections for identities it does not configure.
        text = handoff_config.emit_host_sections(
            {
                "arbiter": {"backend": "codex", "model": "m", "effort": "xhigh"},
                "deep_reasoner": {"backend": "claude", "model": "opus", "effort": "high"},
                "fast_worker": {"backend": "codex", "model": "m", "effort": "high"},
            }
        )
        self.assertEqual(
            [
                "[deep_reasoner]",
                "[fast_worker]",
                "[arbiter]",
            ],
            [line for line in text.splitlines() if line.startswith("[")],
        )

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
        self.assertIn("[e2e_verifier]", text)
        parsed = handoff_config.parse_config(text)
        identity = handoff_config.identities_of(parsed)["e2e_verifier"]
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
            text.index("[deep_reasoner]"),
            text.index("[e2e_specifier]"),
            text.index("[e2e_verifier]"),
        ]
        self.assertEqual(sorted(positions), positions)

    def test_override_accepts_optional_identity(self):
        override = handoff_config._parse_override(["e2e_verifier.effort=low"])
        self.assertEqual({"e2e_verifier": {"effort": "low"}}, override)


class RetiredSpecReviewFieldTests(unittest.TestCase):
    LEGACY = (
        "schema_version = 2\n"
        "revision = 0\n"
        "[deep_reasoner]\n"
        'backend = "claude"\n'
        'model = "opus"\n'
        'effort = "high"\n'
        "auto_review_spec = true\n"
    )

    def test_dropped_on_read(self):
        parsed = handoff_config.validate_config(self.LEGACY)
        identity = parsed["deep_reasoner"]
        self.assertNotIn("auto_review_spec", identity)

    def test_dropped_even_where_it_used_to_be_invalid(self):
        text = self.LEGACY.replace("deep_reasoner", "fast_worker").replace(
            "auto_review_spec = true", 'auto_review_spec = "yes"'
        )
        parsed = handoff_config.validate_config(text)
        identity = parsed["fast_worker"]
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
        "[fast_worker]\n"
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


class PermissionModeTests(unittest.TestCase):
    def test_values_order_merge_cli_and_verification(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "repo"
            project = repo / ".handoff" / "config.toml"
            global_path = root / "xdg" / "handoff" / "config.toml"
            project.parent.mkdir(parents=True)
            global_path.parent.mkdir(parents=True)
            env = {"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg")}
            identity = {"backend": "codex", "model": "fixture", "effort": "high",
                        "verified": True, "verified_at": "2026-09-10T00:00:00Z"}
            def doc(values):
                return handoff_config.update_host("", identities={"fast_worker": values})
            def resolved():
                return handoff_config.resolve_config(repo, env=env)["fast_worker"]
            project.write_text(doc(identity))
            self.assertEqual("default", resolved()["permission_mode"])
            for mode in handoff_config.PERMISSION_MODES:
                with self.subTest(mode=mode):
                    text = doc(dict(identity, permission_mode=mode))
                    handoff_config.validate_config(text)
                    self.assertLess(text.index("effort ="), text.index("permission_mode ="))
                    self.assertLess(text.index("permission_mode ="), text.index("verified ="))
            for bad in ("", "bypassPermissions", "DEFAULT", True, 4):
                with self.subTest(bad=bad), self.assertRaises(handoff_config.ConfigValidationError):
                    doc(dict(identity, permission_mode=bad))
            global_path.write_text(doc(dict(identity, permission_mode="allow-all")))
            self.assertEqual("allow-all", resolved()["permission_mode"])
            self.assertTrue(resolved()["verified"])
            with patch.dict(os.environ, env), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, handoff_config.main([
                    "--repo", str(repo), "set", "--role", "fast_worker",
                    "--permission-mode", "default"]))
            self.assertEqual("default", resolved()["permission_mode"])
            self.assertTrue(resolved()["verified"])
            self.assertEqual(identity["verified_at"], resolved()["verified_at"])
            output = io.StringIO()
            with patch.dict(os.environ, env), contextlib.redirect_stdout(output):
                self.assertEqual(0, handoff_config.main([
                    "--repo", str(repo), "resolve", "--override",
                    "fast_worker.permission_mode=allow-all"]))
            fields = json.loads(output.getvalue())["fast_worker"]
            self.assertEqual("allow-all", fields["permission_mode"])
            self.assertTrue(fields["verified"])


class IdentityHelperTests(unittest.TestCase):
    def test_identity_name_owns_only_identity_headers(self):
        legacy = handoff_config.LEGACY_IDENTITY_PREFIX
        owned = {
            f"{legacy}deep_reasoner": "deep_reasoner",
            f"{legacy}deep_reasonr": "deep_reasonr",
            f"{legacy}review": "review",
            f"{legacy}deep_reasoner.x": "deep_reasoner.x",
            "deep_reasoner": "deep_reasoner",
            "e2e_verifier": "e2e_verifier",
            "deep_reasoner.x": "deep_reasoner.x",
        }
        for header, name in owned.items():
            with self.subTest(header=header):
                self.assertEqual(name, handoff_config._identity_name(header))
        for header in (
            None,
            "routing",
            "review",
            "notes",
            "future.plugins",
            legacy.rstrip("."),
            "hosts.codex.identities.deep_reasoner",
            "hosts.claude_code.roles.deep_reasoner",
        ):
            with self.subTest(header=header):
                self.assertIsNone(handoff_config._identity_name(header))

    def test_identities_of_returns_the_documents_own_tables_in_canonical_order(self):
        data = {
            "schema_version": 2,
            "review": {"spec_max_rounds": 1},
            "fast_worker": {"backend": "codex"},
            "deep_reasoner": {"backend": "claude"},
            "notes": {"x": 1},
        }
        identities = handoff_config.identities_of(data)
        self.assertEqual(["deep_reasoner", "fast_worker"], list(identities))
        self.assertIs(data["deep_reasoner"], identities["deep_reasoner"])
        self.assertIs(data["fast_worker"], identities["fast_worker"])
        self.assertEqual({}, handoff_config.identities_of({"schema_version": 2}))


class IdentityLayoutTests(unittest.TestCase):
    """Bare identity headers, and the legacy headers earlier releases wrote."""

    HEAD = "schema_version = 2\nrevision = 0\n\n"
    BODY = 'backend = "claude"\nmodel = "opus"\neffort = "high"\n'
    PREAMBLE = "# preamble comment, kept as written\nschema_version = 2\nrevision = 3\n\n"
    LEGACY_IDENTITIES = (
        "[hosts.claude_code.identities.deep_reasoner]\n"
        'backend = "claude"\n'
        'model = "opus"\n'
        'effort = "high"\n'
        "\n"
        "[hosts.claude_code.identities.fast_worker]\n"
        'backend = "codex"\n'
        'model = "gpt-fast"\n'
        'effort = "medium"\n'
        "\n"
    )
    CODEX_BLOCK = (
        "[hosts.codex.identities.deep_reasoner]\n"
        "# comment inside the codex block\n"
        'backend = "codex"\n'
        'model = "gpt-test" # untouched inline\n'
        'effort = "xhigh"\n'
        "\n"
    )
    TAIL = "[routing]\nalways_on_host_rules = false # routing comment\n"

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.repo = self.root / "repo"
        self.path = handoff_config.project_config_path(self.repo)
        # The CLI resolves the global file from os.environ; a real one on this
        # machine must not be able to answer for the fixture.
        self.env = {"HOME": str(self.root / "home"), "XDG_CONFIG_HOME": str(self.root / "xdg")}
        patcher = patch.dict(os.environ, self.env)
        patcher.start()
        self.addCleanup(patcher.stop)

    def legacy_file(self) -> str:
        return self.PREAMBLE + self.LEGACY_IDENTITIES + self.CODEX_BLOCK + self.TAIL

    def write_project(self, text: str) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8", newline="")
        return self.path

    def run_cli(self, *arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = handoff_config.main(["--repo", str(self.repo), *arguments])
        return status, stdout.getvalue(), stderr.getvalue()

    def test_legacy_document_and_its_bare_twin_validate_identically(self):
        legacy = self.legacy_file()
        pairs = (
            (legacy, legacy.replace("[hosts.claude_code.identities.", "[")),
            (document(), bare_document()),
        )
        for legacy_text, bare_text in pairs:
            with self.subTest(text=legacy_text[:40]):
                self.assertNotEqual(legacy_text, bare_text)
                parsed = handoff_config.validate_config(legacy_text)
                self.assertEqual(
                    ["deep_reasoner", "fast_worker"], list(handoff_config.identities_of(parsed))
                )
                self.assertEqual(parsed, handoff_config.validate_config(bare_text))

    def test_resolve_and_get_read_a_legacy_project_file_without_touching_it(self):
        path = self.write_project(self.legacy_file())
        before = path.read_bytes()
        self.assertEqual((0, "claude\n", ""), self.run_cli("resolve", "deep_reasoner.backend"))
        self.assertEqual((0, "claude\n", ""), self.run_cli("get", "deep_reasoner.backend"))
        self.assertEqual(
            (0, "gpt-fast\nmedium\n", ""),
            self.run_cli("resolve", "fast_worker.model", "fast_worker.effort"),
        )
        self.assertEqual(0, self.run_cli("validate")[0])
        self.assertEqual(before, path.read_bytes())

    def test_set_on_a_legacy_file_rewrites_bare_and_keeps_everything_else(self):
        path = self.write_project(self.legacy_file())
        status, _, error = self.run_cli("set", "--role", "fast_worker", "--effort", "high")
        self.assertEqual((0, ""), (status, error))
        updated = path.read_text(encoding="utf-8")
        self.assertNotIn("hosts.claude_code", updated)
        self.assertTrue(updated.startswith(self.PREAMBLE))
        self.assertIn(self.CODEX_BLOCK, updated)
        self.assertIn(self.TAIL, updated)
        # The bare sections stand where the first legacy section stood.
        positions = [
            updated.index(marker)
            for marker in ("[deep_reasoner]\n", "[fast_worker]\n", self.CODEX_BLOCK, "[routing]\n")
        ]
        self.assertEqual(sorted(positions), positions)
        parsed = handoff_config.validate_config(updated)
        self.assertEqual("high", parsed["fast_worker"]["effort"])
        self.assertEqual("gpt-fast", parsed["fast_worker"]["model"])
        self.assertEqual(
            handoff_config.identities_of(handoff_config.validate_config(self.legacy_file()))["deep_reasoner"],
            parsed["deep_reasoner"],
        )

    def test_set_review_on_a_legacy_file_also_rewrites_bare(self):
        path = self.write_project(self.legacy_file())
        before = handoff_config.identities_of(handoff_config.validate_config(self.legacy_file()))
        status, _, error = self.run_cli("set-review", "--spec-max-rounds", "2")
        self.assertEqual((0, ""), (status, error))
        updated = path.read_text(encoding="utf-8")
        self.assertNotIn("hosts.claude_code", updated)
        self.assertIn("[deep_reasoner]\n", updated)
        self.assertIn("[fast_worker]\n", updated)
        self.assertTrue(updated.startswith(self.PREAMBLE))
        self.assertIn(self.CODEX_BLOCK, updated)
        parsed = handoff_config.validate_config(updated)
        self.assertEqual(2, parsed["review"]["spec_max_rounds"])
        self.assertEqual(before, handoff_config.identities_of(parsed))

    def test_two_tables_for_one_identity_are_refused_in_every_form(self):
        legacy_header = "hosts.claude_code.identities.deep_reasoner"
        cases = {
            "bare then legacy": ("deep_reasoner", legacy_header),
            "legacy then bare": (legacy_header, "deep_reasoner"),
            "two bare": ("deep_reasoner", "deep_reasoner"),
            "two legacy": (legacy_header, legacy_header),
        }
        for label, (first, second) in cases.items():
            with self.subTest(label):
                prefix = f"{self.HEAD}[{first}]\n{self.BODY}\n"
                text = f"{prefix}[{second}]\n{self.BODY}"
                named = f"([{first}] and [{second}]); keep one."
                with self.assertRaises(handoff_config.ConfigParseError) as raised:
                    handoff_config.validate_config(text)
                self.assertIn(f"line {prefix.count(chr(10)) + 1}, column 1", str(raised.exception))
                self.assertIn("identity 'deep_reasoner' is defined twice", str(raised.exception))
                self.assertIn(named, str(raised.exception))
                path = self.write_project(text)
                before = path.read_bytes()
                for command in (
                    ("validate",),
                    ("set", "--role", "fast_worker", "--backend", "codex", "--model", "m", "--effort", "high"),
                ):
                    status, output, error = self.run_cli(*command)
                    self.assertEqual((2, ""), (status, output), command)
                    self.assertIn(named, error)
                self.assertEqual(before, path.read_bytes())

    def test_a_bare_and_a_legacy_table_for_different_identities_both_load(self):
        text = (
            f"{self.HEAD}[deep_reasoner]\n{self.BODY}\n"
            "[hosts.claude_code.identities.fast_worker]\n"
            'backend = "codex"\nmodel = "gpt-fast"\neffort = "medium"\n'
        )
        parsed = handoff_config.validate_config(text)
        self.assertEqual(["deep_reasoner", "fast_worker"], list(handoff_config.identities_of(parsed)))
        self.write_project(text)
        self.assertEqual(
            (0, "claude\ncodex\n", ""),
            self.run_cli("resolve", "deep_reasoner.backend", "fast_worker.backend"),
        )

    def test_unknown_legacy_identity_names_are_refused_before_anything_is_stored(self):
        typo = f"{self.HEAD}[hosts.claude_code.identities.deep_reasonr]\n{self.BODY}"
        legacy_review = "[hosts.claude_code.identities.review]\nspec_max_rounds = 9\n"
        review = "[review]\nspec_max_rounds = 2\n"
        cases = {
            "typo": (typo, "deep_reasonr"),
            "review after": (f"{self.HEAD}{review}\n{legacy_review}", "review"),
            "review before": (f"{self.HEAD}{legacy_review}\n{review}", "review"),
        }
        for label, (text, name) in cases.items():
            with self.subTest(label):
                with self.assertRaises(handoff_config.ConfigValidationError) as raised:
                    handoff_config.parse_config(text)
                self.assertIn(f"unsupported identity: '{name}'", str(raised.exception))
                with self.assertRaises(handoff_config.ConfigValidationError):
                    handoff_config.validate_config(text)
                path = self.write_project(text)
                before = path.read_bytes()
                with self.assertRaises(handoff_config.ConfigValidationError):
                    handoff_config.resolve_config(self.repo, env=self.env)
                # Nothing may print a review cap read from the refused table.
                self.assertEqual((2, ""), self.run_cli("resolve", "review.spec_max_rounds")[:2])
                self.assertEqual(2, self.run_cli("validate")[0])
                status, _, error = self.run_cli("set-review", "--spec-max-rounds", "4")
                self.assertEqual(2, status)
                self.assertIn(f"unsupported identity: '{name}'", error)
                self.assertEqual(before, path.read_bytes())
        # Control: without the legacy table the same file resolves its own cap.
        self.write_project(f"{self.HEAD}{review}")
        self.assertEqual((0, "2\n", ""), self.run_cli("resolve", "review.spec_max_rounds"))

    def test_nested_and_array_identity_tables_are_refused_and_unknown_bare_sections_survive(self):
        valid = f"{self.HEAD}[deep_reasoner]\n{self.BODY}\n"
        cases = {
            "dotted": ("[deep_reasoner.x]\nk = 1\n", "invalid owned identity section [deep_reasoner.x]"),
            "legacy dotted": (
                "[hosts.claude_code.identities.deep_reasoner.x]\nk = 1\n",
                "invalid owned identity section [hosts.claude_code.identities.deep_reasoner.x]",
            ),
            "array": ('[[deep_reasoner]]\nbackend = "claude"\n', "array-of-tables headers are not supported"),
        }
        for label, (extra, message) in cases.items():
            with self.subTest(label):
                with self.assertRaises(handoff_config.ConfigParseError) as raised:
                    handoff_config.validate_config(valid + extra)
                self.assertIn(message, str(raised.exception))
                path = self.write_project(valid + extra)
                before = path.read_bytes()
                self.assertEqual(2, self.run_cli("validate")[0])
                self.assertEqual(2, self.run_cli("set", "--role", "deep_reasoner", "--effort", "xhigh")[0])
                self.assertEqual(before, path.read_bytes())

        notes = '[notes]\nopaque = { inline = "table" } # not parsed, not ours\n\n'
        text = f"{self.HEAD}[deep_reasoner]\n{self.BODY}\n{notes}{self.TAIL}"
        path = self.write_project(text)
        self.assertEqual(0, self.run_cli("validate")[0])
        status, _, error = self.run_cli("set", "--role", "deep_reasoner", "--effort", "xhigh")
        self.assertEqual((0, ""), (status, error))
        updated = path.read_text(encoding="utf-8")
        self.assertIn(notes, updated)
        self.assertIn('effort = "xhigh"', updated)

        def unowned(source: str):
            return {
                chunk.name: chunk.text
                for chunk in handoff_config.split_sections(source)
                if chunk.name and handoff_config._identity_name(chunk.name) is None
            }

        self.assertEqual(unowned(text), unowned(updated))

    def test_resolution_merges_per_field_across_both_forms(self):
        global_path = handoff_config.global_config_path(self.env)
        global_path.parent.mkdir(parents=True)
        global_path.write_text(
            self.HEAD
            + "[hosts.claude_code.identities.deep_reasoner]\n"
            'backend = "codex"\n'
            'model = "global-model"\n'
            'effort = "high"\n'
            'permission_mode = "allow-all"\n'
            "verified = true\n"
            'verified_at = "2026-09-10T00:00:00Z"\n',
            encoding="utf-8",
        )
        # A layer is validated whole, so a project file naming only `effort`
        # is refused rather than completed from the global one.
        self.write_project(f'{self.HEAD}[deep_reasoner]\neffort = "xhigh"\n')
        with self.assertRaisesRegex(
            handoff_config.ConfigValidationError, r"deep_reasoner\.backend must be one of"
        ):
            handoff_config.resolve_config(self.repo, env=self.env)
        # The project layer changes only the effort of the legacy global identity.
        self.write_project(
            f"{self.HEAD}[deep_reasoner]\n"
            'backend = "codex"\n'
            'model = "global-model"\n'
            'effort = "xhigh"\n'
        )
        resolved = handoff_config.resolve_config(self.repo, env=self.env)
        self.assertEqual("project", resolved["source"])
        identity = resolved["deep_reasoner"]
        self.assertEqual(("codex", "global-model", "xhigh"), (
            identity["backend"], identity["model"], identity["effort"]))
        # Fields the project omits come from the legacy global layer, but a
        # changed identity no longer inherits the global verification.
        self.assertEqual("allow-all", identity["permission_mode"])
        self.assertFalse(identity["verified"])
        self.assertNotIn("verified_at", identity)
        self.assertEqual((0, "xhigh\n", ""), self.run_cli("resolve", "deep_reasoner.effort"))


if __name__ == "__main__":
    unittest.main()
