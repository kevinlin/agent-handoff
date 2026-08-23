from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-receipt.py"
SPEC = importlib.util.spec_from_file_location("validate_receipt", SCRIPT)
validate_receipt = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = validate_receipt
SPEC.loader.exec_module(validate_receipt)


def fields(**overrides) -> dict:
    base = {
        "phase": "review",
        "claude_session": "abc123",
        "checks": "python3 -m unittest discover -s tests",
        "anomalies": "none",
        "codex_jobs": "2",
        "scope": "project",
        "config_source": "project",
        "roles_used": "none",
        "receipt_schema_version": "3",
    }
    base.update(overrides)
    return base


class ValidateReceiptTests(unittest.TestCase):
    def assert_one_failure(self, needle: str, **overrides):
        failures = validate_receipt.validate(fields(**overrides))
        self.assertTrue(failures, f"expected a failure mentioning {needle!r}")
        self.assertIn(needle, " ".join(failures))

    def test_valid_receipt_passes(self):
        self.assertEqual([], validate_receipt.validate(fields()))
        self.assertEqual(
            [],
            validate_receipt.validate(
                fields(
                    roles_used='[{"role": "arbiter", "host": "codex", '
                    '"model": "gpt-x", "effort": "high", "verified": true}]'
                )
            ),
        )

    def test_unknown_phase_fails(self):
        self.assert_one_failure("phase must be one of", phase="vibes")

    def test_template_placeholder_fails(self):
        self.assert_one_failure("template placeholder", claude_session="<session id>")

    def test_non_integer_codex_jobs_fails(self):
        self.assert_one_failure("codex_jobs must be an integer", codex_jobs="two")

    def test_malformed_roles_used_fails(self):
        self.assert_one_failure("roles_used is invalid", roles_used='[{"role": "arbiter"}]')
        self.assert_one_failure("roles_used is invalid", roles_used='[{"role": "boss", '
                                '"host": "codex", "model": "m", "effort": "high", "verified": true}]')

    def test_missing_and_unknown_fields_fail(self):
        missing = fields()
        del missing["checks"]
        self.assertIn("missing field: checks", " ".join(validate_receipt.validate(missing)))
        self.assert_one_failure("unknown fields: direction", direction="claude")

    def test_old_schema_version_fails(self):
        self.assert_one_failure("receipt_schema_version must be 3", receipt_schema_version="2")

    def test_block_is_extracted_from_surrounding_markdown(self):
        text = "# notes\n\n[Handoff session receipt]\n" + "\n".join(
            f"{key}: {value}" for key, value in fields().items()
        ) + "\n\nmore prose\n"
        self.assertEqual([], validate_receipt.validate(validate_receipt.extract_block(text)))
        self.assertIsNone(validate_receipt.extract_block("no receipt here"))


class MakeReceiptTests(unittest.TestCase):
    def run_make(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make-receipt.py"), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_generated_receipt_validates(self):
        made = self.run_make(
            "--phase", "review", "--claude-session", "abc123",
            "--checks", "unittest", "--codex-jobs", "1",
            "--scope", "project", "--config-source", "project", "--roles-used", "[]",
        )
        self.assertEqual(0, made.returncode, made.stderr)
        checked = subprocess.run(
            [sys.executable, str(SCRIPT), "-"],
            input=made.stdout, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, checked.returncode, checked.stdout)

    def test_invalid_arguments_refuse_to_emit_a_receipt(self):
        made = self.run_make(
            "--phase", "vibes", "--claude-session", "abc123",
            "--checks", "unittest", "--codex-jobs", "1",
        )
        self.assertEqual(1, made.returncode)
        self.assertNotIn("[Handoff session receipt]", made.stdout)
        self.assertIn("FAIL", made.stderr)


if __name__ == "__main__":
    unittest.main()
