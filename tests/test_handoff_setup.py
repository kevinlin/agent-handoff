from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "handoff-setup.py"
SPEC = importlib.util.spec_from_file_location("handoff_setup", SCRIPT)
handoff_setup = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = handoff_setup
SPEC.loader.exec_module(handoff_setup)


class SetupTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.xdg = self.root / "xdg"
        self.codex_home = self.root / "codex-home"
        self.repo.mkdir()
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        claude = self.bin / "claude"
        claude.write_text(
            "#!/bin/sh\n"
            "if [ -n \"${HANDOFF_TEST_CLAUDE_ARGS:-}\" ]; then\n"
            "  printf '%s\\n' \"$@\" > \"$HANDOFF_TEST_CLAUDE_ARGS\"\n"
            "fi\n"
            "if [ -n \"${HANDOFF_TEST_CLAUDE_ENV:-}\" ]; then\n"
            "  env > \"$HANDOFF_TEST_CLAUDE_ENV\"\n"
            "fi\n"
            "printf 'HANDOFF_SMOKE_OK\\n'\n",
            encoding="utf-8",
        )
        claude.chmod(0o755)
        codex = self.bin / "codex"
        codex.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        codex.chmod(0o755)
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.home),
                "XDG_CONFIG_HOME": str(self.xdg),
                "CODEX_HOME": str(self.codex_home),
                "PATH": f"{self.bin}:/usr/bin:/bin",
            }
        )
        self.write_codex_native()

    def run_cli(self, *arguments):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            status = handoff_setup.main(list(arguments), env=self.env)
        return status, stdout.getvalue(), stderr.getvalue()

    def claude_args(self, action="--apply", *extra):
        return (
            action,
            "--repo",
            str(self.repo),
            "--exclude-choice",
            "track",
            *extra,
        )

    def custom_args(self, choices, action="--apply"):
        arguments = list(self.claude_args(action, "--mode", "custom"))
        for identity in handoff_setup.IDENTITIES:
            if identity not in choices:
                continue
            backend, model, effort = choices[identity]
            arguments.extend(("--role-backend", f"{identity}={backend}"))
            arguments.extend(("--role-model", f"{identity}={model}"))
            arguments.extend(("--role-effort", f"{identity}={effort}"))
        return tuple(arguments)

    def write_codex_native(self, model="gpt-detected", effort="xhigh"):
        self.codex_home.mkdir(parents=True, exist_ok=True)
        (self.codex_home / "config.toml").write_text(
            f'model = "{model}"\nmodel_reasoning_effort = "{effort}"\n',
            encoding="utf-8",
        )

    def snapshot(self):
        return {
            str(path.relative_to(self.root)): path.read_bytes()
            for path in self.root.rglob("*")
            if path.is_file()
        }

    def test_preview_is_zero_write_and_lists_every_target_with_diffs(self):
        before = self.snapshot()
        status, output, error = self.run_cli(
            *self.claude_args("--preview", "--routing-block")
        )
        self.assertEqual((0, ""), (status, error))
        self.assertEqual(before, self.snapshot())
        repo = self.repo.resolve()
        targets = (
            repo / ".handoff" / "config.toml",
            repo / ".claude" / "agents" / "handoff-deep-reasoner.md",
            repo / ".handoff" / ".generated-manifest",
            repo / "CLAUDE.md",
        )
        for target in targets:
            self.assertIn(str(target), output)
            self.assertIn(f"Diff: {target}", output)
        self.assertGreaterEqual(output.count("--- /dev/null"), len(targets))

    def test_custom_effort_must_match_selected_backend(self):
        choices = {
            "deep_reasoner": ("claude", "opus", "max"),
            "fast_worker": ("codex", "gpt-detected", "high"),
            "arbiter": ("codex", "gpt-detected", "xhigh"),
        }
        status, _, error = self.run_cli(*self.custom_args(choices, action="--preview"))
        self.assertEqual((0, ""), (status, error))

        for codex_only in ("minimal", "ultra"):
            choices["deep_reasoner"] = ("claude", "opus", codex_only)
            status, _, error = self.run_cli(*self.custom_args(choices, action="--preview"))
            self.assertEqual(2, status)
            self.assertIn("backend=claude", error)

        choices["deep_reasoner"] = ("claude", "opus", "high")
        choices["fast_worker"] = ("codex", "gpt-detected", "ultra")
        status, _, error = self.run_cli(*self.custom_args(choices, action="--preview"))
        self.assertEqual((0, ""), (status, error))

        choices["fast_worker"] = ("codex", "gpt-detected", "supreme")
        status, _, error = self.run_cli(*self.custom_args(choices, action="--preview"))
        self.assertEqual(2, status)
        self.assertIn("backend=codex", error)

    def test_apply_is_idempotent_for_config_and_agents(self):
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        config = self.repo / ".handoff" / "config.toml"
        agents = sorted((self.repo / ".claude" / "agents").glob("*.md"))
        before = {path: path.read_bytes() for path in [config, *agents]}
        status, output, error = self.run_cli(*self.claude_args())
        self.assertEqual((0, ""), (status, error))
        self.assertEqual(before, {path: path.read_bytes() for path in before})
        self.assertIn("UNCHANGED", output)

    def test_user_agent_is_refused_while_other_files_are_written(self):
        agent_dir = self.repo / ".claude" / "agents"
        agent_dir.mkdir(parents=True)
        protected = agent_dir / "handoff-deep-reasoner.md"
        protected.write_text("user content\n", encoding="utf-8")
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "quality"))
        self.assertEqual(1, status)
        self.assertIn("REFUSED", error)
        self.assertIn("references/setup.md", error)
        self.assertEqual("user content\n", protected.read_text(encoding="utf-8"))
        self.assertTrue((agent_dir / "handoff-fast-worker.md").is_file())
        self.assertTrue((self.repo / ".handoff" / "config.toml").is_file())
        manifest = json.loads(
            (self.repo / ".handoff" / ".generated-manifest").read_text(encoding="utf-8")
        )
        self.assertNotIn(str(protected), manifest)

    def test_managed_block_apply_and_remove_restore_original_bytes(self):
        target = self.repo / "CLAUDE.md"
        original = "# User rules\n\nKeep this byte-for-byte.\n"
        target.write_text(original, encoding="utf-8")
        self.assertEqual(
            0,
            self.run_cli(
                *self.claude_args("--apply", "--no-write-agents", "--routing-block")
            )[0],
        )
        managed = target.read_text(encoding="utf-8")
        self.assertIn(handoff_setup.BEGIN_MARKER, managed)
        self.assertNotIn("opus", managed)
        self.assertNotIn("high", managed)
        self.assertEqual(
            0,
            self.run_cli(
                *self.claude_args(
                    "--apply", "--no-write-agents", "--remove-routing-block"
                )
            )[0],
        )
        self.assertEqual(original, target.read_text(encoding="utf-8"))

    def test_managed_block_five_fail_closed_cases_and_force_boundary(self):
        valid = handoff_setup.render_managed_block(handoff_setup.CORE_IDENTITIES)
        digest_at = valid.index("sha256:") + len("sha256:")
        wrong_digit = "0" if valid[digest_at] != "0" else "1"
        hash_mismatch = valid[:digest_at] + wrong_digit + valid[digest_at + 1 :]
        malformed = {
            "missing_half": handoff_setup.BEGIN_MARKER + "\n",
            "duplicate": (
                handoff_setup.BEGIN_MARKER
                + "\n"
                + handoff_setup.BEGIN_MARKER
                + "\n"
                + handoff_setup.END_MARKER
                + "\n"
            ),
            "reversed": handoff_setup.END_MARKER + "\n" + handoff_setup.BEGIN_MARKER + "\n",
            "hash_mismatch": hash_mismatch,
            "hash_missing": (
                handoff_setup.BEGIN_MARKER
                + "\nchanged policy\n"
                + handoff_setup.END_MARKER
                + "\n"
            ),
        }
        for name, text in malformed.items():
            with self.subTest(name=name):
                with self.assertRaises(handoff_setup.SetupError):
                    handoff_setup.update_managed_block(text, handoff_setup.CORE_IDENTITIES)
        self.assertIn(
            handoff_setup.HASH_PREFIX,
            handoff_setup.update_managed_block(malformed["hash_missing"], handoff_setup.CORE_IDENTITIES, force=True),
        )
        with self.assertRaises(handoff_setup.SetupError):
            handoff_setup.update_managed_block(malformed["missing_half"], handoff_setup.CORE_IDENTITIES, force=True)
        empty = handoff_setup.BEGIN_MARKER + "\n" + handoff_setup.END_MARKER + "\n"
        self.assertIn(handoff_setup.HASH_PREFIX, handoff_setup.update_managed_block(empty, handoff_setup.CORE_IDENTITIES))

    def test_rollback_restores_latest_pre_apply_state_and_keeps_three_backups(self):
        modes = ("balanced", "quality", "cost", "balanced")
        previous = b""
        for index, mode in enumerate(modes, 1):
            config = self.repo / ".handoff" / "config.toml"
            previous = config.read_bytes() if config.exists() else b""
            status, _, error = self.run_cli(
                *self.claude_args(
                    "--apply",
                    "--mode",
                    mode,
                    "--timestamp",
                    f"20260720T00000{index}Z",
                )
            )
            self.assertEqual((0, ""), (status, error))
        backups = sorted((self.repo / ".handoff" / "backups").glob("*/manifest.json"))
        self.assertEqual(3, len(backups))
        status, _, error = self.run_cli(
            "--rollback",
            "--repo",
            str(self.repo),
        )
        self.assertEqual((0, ""), (status, error))
        self.assertEqual(previous, (self.repo / ".handoff" / "config.toml").read_bytes())

    def test_git_exclude_is_added_once(self):
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        arguments = (
            "--apply",
            "--repo",
            str(self.repo),
            "--no-write-agents",
        )
        self.assertEqual(0, self.run_cli(*arguments)[0])
        self.assertEqual(0, self.run_cli(*arguments)[0])
        exclude = self.repo / ".git" / "info" / "exclude"
        self.assertEqual(1, exclude.read_text(encoding="utf-8").splitlines().count(".handoff/config.toml"))

    def test_codex_without_detected_or_explicit_model_fails_with_guidance(self):
        (self.codex_home / "config.toml").unlink()
        status, _, error = self.run_cli(*self.claude_args("--preview"))
        self.assertEqual(2, status)
        self.assertIn("CODEX_HOME", error)
        self.assertIn("--role-model fast_worker", error)
        self.assertIn("--role-model arbiter", error)
        self.assertFalse((self.repo / ".handoff").exists())

    def test_balanced_preset_applies_three_identity_matrix(self):
        self.write_codex_native(model="gpt-injected", effort="low")
        status, _, error = self.run_cli(*self.claude_args())
        self.assertEqual((0, ""), (status, error))
        parsed = handoff_setup.handoff_config.validate_config(
            handoff_setup.read_text(self.repo / ".handoff" / "config.toml"),
            "claude_code",
        )
        identities = parsed["hosts"]["claude_code"]["identities"]
        self.assertEqual(
            ("claude", "opus", "high"),
            tuple(identities["deep_reasoner"][field] for field in ("backend", "model", "effort")),
        )
        self.assertEqual(
            ("codex", "gpt-injected", "high"),
            tuple(identities["fast_worker"][field] for field in ("backend", "model", "effort")),
        )
        self.assertEqual(
            ("codex", "gpt-injected", "xhigh"),
            tuple(identities["arbiter"][field] for field in ("backend", "model", "effort")),
        )

    def test_arbiter_agent_is_generated_only_for_claude_backend(self):
        arbiter = self.repo.resolve() / ".claude" / "agents" / "handoff-arbiter.md"
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        self.assertFalse(arbiter.exists())
        choices = {
            "deep_reasoner": ("claude", "opus", "high"),
            "fast_worker": ("codex", "gpt-injected", "medium"),
            "arbiter": ("claude", "sonnet", "high"),
        }
        self.assertEqual(0, self.run_cli(*self.custom_args(choices))[0])
        self.assertTrue(arbiter.is_file())
        rendered = arbiter.read_text(encoding="utf-8")
        self.assertIn("Independent blind arbiter", rendered)
        self.assertIn("carries no one else's answer", rendered)

    def test_v1_config_is_replaced_by_a_fresh_v2_document_and_backed_up(self):
        config = self.repo / ".handoff" / "config.toml"
        config.parent.mkdir()
        original = """schema_version = 1
revision = 7

[hosts.claude_code.roles.deep_reasoner]
model = "claude-old-deep"
effort = "high"

[routing]
always_on_host_rules = false
"""
        config.write_text(original, encoding="utf-8")
        status, output, error = self.run_cli(*self.claude_args("--preview"))
        self.assertEqual((0, ""), (status, error))
        self.assertIn("NOTE: v1 config replaced by a fresh schema v2 document", output)
        self.assertEqual(original, config.read_text(encoding="utf-8"))

        status, _, error = self.run_cli(*self.claude_args())
        self.assertEqual((0, ""), (status, error))
        rewritten = config.read_text(encoding="utf-8")
        self.assertIn("schema_version = 2", rewritten)
        self.assertNotIn("roles", rewritten)
        identities = handoff_setup.handoff_config.validate_config(
            rewritten
        )["hosts"]["claude_code"]["identities"]
        self.assertEqual("opus", identities["deep_reasoner"]["model"])

        backups = sorted((self.repo / ".handoff" / "backups").glob("*/files/*"))
        self.assertTrue(any(path.read_text(encoding="utf-8") == original for path in backups))

    def test_missing_codex_cli_refuses_apply_without_writing(self):
        claude_only = self.root / "claude-only-bin"
        claude_only.mkdir()
        executable = claude_only / "claude"
        executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        executable.chmod(0o755)
        self.env["PATH"] = f"{claude_only}:/usr/bin:/bin"
        before = self.snapshot()
        status, output, error = self.run_cli(*self.claude_args("--preview"))
        self.assertEqual((0, ""), (status, error))
        self.assertIn("fast_worker: backend=codex", output)
        self.assertIn("availability=unavailable", output)
        self.assertEqual(before, self.snapshot())
        status, _, error = self.run_cli(*self.claude_args())
        self.assertEqual(2, status)
        self.assertIn("required backend CLI unavailable", error)
        self.assertIn("codex", error)
        self.assertEqual(before, self.snapshot())

    def test_same_vendor_note_is_shown(self):
        choices = {
            "deep_reasoner": ("claude", "opus", "high"),
            "fast_worker": ("claude", "sonnet", "medium"),
            "arbiter": ("claude", "opus", "high"),
        }
        status, output, error = self.run_cli(*self.custom_args(choices, "--preview"))
        self.assertEqual((0, ""), (status, error))
        self.assertIn("NOTE: blind-review value reduced (same-vendor)", output)

    def test_switching_agent_backend_to_codex_deletes_tracked_file(self):
        all_claude = {
            "deep_reasoner": ("claude", "opus", "high"),
            "fast_worker": ("claude", "sonnet", "medium"),
            "arbiter": ("claude", "opus", "high"),
        }
        self.assertEqual(0, self.run_cli(*self.custom_args(all_claude))[0])
        arbiter = self.repo.resolve() / ".claude" / "agents" / "handoff-arbiter.md"
        self.assertTrue(arbiter.is_file())
        switched = dict(all_claude)
        switched["arbiter"] = ("codex", "gpt-injected", "xhigh")
        status, preview, error = self.run_cli(*self.custom_args(switched, "--preview"))
        self.assertEqual((0, ""), (status, error))
        self.assertIn(f"[DELETE] {arbiter}", preview)
        status, _, error = self.run_cli(*self.custom_args(switched))
        self.assertEqual((0, ""), (status, error))
        self.assertFalse(arbiter.exists())
        manifest = json.loads(
            (self.repo / ".handoff" / ".generated-manifest").read_text(encoding="utf-8")
        )
        self.assertNotIn(str(arbiter), manifest)

    def test_codex_smoke_records_exact_timestamp(self):
        self.write_codex_native()
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        timestamp = "2026-07-20T01:02:03Z"
        status, output, error = self.run_cli(
            "--smoke",
            "--repo",
            str(self.repo),
            "--timestamp",
            timestamp,
        )
        self.assertEqual((0, ""), (status, error))
        self.assertEqual(3, output.count("PASS"))
        parsed = handoff_setup.handoff_config.validate_config(
            handoff_setup.read_text(self.repo / ".handoff" / "config.toml")
        )
        identities = parsed["hosts"]["claude_code"]["identities"]
        for identity in handoff_setup.ordered(identities):
            self.assertTrue(identities[identity]["verified"])
            self.assertEqual(timestamp, identities[identity]["verified_at"])

        status, status_output, error = self.run_cli(
            "--status", "--repo", str(self.repo)
        )
        self.assertEqual((0, ""), (status, error))
        self.assertIn("config_source=project", status_output)
        self.assertIn("model=gpt-detected", status_output)
        self.assertIn("effort=high", status_output)
        self.assertIn("verified=true", status_output)
        self.assertIn(f"verified_at={timestamp}", status_output)

    def test_uninstall_removes_manifest_tracked_agents_and_updates_manifest(self):
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        deep_agent = self.repo.resolve() / ".claude" / "agents" / "handoff-deep-reasoner.md"
        fast_agent = self.repo.resolve() / ".claude" / "agents" / "handoff-fast-worker.md"
        self.assertTrue(deep_agent.is_file())
        self.assertFalse(fast_agent.exists())

        status, output, error = self.run_cli(
            "--uninstall", "--repo", str(self.repo)
        )
        self.assertEqual((0, ""), (status, error))
        self.assertIn(f"REMOVED {deep_agent}", output)
        self.assertFalse(deep_agent.exists())
        self.assertFalse(fast_agent.exists())
        manifest = json.loads(
            (self.repo / ".handoff" / ".generated-manifest").read_text(encoding="utf-8")
        )
        self.assertNotIn(str(deep_agent), manifest)
        self.assertNotIn(str(fast_agent), manifest)
        # Config itself is untouched by a plain uninstall.
        self.assertTrue((self.repo / ".handoff" / "config.toml").is_file())

    def test_uninstall_skips_agent_modified_since_generation(self):
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        deep_agent = self.repo.resolve() / ".claude" / "agents" / "handoff-deep-reasoner.md"
        deep_agent.write_text("hand-edited by the user\n", encoding="utf-8")

        status, _, error = self.run_cli(
            "--uninstall", "--repo", str(self.repo)
        )
        self.assertEqual(0, status)
        self.assertIn("modified since generation", error)
        self.assertEqual("hand-edited by the user\n", deep_agent.read_text(encoding="utf-8"))

    def test_uninstall_dry_run_removes_nothing(self):
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        deep_agent = self.repo.resolve() / ".claude" / "agents" / "handoff-deep-reasoner.md"
        before = deep_agent.read_bytes()

        status, output, error = self.run_cli(
            "--uninstall", "--repo", str(self.repo), "--dry-run"
        )
        self.assertEqual((0, ""), (status, error))
        self.assertIn(f"WOULD_REMOVE {deep_agent}", output)
        self.assertTrue(deep_agent.exists())
        self.assertEqual(before, deep_agent.read_bytes())

    def test_uninstall_removes_managed_block_and_restores_user_content(self):
        target = self.repo / "CLAUDE.md"
        original = "# User rules\n\nKeep this byte-for-byte.\n"
        target.write_text(original, encoding="utf-8")
        self.assertEqual(
            0,
            self.run_cli(*self.claude_args("--apply", "--no-write-agents", "--routing-block"))[0],
        )
        self.assertIn(handoff_setup.BEGIN_MARKER, target.read_text(encoding="utf-8"))

        status, output, error = self.run_cli(
            "--uninstall", "--repo", str(self.repo)
        )
        self.assertEqual((0, ""), (status, error))
        self.assertIn("managed routing block", output)
        self.assertEqual(original, target.read_text(encoding="utf-8"))

    def test_uninstall_remove_config_clears_only_the_owned_identities(self):
        self.write_codex_native()
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        config = self.repo / ".handoff" / "config.toml"
        # A config written by a dual-host version still carries this block.
        stale = (
            "\n[hosts.codex.identities.deep_reasoner]\n"
            'backend = "codex"\n'
            'model = "stale-model" # keep this byte-for-byte\n'
            'effort = "xhigh"\n'
        )
        config.write_text(
            handoff_setup.read_text(config) + stale, encoding="utf-8"
        )
        codex_before = "".join(
            chunk.text
            for chunk in handoff_setup.handoff_config.split_sections(handoff_setup.read_text(config))
            if chunk.name and chunk.name.startswith("hosts.codex.identities.")
        )
        self.assertIn("stale-model", codex_before)

        status, output, error = self.run_cli(
            "--uninstall", "--repo", str(self.repo), "--remove-config"
        )
        self.assertEqual((0, ""), (status, error))
        self.assertIn("identities cleared", output)
        parsed = handoff_setup.handoff_config.validate_config(
            handoff_setup.read_text(config)
        )
        self.assertEqual({}, parsed["hosts"]["claude_code"]["identities"])
        codex_after = "".join(
            chunk.text
            for chunk in handoff_setup.handoff_config.split_sections(handoff_setup.read_text(config))
            if chunk.name and chunk.name.startswith("hosts.codex.identities.")
        )
        self.assertEqual(codex_before, codex_after)

    def test_claude_smoke_uses_fresh_session_and_records_verified(self):
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        arguments_log = self.root / "claude-smoke-args.txt"
        self.env["HANDOFF_TEST_CLAUDE_ARGS"] = str(arguments_log)
        status, output, error = self.run_cli(
            "--smoke",
            "--repo",
            str(self.repo),
            "--timestamp",
            "2026-07-20T01:02:03Z",
        )
        self.assertEqual((0, ""), (status, error))
        self.assertIn("PASS (fresh Claude session)", output)
        arguments = arguments_log.read_text(encoding="utf-8").splitlines()
        self.assertIn("--no-session-persistence", arguments)
        self.assertIn("--no-chrome", arguments)
        self.assertIn("--tools", arguments)
        self.assertIn("--agent", arguments)
        self.assertIn("handoff-deep-reasoner", arguments)
        parsed = handoff_setup.handoff_config.validate_config(
            handoff_setup.read_text(self.repo / ".handoff" / "config.toml"),
            "claude_code",
        )
        identities = parsed["hosts"]["claude_code"]["identities"]
        for identity in handoff_setup.ordered(identities):
            self.assertTrue(identities[identity]["verified"])
            self.assertEqual(
                "2026-07-20T01:02:03Z", identities[identity]["verified_at"]
            )

    def test_nested_claude_env_strips_host_credential_vars(self):
        source = {
            "PATH": "/usr/bin",
            "HOME": "/home/carl",
            "ANTHROPIC_BASE_URL": "https://poisoned.example",
            "ANTHROPIC_AUTH_TOKEN": "poisoned-token",
            "CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST": "1",
            "CLAUDE_CODE_SSE_PORT": "12345",
            "CLAUDECODE": "1",
        }
        cleaned = handoff_setup.clean_claude_env(source)
        for key in cleaned:
            self.assertFalse(key.startswith("ANTHROPIC_"), key)
            self.assertFalse(key.startswith("CLAUDE_CODE_"), key)
        self.assertEqual("", cleaned["CLAUDECODE"])
        self.assertEqual("/usr/bin", cleaned["PATH"])
        self.assertEqual("/home/carl", cleaned["HOME"])

    def test_claude_smoke_strips_host_credential_env_vars(self):
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        env_log = self.root / "claude-smoke-env.txt"
        self.env["HANDOFF_TEST_CLAUDE_ENV"] = str(env_log)
        self.env["ANTHROPIC_BASE_URL"] = "https://poisoned.example"
        self.env["ANTHROPIC_AUTH_TOKEN"] = "poisoned-token"
        self.env["CLAUDE_CODE_PROVIDER_MANAGED_BY_HOST"] = "1"
        self.env["CLAUDECODE"] = "1"
        status, output, error = self.run_cli(
            "--smoke",
            "--repo",
            str(self.repo),
            "--timestamp",
            "2026-07-20T01:02:03Z",
        )
        self.assertEqual((0, ""), (status, error))
        self.assertIn("PASS (fresh Claude session)", output)
        captured = dict(
            line.split("=", 1)
            for line in env_log.read_text(encoding="utf-8").splitlines()
            if "=" in line
        )
        for key in captured:
            self.assertFalse(key.startswith("ANTHROPIC_"), key)
            self.assertFalse(key.startswith("CLAUDE_CODE_"), key)
        self.assertEqual("", captured.get("CLAUDECODE", ""))

    def test_claude_smoke_failure_keeps_only_that_identity_unverified(self):
        self.assertEqual(0, self.run_cli(*self.claude_args())[0])
        claude = self.bin / "claude"
        claude.write_text(
            "#!/bin/sh\nprintf 'model unavailable\\n' >&2\nexit 3\n",
            encoding="utf-8",
        )
        status, output, error = self.run_cli(
            "--smoke",
            "--repo",
            str(self.repo),
            "--timestamp",
            "2026-07-20T01:02:03Z",
        )
        self.assertEqual(1, status)
        self.assertIn("deep_reasoner: FAIL", error)
        self.assertIn("model unavailable", error)
        self.assertEqual(2, output.count("PASS"))
        parsed = handoff_setup.handoff_config.validate_config(
            handoff_setup.read_text(self.repo / ".handoff" / "config.toml")
        )
        identities = parsed["hosts"]["claude_code"]["identities"]
        self.assertFalse(identities["deep_reasoner"]["verified"])
        self.assertTrue(identities["fast_worker"]["verified"])
        self.assertTrue(identities["arbiter"]["verified"])

    def configured(self):
        config = self.repo / ".handoff" / "config.toml"
        parsed = handoff_setup.handoff_config.validate_config(
            config.read_text(encoding="utf-8")
        )
        return parsed["hosts"]["claude_code"]["identities"]

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
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertEqual((0, ""), (status, error))
        status, _, error = self.run_cli(*self.claude_args("--smoke"))
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
        agents = self.repo / ".claude" / "agents"
        specifier = agents / "handoff-e2e-specifier.md"
        self.assertTrue(specifier.exists())
        self.assertIn("name: handoff-e2e-specifier", specifier.read_text(encoding="utf-8"))
        # backend=codex identities never get a Claude subagent definition
        self.assertFalse((agents / "handoff-e2e-verifier.md").exists())


