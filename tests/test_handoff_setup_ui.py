from __future__ import annotations

import contextlib
import http.client
import importlib.util
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "handoff-setup-ui.py"
SPEC = importlib.util.spec_from_file_location("handoff_setup_ui", SCRIPT)
handoff_setup_ui = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = handoff_setup_ui
SPEC.loader.exec_module(handoff_setup_ui)


class SetupUITests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.home = self.root / "home"
        self.codex_home = self.root / "codex-home"
        self.xdg = self.root / "xdg"
        self.bin = self.root / "bin"
        for path in (self.repo, self.home, self.codex_home, self.xdg, self.bin):
            path.mkdir()
        claude = self.bin / "claude"
        claude.write_text(
            "#!/bin/sh\n"
            "if [ \"$1\" = \"--help\" ]; then\n"
            "  printf '%s\\n' '--effort <level>  Effort level for the current session'\n"
            "  printf '%s\\n' '                  (low, medium, high, xhigh, max)'\n"
            "  printf \"Provide\\n  an alias for the latest model (e.g.\\n  'fable', 'opus', or 'sonnet').\\n\"\n"
            "else\n"
            "  printf 'Claude Code 9.9\\n'\n"
            "fi\n",
            encoding="utf-8",
        )
        claude.chmod(0o755)
        codex = self.bin / "codex"
        codex.write_text(
            f"#!{sys.executable}\n"
            "import json\n"
            "import sys\n"
            "if '--version' in sys.argv:\n"
            "    print('codex-cli 8.8')\n"
            "elif len(sys.argv) > 1 and sys.argv[1] == 'app-server':\n"
            "    for line in sys.stdin:\n"
            "        message = json.loads(line)\n"
            "        if message.get('id') == 1:\n"
            "            print(json.dumps({'id': 1, 'result': {'userAgent': 'test'}}), flush=True)\n"
            "        elif message.get('id') == 2:\n"
            "            models = [\n"
            "                {'model': 'gpt-catalog', 'displayName': 'GPT Catalog', "
            "'description': 'Account model', 'isDefault': True, "
            "'supportedReasoningEfforts': [{'reasoningEffort': 'low'}, "
            "{'reasoningEffort': 'high'}, {'reasoningEffort': 'none'}]},\n"
            "                {'model': 'gpt-catalog-fast', 'displayName': 'GPT Catalog Fast', "
            "'description': 'Fast model', 'isDefault': False, "
            "'supportedReasoningEfforts': [{'reasoningEffort': 'medium'}]},\n"
            "            ]\n"
            "            print(json.dumps({'id': 2, 'result': {'data': models, "
            "'nextCursor': None}}), flush=True)\n",
            encoding="utf-8",
        )
        codex.chmod(0o755)
        (self.codex_home / "config.toml").write_text(
            'model = "gpt-detected"\nmodel_reasoning_effort = "xhigh"\n',
            encoding="utf-8",
        )
        self.env = os.environ.copy()
        self.env.update(
            {
                "HOME": str(self.home),
                "CODEX_HOME": str(self.codex_home),
                "XDG_CONFIG_HOME": str(self.xdg),
                "PATH": f"{self.bin}:/usr/bin:/bin",
                "HANDOFF_CODEX_BIN": str(self.bin / "codex"),
            }
        )

    def payload(self, controller, mode="balanced"):
        state = controller.state()
        identities = {
            identity: {
                field: state["presets"][mode][identity][field]
                for field in ("backend", "model", "effort")
            }
            for identity in handoff_setup_ui.engine.IDENTITIES
        }
        return {
            "mode": mode,
            "review": dict(state["initial_review"]),
            "identities": identities,
            "scope": "project",
            "exclude_choice": "track",
            "routing_action": "none",
            "write_agents": False,
            "smoke": False,
        }

    def test_permission_payload_presets_page_and_reload(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        raw = self.payload(controller)
        normalized = handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)
        self.assertTrue(all(v["permission_mode"] == "default" for v in normalized["identities"].values()))
        raw["identities"]["fast_worker"]["permission_mode"] = "allow-all"
        with self.assertRaisesRegex(handoff_setup_ui.UIError, "custom"):
            handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)
        raw["mode"] = "custom"
        normalized = handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)
        args = handoff_setup_ui.engine_arguments(normalized, "--preview")
        self.assertIn("--role-permission-mode", args)
        self.assertIn("fast_worker=allow-all", args)
        self.assertTrue(controller.preview(raw)["ok"])
        applied = controller.apply(raw)
        self.assertTrue(applied["ok"], applied)
        state = handoff_setup_ui.SetupController(self.repo, self.env).state()
        self.assertEqual("allow-all", state["initial_matrix"]["fast_worker"]["permission_mode"])
        self.assertEqual(["default", "allow-all"], state["permission_modes"])
        raw["identities"]["fast_worker"]["permission_mode"] = "unsafe"
        with self.assertRaisesRegex(handoff_setup_ui.UIError, "permission_mode"):
            handoff_setup_ui.normalize_payload(raw, repo=self.repo, env=self.env)
        page = SCRIPT.read_text()
        self.assertIn('data-field="permission_mode"', page)
        self.assertIn("permission_mode: matrix[identity].permission_mode", page)
        self.assertIn("state.permission_labels[p]", page)
        self.assertIn("values.permission_mode || 'default'", page)
        self.assertIn('role="switch"', page)
        self.assertIn("event.target.checked ? 'allow-all' : 'default'", page)

    def test_state_shows_exact_detected_models_and_full_presets(self):
        state = handoff_setup_ui.build_state(self.repo, self.env)
        self.assertEqual("default", state["config_source"])
        self.assertEqual("gpt-detected", state["detected"]["codex_model"])
        self.assertEqual("xhigh", state["detected"]["codex_effort"])
        self.assertEqual("Claude Code 9.9", state["clis"]["claude"]["version"])
        self.assertEqual(
            ("codex", "gpt-detected", "high", "detected"),
            tuple(
                state["presets"]["balanced"]["fast_worker"][field]
                for field in ("backend", "model", "effort", "model_source")
            ),
        )
        self.assertEqual(
            ("claude", "opus", "high"),
            tuple(
                state["presets"]["quality"]["fast_worker"][field]
                for field in ("backend", "model", "effort")
            ),
        )
        self.assertEqual(
            ["gpt-catalog", "gpt-catalog-fast", "gpt-detected"],
            [option["value"] for option in state["model_options"]["codex"]],
        )
        self.assertEqual(
            ["low", "high"], state["model_options"]["codex"][0]["efforts"]
        )
        self.assertEqual(
            ["fable", "opus", "sonnet", "haiku"],
            [option["value"] for option in state["model_options"]["claude"]],
        )
        self.assertEqual(
            ["low", "medium", "high", "xhigh", "max"],
            state["model_options"]["claude"][0]["efforts"],
        )
        self.assertEqual(
            {
                "claude": ["low", "medium", "high", "xhigh", "max"],
                "codex": ["minimal", "low", "medium", "high", "xhigh", "max", "ultra"],
                "copilot": ["none", "minimal", "low", "medium", "high", "xhigh", "max"],
            },
            state["efforts_by_backend"],
        )
        self.assertEqual("Read from Codex CLI", state["model_discovery"]["codex"])

    def test_claude_context_variants_are_normalized_and_deduplicated(self):
        options, _ = handoff_setup_ui._claude_model_options(
            str(self.bin / "claude"),
            self.env,
            {
                "opus": "claude-opus-4-6[1m] 1M",
                "opus_duplicate": "claude-opus-4-6",
                "haiku": "haiku 1M",
            },
        )
        values = [option["value"] for option in options]
        self.assertEqual(1, values.count("haiku"))
        self.assertEqual(1, values.count("claude-opus-4-6"))
        self.assertNotIn("claude-opus-4-6[1m] 1M", values)

    def test_preview_is_zero_write_and_apply_requires_the_same_payload(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        payload = self.payload(controller)
        preview = controller.preview(payload)
        self.assertTrue(preview["ok"], preview)
        self.assertIn("fast_worker: backend=codex", preview["output"])
        config = self.repo / ".handoff" / "config.toml"
        self.assertFalse(config.exists())

        changed = dict(payload)
        changed["scope"] = "global"
        with self.assertRaisesRegex(handoff_setup_ui.UIError, "exact preview"):
            controller.apply(changed)

        applied = controller.apply(payload)
        self.assertTrue(applied["ok"], applied)
        self.assertTrue(config.is_file())
        status = handoff_setup_ui.engine.handoff_config.resolve_config(
            self.repo, env=self.env
        )
        self.assertEqual(
            "gpt-detected",
            status["hosts"]["claude_code"]["identities"]["fast_worker"]["model"],
        )

    def test_manual_matrix_requires_custom_mode(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        payload = self.payload(controller)
        payload["identities"]["fast_worker"]["effort"] = "low"
        with self.assertRaisesRegex(handoff_setup_ui.UIError, "custom mode"):
            handoff_setup_ui.normalize_payload(
                payload,
                repo=self.repo,
                env=self.env,
            )
        payload["mode"] = "custom"
        normalized = handoff_setup_ui.normalize_payload(
            payload,
            repo=self.repo,
            env=self.env,
        )
        self.assertEqual("low", normalized["identities"]["fast_worker"]["effort"])

    def test_payload_rejects_effort_not_supported_by_backend_or_model(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        payload = self.payload(controller)
        payload["mode"] = "custom"
        payload["identities"]["deep_reasoner"]["effort"] = "minimal"
        with self.assertRaisesRegex(handoff_setup_ui.UIError, "claude/opus"):
            handoff_setup_ui.normalize_payload(
                payload,
                repo=self.repo,
                env=self.env,
                model_options=controller.initial_state["model_options"],
            )

        payload = self.payload(controller)
        payload["mode"] = "custom"
        payload["identities"]["fast_worker"]["model"] = "gpt-catalog-fast"
        payload["identities"]["fast_worker"]["effort"] = "high"
        with self.assertRaisesRegex(handoff_setup_ui.UIError, "allowed values: medium"):
            handoff_setup_ui.normalize_payload(
                payload,
                repo=self.repo,
                env=self.env,
                model_options=controller.initial_state["model_options"],
            )

    def serve(self, token="test-token"):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        server = ThreadingHTTPServer(
            ("127.0.0.1", 0), handoff_setup_ui.make_handler(controller, token)
        )
        server.daemon_threads = True
        thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.shutdown)
        self.addCleanup(server.server_close)
        return server.server_port

    def request(self, port, method, path, headers=None, body=None):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
        try:
            connection.request(method, path, body=body, headers=headers or {})
            response = connection.getresponse()
            return response.status, response.read().decode("utf-8")
        finally:
            connection.close()

    def test_server_refuses_anything_without_the_local_token(self):
        port = self.serve()
        status, body = self.request(port, "GET", "/?token=test-token")
        self.assertEqual(200, status)
        self.assertIn("<!doctype html>", body)

        self.assertEqual(403, self.request(port, "GET", "/")[0])
        self.assertEqual(403, self.request(port, "GET", "/?token=wrong")[0])
        self.assertEqual(403, self.request(port, "GET", "/api/state")[0])
        self.assertEqual(
            403, self.request(port, "POST", "/api/preview?token=wrong", body=b"{}")[0]
        )

    def test_unauthorized_post_with_a_body_still_returns_readable_403_json(self):
        port = self.serve()
        body = json.dumps({"mode": "custom", "pad": "x" * 65536}).encode("utf-8")
        for headers in (
            {},
            {"X-Handoff-Token": "wrong"},
            {"X-Handoff-Token": "test-token", "Host": "attacker.example"},
        ):
            with self.subTest(headers=headers):
                status, payload = self.request(
                    port,
                    "POST",
                    "/api/apply",
                    headers={"Content-Type": "application/json", **headers},
                    body=body,
                )
                self.assertEqual(403, status)
                self.assertEqual("Invalid local access token", json.loads(payload)["error"])

    def test_server_refuses_a_non_loopback_host_header(self):
        port = self.serve()
        status, _ = self.request(
            port, "GET", "/?token=test-token", headers={"Host": "attacker.example"}
        )
        self.assertEqual(403, status)

    def test_server_rejects_an_oversized_or_unparsable_body(self):
        port = self.serve()
        headers = {"X-Handoff-Token": "test-token", "Content-Type": "application/json"}
        status, body = self.request(
            port,
            "POST",
            "/api/preview",
            headers={**headers, "Content-Length": "999999"},
        )
        self.assertEqual(400, status)
        self.assertIn("Invalid request size", body)

        status, body = self.request(port, "POST", "/api/preview", headers=headers, body=b"not json")
        self.assertEqual(400, status)
        self.assertIn("not valid JSON", body)

    def test_ui_keeps_its_accessibility_and_theme_contract(self):
        html = handoff_setup_ui.HTML
        for required in (
            'role="status" aria-live="polite"',
            'aria-describedby="${identity}-source"',
            "prefers-reduced-motion:reduce",
            ':root[data-theme="dark"]',
            "prefers-color-scheme: dark",
            'id="themeSwitch"',
        ):
            self.assertIn(required, html)

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

    def test_state_fills_unconfigured_e2e_identities_from_balanced(self):
        with contextlib.redirect_stdout(io.StringIO()):
            handoff_setup_ui.engine.main(
                ["--apply", "--repo", str(self.repo), "--exclude-choice", "track", "--no-write-agents"],
                env=self.env,
            )
        state = handoff_setup_ui.build_state(self.repo, self.env)
        self.assertEqual("custom", state["initial_mode"])
        for identity in state["optional_identities"]:
            self.assertEqual(state["presets"]["balanced"][identity], state["initial_matrix"][identity])

COPILOT_FAKE = """#!/bin/sh
printf '%s\\n' "$@" >> "$HANDOFF_TEST_COPILOT_ARGS"
case "$1" in
  --version) printf 'GitHub Copilot CLI 1.0.83.\\n'; exit 0 ;;
esac
printf 'HANDOFF_SMOKE_OK\\n'
"""


class CopilotSetupUITests(SetupUITests):
    """Task 5: typed model entry, validated against the CLI, with no probe."""

    def setUp(self):
        super().setUp()
        self.copilot_log = self.root / "copilot-ui-args.txt"
        copilot = self.bin / "copilot"
        copilot.write_text(COPILOT_FAKE, encoding="utf-8")
        copilot.chmod(0o755)
        self.env["HANDOFF_TEST_COPILOT_ARGS"] = str(self.copilot_log)

    def copilot_payload(self, model="mai-code-1.1-flash", effort="medium"):
        return {
            "mode": "custom",
            "identities": {
                "deep_reasoner": {"backend": "claude", "model": "opus", "effort": "high"},
                "fast_worker": {"backend": "copilot", "model": model, "effort": effort},
                "arbiter": {"backend": "codex", "model": "gpt-detected", "effort": "xhigh"},
            },
            "review": {"spec_max_rounds": 1, "implementation_max_rounds": 3},
            "scope": "project",
            "exclude_choice": "track",
            "routing_action": "none",
            "write_agents": False,
            "smoke": False,
        }

    def test_opening_the_wizard_makes_no_copilot_subprocess_call(self):
        # There is no catalog to read, so a Codex-and-Claude user pays nothing
        # for Copilot support: no discovery probe, no cache, no refresh.
        state = handoff_setup_ui.build_state(self.repo, self.env)
        self.assertFalse(
            self.copilot_log.exists(),
            self.copilot_log.read_text(encoding="utf-8") if self.copilot_log.exists() else "",
        )
        self.assertNotIn("copilot", state["clis"])
        self.assertNotIn("copilot", state["model_options"])
        self.assertNotIn("copilot", state["model_discovery"])
        # and nothing catalog-shaped crept into the state
        self.assertEqual(
            {"claude", "codex", "copilot"}, set(state["efforts_by_backend"])
        )

    def test_controller_construction_makes_no_copilot_subprocess_call(self):
        handoff_setup_ui.SetupController(self.repo, self.env)
        self.assertFalse(self.copilot_log.exists())

    def test_the_page_types_the_copilot_model_and_labels_its_source(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("const TYPED_MODEL_BACKENDS = ['copilot'];", source)
        self.assertIn("'typed':'Typed; validated against the CLI',", source)
        self.assertIn("copilot:'GitHub Copilot'", source)
        # the disabling <select> is not reused for a backend with no options
        self.assertIn('type="text" spellcheck="false"', source)
        self.assertNotIn("state.clis.copilot", source)

    def test_preview_refuses_auto_with_the_engine_message_and_writes_nothing(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        result = controller.preview(self.copilot_payload(model="auto"))
        self.assertFalse(result["ok"], result)
        self.assertIn("'auto', which is refused on copilot", result["error"])
        self.assertFalse((self.repo / ".handoff" / "config.toml").exists())
        # the refusal is the engine's; the UI process ran no validation itself
        self.assertFalse(self.copilot_log.exists())

    def test_apply_is_blocked_without_a_matching_preview(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        with self.assertRaises(handoff_setup_ui.UIError):
            controller.apply(self.copilot_payload())

    def test_normalize_accepts_the_full_copilot_effort_enum(self):
        for effort in handoff_setup_ui.engine.COPILOT_EFFORTS:
            payload = handoff_setup_ui.normalize_payload(
                self.copilot_payload(effort=effort), repo=self.repo, env=self.env
            )
            self.assertEqual(effort, payload["identities"]["fast_worker"]["effort"])
        with self.assertRaises(handoff_setup_ui.UIError):
            handoff_setup_ui.normalize_payload(
                self.copilot_payload(effort="ultra"), repo=self.repo, env=self.env
            )


if __name__ == "__main__":
    unittest.main()
