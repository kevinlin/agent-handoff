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
        original = document()
        before_chunks = {
            chunk.name: chunk.text
            for chunk in handoff_config.split_sections(original)
            if chunk.name and not chunk.name.startswith("hosts.claude_code.identities.")
        }
        identities = {
            "deep_reasoner": {"backend": "claude", "model": "new-opus", "effort": "high"},
            "fast_worker": {"backend": "codex", "model": "new-sonnet", "effort": "low"},
        }
        updated = handoff_config.update_host(original, identities=identities)
        after_chunks = {
            chunk.name: chunk.text
            for chunk in handoff_config.split_sections(updated)
            if chunk.name and not chunk.name.startswith("hosts.claude_code.identities.")
        }
        self.assertEqual(before_chunks, after_chunks)
        self.assertIn("# keep this top comment\r\n", updated)

    def test_stale_second_host_sections_survive_a_write(self):
        # Configs written by dual-host versions still carry hosts.codex.* blocks.
        # They are unowned now, so a write must leave them byte-identical.
        original = document()
        stale = "".join(
            chunk.text for chunk in handoff_config.split_sections(original)
            if chunk.name and chunk.name.startswith("hosts.codex.")
        )
        self.assertIn("gpt-test", stale)
        parsed = handoff_config.validate_config(original)
        identities = parsed["hosts"]["claude_code"]["identities"]
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
        identities = parsed["hosts"]["claude_code"]["identities"]
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
            "[hosts.claude_code.identities.deep_reasoner]\n"
            'backend = "codex"\n'
            f"{assignment}\n"
            'effort = "high"\n'
        )
        with self.assertRaises(handoff_config.ConfigParseError) as raised:
            handoff_config.validate_config(text)
        message = str(raised.exception)
        self.assertRegex(message, r"line \d+, column \d+")
        self.assertIn("docs/config-schema.md", message)

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
            "[hosts.claude_code.identities.deep_reasoner]\n"
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
            self.assertEqual("project", resolved["hosts"]["claude_code"]["identities"]["deep_reasoner"]["model"])
            self.assertEqual("codex", resolved["hosts"]["claude_code"]["identities"]["deep_reasoner"]["backend"])
            override = {"hosts": {"claude_code": {"identities": {"deep_reasoner": {"model": "session"}}}}}
            resolved = handoff_config.resolve_config(repo, session_override=override, env=env)
            self.assertEqual("session", resolved["source"])
            self.assertEqual("session", resolved["hosts"]["claude_code"]["identities"]["deep_reasoner"]["model"])
            project_path.unlink()
            resolved = handoff_config.resolve_config(repo, env=env)
            self.assertEqual("global", resolved["source"])
            global_path.unlink()
            resolved = handoff_config.resolve_config(repo, env=env)
            self.assertEqual("default", resolved["source"])
            self.assertEqual({}, resolved["hosts"]["claude_code"]["identities"])

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
            identity = resolved["hosts"]["claude_code"]["identities"]["deep_reasoner"]
            self.assertEqual("unverified-new", identity["model"])
            self.assertFalse(identity["verified"])
            self.assertNotIn("verified_at", identity)

            override = {
                "hosts": {
                    "claude_code": {
                        "identities": {
                            "deep_reasoner": {"model": "session-model"}
                        }
                    }
                }
            }
            resolved = handoff_config.resolve_config(
                repo, session_override=override, env=env
            )
            identity = resolved["hosts"]["claude_code"]["identities"]["deep_reasoner"]
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
                *base, "get", "hosts.claude_code.identities.deep_reasoner.model"
            )
            self.assertEqual((0, "chosen-model\n", ""), (status, output, error))
            status, _, error = self.run_cli(
                *base, "set", "--role", "deep_reasoner", "--effort", "xhigh"
            )
            self.assertEqual((0, ""), (status, error))
            status, output, error = self.run_cli(
                *base, "get", "hosts.claude_code.identities.deep_reasoner.backend"
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
                *base, "get", "hosts.claude_code.identities.arbiter"
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
                *base, "get", "hosts.claude_code.identities.deep_reasoner"
            )
            self.assertEqual((0, ""), (status, error))
            identity = json.loads(output)
            self.assertEqual("unverified-new", identity["model"])
            self.assertFalse(identity["verified"])
            self.assertNotIn("verified_at", identity)


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


if __name__ == "__main__":
    unittest.main()


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
                "[hosts.claude_code.identities.deep_reasoner]",
                "[hosts.claude_code.identities.fast_worker]",
                "[hosts.claude_code.identities.arbiter]",
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