class SpecReviewToggleTests(SetupTests):
    def configured(self):
        resolved = handoff_setup.handoff_config.resolve_config(self.repo, env=self.env)
        return resolved["hosts"][handoff_setup.handoff_config.HOST]["identities"]

    def test_default_apply_omits_the_toggle(self):
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertEqual((0, ""), (status, error))
        self.assertNotIn("auto_review_spec", self.configured()["deep_reasoner"])

    def test_flag_writes_the_toggle_on_deep_reasoner_only(self):
        status, _, error = self.run_cli(
            *self.claude_args("--apply", "--mode", "balanced", "--spec-review")
        )
        self.assertEqual((0, ""), (status, error))
        identities = self.configured()
        self.assertIs(True, identities["deep_reasoner"]["auto_review_spec"])
        for identity in ("fast_worker", "arbiter"):
            self.assertNotIn("auto_review_spec", identities[identity])

    def test_reapplying_without_the_flag_removes_it(self):
        self.run_cli(*self.claude_args("--apply", "--mode", "balanced", "--spec-review"))
        status, _, error = self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertEqual((0, ""), (status, error))
        self.assertNotIn("auto_review_spec", self.configured()["deep_reasoner"])

    def test_toggle_survives_custom_mode_and_keeps_verification(self):
        choices = {
            "deep_reasoner": ("claude", "opus", "high"),
            "fast_worker": ("codex", "gpt-detected", "medium"),
            "arbiter": ("codex", "gpt-detected", "xhigh"),
        }
        status, _, error = self.run_cli(*self.custom_args(choices), "--spec-review")
        self.assertEqual((0, ""), (status, error))
        self.assertIs(True, self.configured()["deep_reasoner"]["auto_review_spec"])
        status, _, error = self.run_cli(*self.claude_args("--smoke"))
        self.assertEqual((0, ""), (status, error))
        identity = self.configured()["deep_reasoner"]
        # the toggle is not a routing value: smoke keeps it, and it never
        # invalidated the verification it just wrote
        self.assertIs(True, identity["auto_review_spec"])
        self.assertIs(True, identity["verified"])

    def test_status_reports_the_toggle_for_deep_reasoner_only(self):
        self.run_cli(*self.claude_args("--apply", "--mode", "balanced", "--spec-review"))
        status, output, _ = self.run_cli(*self.claude_args("--status"))
        self.assertEqual(0, status)
        self.assertIn("spec_review=true", output)
        self.assertEqual(1, output.count("spec_review="))
        self.run_cli(*self.claude_args("--apply", "--mode", "balanced"))
        self.assertIn("spec_review=false", self.run_cli(*self.claude_args("--status"))[1])


if __name__ == "__main__":
    unittest.main()
