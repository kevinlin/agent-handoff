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
    def failures(self, *args, **kwargs):
        return "\n".join(validate_verdict.validate(*args, **kwargs))

    def test_valid_pass_has_no_failures(self):
        self.assertEqual([], validate_verdict.validate(verdict(), None))

    def test_missing_field_fails(self):
        broken = verdict()
        del broken["tested_commit"]
        self.assertIn("missing field: tested_commit", self.failures(broken, None))

    def test_unknown_result_fails(self):
        self.assertIn("result must be one of", self.failures(verdict(result="GREEN"), None))

    def test_pass_with_nonzero_exit_fails(self):
        self.assertIn("PASS requires exit_code 0", self.failures(verdict(exit_code=1), None))

    def test_pass_with_unrun_scenarios_fails(self):
        self.assertIn(
            "PASS requires every scenario to pass",
            self.failures(
                verdict(
                    scenarios={
                        "total": 3,
                        "passed": 2,
                        "ids": ["CHK-01", "CHK-02", "CHK-03"],
                    }
                ),
                None,
            ),
        )

    def test_pass_with_findings_fails(self):
        self.assertIn(
            "PASS requires an empty findings list",
            self.failures(verdict(findings=["checkout total is wrong"]), None),
        )

    def test_id_count_must_match_total(self):
        self.assertIn(
            "scenarios.total must equal the number of ids",
            self.failures(verdict(scenarios={"total": 3, "passed": 3, "ids": ["CHK-01"]}), None),
        )

    def test_fail_requires_a_finding(self):
        self.assertIn(
            "FAIL requires at least one finding",
            self.failures(
                verdict(
                    result="FAIL",
                    exit_code=1,
                    scenarios={"total": 2, "passed": 1, "ids": ["CHK-01", "CHK-02"]},
                ),
                None,
            ),
        )

    def test_fail_without_evidence_of_failure_fails(self):
        self.assertIn(
            "FAIL requires a non-zero exit_code",
            self.failures(verdict(result="FAIL", findings=["broken"], exit_code=0), None),
        )

    def test_blocked_requires_zero_passed_and_a_reason(self):
        ok = verdict(
            result="BLOCKED",
            command=None,
            exit_code=None,
            scenarios={"total": 0, "passed": 0, "ids": []},
            findings=["dev server would not start: port 5173 in use"],
        )
        self.assertEqual([], validate_verdict.validate(ok, None))
        self.assertIn(
            "BLOCKED requires scenarios.passed to be 0",
            self.failures(
                verdict(
                    result="BLOCKED",
                    scenarios={"total": 2, "passed": 2, "ids": ["CHK-01", "CHK-02"]},
                ),
                None,
            ),
        )

    def test_hash_mismatch_is_rejected(self):
        self.assertIn(
            "scenarios_sha256 does not match the reviewed scenarios",
            self.failures(verdict(), "d" * 64),
        )

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

    def test_cli_accepts_a_valid_verdict_on_stdin(self):
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "-", "--expect-scenarios-sha256", "c" * 64],
            input=json.dumps(verdict()),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("OK: PASS", result.stdout)


if __name__ == "__main__":
    unittest.main()
