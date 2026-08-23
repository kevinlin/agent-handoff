from __future__ import annotations

import http.client
import importlib.util
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
            "{'reasoningEffort': 'high'}, {'reasoningEffort': 'ultra'}]},\n"
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
            "identities": identities,
            "scope": "project",
            "exclude_choice": "track",
            "routing_action": "none",
            "write_agents": False,
            "smoke": False,
        }

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
                "codex": ["minimal", "low", "medium", "high", "xhigh"],
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


if __name__ == "__main__":
    unittest.main()


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
