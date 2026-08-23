from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
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
        "duration": "74min 05sec",
        "checks": "python3 -m unittest discover -s tests",
        "anomalies": "none",
        "codex_jobs": "2",
        "codex_job_durations": "none",
        "scope": "project",
        "config_source": "project",
        "roles_used": "none",
        "receipt_schema_version": "4",
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
        self.assert_one_failure("receipt_schema_version must be 4", receipt_schema_version="3")

    def test_malformed_duration_fails(self):
        for bad in ("74 min", "74min 5sec", "74min 99sec", "a while"):
            self.assert_one_failure("duration must look like", duration=bad)

    def test_job_durations_accept_measured_entries_and_reject_junk(self):
        self.assertEqual(
            [],
            validate_receipt.validate(
                fields(codex_job_durations="job-a-t1=12min 04sec; job-a-t1-r2=running")
            ),
        )
        self.assert_one_failure("codex_job_durations must be", codex_job_durations="job-a-t1")
        self.assert_one_failure("codex_job_durations must be", codex_job_durations="job-a-t1=soon")

    def test_block_is_extracted_from_surrounding_markdown(self):
        text = "# notes\n\n[Handoff session receipt]\n" + "\n".join(
            f"{key}: {value}" for key, value in fields().items()
        ) + "\n\nmore prose\n"
        self.assertEqual([], validate_receipt.validate(validate_receipt.extract_block(text)))
        self.assertIsNone(validate_receipt.extract_block("no receipt here"))


def stamp(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


RECEIPT_ARGS = (
    "--phase", "review", "--claude-session", "abc123",
    "--checks", "unittest", "--codex-jobs", "1",
    "--scope", "project", "--config-source", "project", "--roles-used", "[]",
)


class MakeReceiptTests(unittest.TestCase):
    def temp_repo(self) -> Path:
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo)
        return Path(repo)

    def run_make(self, *arguments):
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make-receipt.py"), *arguments],
            capture_output=True,
            text=True,
            check=False,
        )

    def make_receipt_fields(self, repo: Path, *extra) -> dict:
        made = self.run_make("--repo", str(repo), *RECEIPT_ARGS, *extra)
        self.assertEqual(0, made.returncode, made.stderr)
        return validate_receipt.extract_block(made.stdout)

    def test_generated_receipt_validates(self):
        made = self.run_make(*RECEIPT_ARGS, "--started-at", stamp(datetime.now(timezone.utc)))
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
            "--started-at", stamp(datetime.now(timezone.utc)),
        )
        self.assertEqual(1, made.returncode)
        self.assertNotIn("[Handoff session receipt]", made.stdout)
        self.assertIn("FAIL", made.stderr)

    def test_start_marker_is_what_the_receipt_measures(self):
        repo = self.temp_repo()
        started = self.run_make("--start", "--repo", str(repo))
        self.assertEqual(0, started.returncode, started.stderr)
        marker = repo / ".handoff" / "session-start"
        self.assertTrue(marker.is_file())

        self.assertEqual("0min 00sec", self.make_receipt_fields(repo)["duration"])

        marker.write_text(stamp(datetime.now(timezone.utc) - timedelta(minutes=74)) + "\n")
        self.assertEqual("74min 00sec", self.make_receipt_fields(repo)["duration"])

    def test_missing_start_marker_refuses_to_emit_a_receipt(self):
        made = self.run_make("--repo", str(self.temp_repo()), *RECEIPT_ARGS)
        self.assertEqual(1, made.returncode)
        self.assertNotIn("[Handoff session receipt]", made.stdout)
        self.assertIn("no session start recorded", made.stderr)

    def test_job_durations_are_measured_from_job_state(self):
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=1)

        def write_job(name: str, submitted: datetime, ended: datetime | None) -> None:
            job = repo / ".handoff" / "jobs" / name
            job.mkdir(parents=True)
            (job / "meta").write_text(f"label=t\nsubmitted_at={stamp(submitted)}\n")
            if ended is not None:
                (job / "exit_code").write_text("0\n")
                os.utime(job / "exit_code", (ended.timestamp(), ended.timestamp()))

        write_job("job-earlier-run", started - timedelta(minutes=1), started)
        write_job("job-a-t1", started + timedelta(minutes=1),
                  started + timedelta(minutes=3, seconds=5))
        write_job("job-a-t1-r2", started + timedelta(minutes=2), None)

        emitted = self.make_receipt_fields(repo, "--started-at", stamp(started))
        self.assertEqual(
            "job-a-t1=2min 05sec; job-a-t1-r2=running",
            emitted["codex_job_durations"],
        )


if __name__ == "__main__":
    unittest.main()
