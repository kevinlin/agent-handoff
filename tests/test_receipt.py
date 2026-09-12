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
        "cc_jobs": "1",
        "cc_job_durations": "none",
        "copilot_jobs": "1",
        "copilot_job_durations": "none",
        "scope": "project",
        "config_source": "project",
        "roles_used": "none",
        "receipt_schema_version": "6",
    }
    base.update(overrides)
    return base


class ValidateReceiptTests(unittest.TestCase):
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

    def test_non_integer_job_counts_fail(self):
        self.assert_one_failure("codex_jobs must be an integer", codex_jobs="two")
        self.assert_one_failure("cc_jobs must be an integer", cc_jobs="two")
        self.assert_one_failure("copilot_jobs must be an integer", copilot_jobs="two")

    def test_delegated_implementation_is_a_phase(self):
        self.assertEqual([], validate_receipt.validate(fields(phase="delegated implementation")))

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
        self.assert_one_failure("receipt_schema_version must be 6", receipt_schema_version="5")

    def test_a_v5_receipt_no_longer_validates(self):
        v5 = fields(receipt_schema_version="5")
        del v5["copilot_jobs"]
        del v5["copilot_job_durations"]
        failures = " ".join(validate_receipt.validate(v5))
        self.assertIn("missing field: copilot_jobs", failures)
        self.assertIn("missing field: copilot_job_durations", failures)

    def test_roles_used_accepts_a_copilot_host(self):
        self.assertEqual(
            [],
            validate_receipt.validate(
                fields(
                    roles_used='[{"role": "fast_worker", "host": "copilot", '
                    '"model": "mai-code-1.1-flash", "effort": "medium", "verified": true}]'
                )
            ),
        )

    def test_roles_used_rejects_an_unknown_host(self):
        self.assert_one_failure(
            "unknown host",
            roles_used='[{"role": "fast_worker", "host": "cursor", '
            '"model": "m", "effort": "medium", "verified": true}]',
        )

    def test_a_v4_receipt_no_longer_validates(self):
        v4 = fields(receipt_schema_version="4")
        del v4["cc_jobs"]
        del v4["cc_job_durations"]
        failures = " ".join(validate_receipt.validate(v4))
        self.assertIn("missing field: cc_jobs", failures)
        self.assertIn("missing field: cc_job_durations", failures)

    def test_malformed_duration_fails(self):
        for bad in ("74 min", "74min 5sec", "74min 99sec", "a while"):
            self.assert_one_failure("duration must look like", duration=bad)

    def test_job_durations_accept_measured_entries_and_reject_junk(self):
        for field in ("codex_job_durations", "cc_job_durations",
                      "copilot_job_durations"):
            with self.subTest(field=field):
                self.assertEqual(
                    [],
                    validate_receipt.validate(
                        fields(**{field: "job-a-t1=12min 04sec; job-a-t1-r2=running"})
                    ),
                )
                self.assert_one_failure(f"{field} must be", **{field: "job-a-t1"})
                self.assert_one_failure(f"{field} must be", **{field: "job-a-t1=soon"})

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
    "--checks", "unittest", "--codex-jobs", "0", "--cc-jobs", "0",
    "--copilot-jobs", "0",
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
        made = self.run_make(
            *RECEIPT_ARGS, "--started-at", stamp(datetime.now(timezone.utc)), "--no-save",
        )
        self.assertEqual(0, made.returncode, made.stderr)
        checked = subprocess.run(
            [sys.executable, str(SCRIPT), "-"],
            input=made.stdout, capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, checked.returncode, checked.stdout)

    def test_wrap_up_saves_the_receipt_without_being_asked(self):
        repo = self.temp_repo()
        ended = datetime.now(timezone.utc)
        made = self.run_make(
            "--repo", str(repo), *RECEIPT_ARGS,
            "--started-at", stamp(ended - timedelta(minutes=5)),
            "--ended-at", stamp(ended),
        )
        self.assertEqual(0, made.returncode, made.stderr)

        saved = sorted((repo / ".handoff" / "receipts").glob("receipt-*.md"))
        self.assertEqual(1, len(saved), saved)
        self.assertEqual(f"receipt-{ended.strftime('%Y%m%dT%H%M%SZ')}.md", saved[0].name)

        checked = subprocess.run(
            [sys.executable, str(SCRIPT), str(saved[0])],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, checked.returncode, checked.stdout)

    def test_a_multiline_field_is_refused_before_anything_is_written(self):
        repo = self.temp_repo()
        made = self.run_make(
            "--repo", str(repo),
            "--phase", "review", "--claude-session", "abc123",
            "--checks", "pytest\nnpm test",
            "--codex-jobs", "0", "--cc-jobs", "0", "--copilot-jobs", "0",
            "--scope", "project", "--config-source", "project", "--roles-used", "[]",
            "--started-at", stamp(datetime.now(timezone.utc)),
        )
        self.assertEqual(1, made.returncode)
        self.assertIn("checks contains a newline", made.stderr)
        self.assertNotIn("[Handoff session receipt]", made.stdout)
        self.assertFalse((repo / ".handoff" / "receipts").exists())

    def test_no_save_leaves_nothing_on_disk(self):
        repo = self.temp_repo()
        made = self.run_make(
            "--repo", str(repo), *RECEIPT_ARGS,
            "--started-at", stamp(datetime.now(timezone.utc)), "--no-save",
        )
        self.assertEqual(0, made.returncode, made.stderr)
        self.assertIn("[Handoff session receipt]", made.stdout)
        self.assertFalse((repo / ".handoff" / "receipts").exists())

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

    @staticmethod
    def write_job(repo: Path, name: str, submitted: datetime,
                  ended: datetime | None, backend: str | None = None) -> None:
        job = repo / ".handoff" / "jobs" / name
        job.mkdir(parents=True)
        backend_line = f"backend={backend}\n" if backend else ""
        (job / "meta").write_text(f"label=t\n{backend_line}submitted_at={stamp(submitted)}\n")
        if ended is not None:
            (job / "exit_code").write_text("0\n")
            os.utime(job / "exit_code", (ended.timestamp(), ended.timestamp()))

    def test_job_durations_are_measured_from_job_state(self):
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=1)

        self.write_job(repo, "job-earlier-run", started - timedelta(minutes=1), started)
        self.write_job(repo, "job-a-t1", started + timedelta(minutes=1),
                       started + timedelta(minutes=3, seconds=5))
        self.write_job(repo, "job-a-t1-r2", started + timedelta(minutes=2), None)

        emitted = self.make_receipt_fields(repo, "--started-at", stamp(started),
                                           "--codex-jobs", "2")
        self.assertEqual(
            "job-a-t1=2min 05sec; job-a-t1-r2=running",
            emitted["codex_job_durations"],
        )
        # A job dir with no backend= line predates backend dispatch: codex.
        self.assertEqual("none", emitted["cc_job_durations"])

    def test_mixed_backend_jobs_are_partitioned_by_their_meta(self):
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=1)

        self.write_job(repo, "job-cx", started + timedelta(minutes=1),
                       started + timedelta(minutes=2), backend="codex")
        self.write_job(repo, "job-cc", started + timedelta(minutes=3),
                       started + timedelta(minutes=5, seconds=30), backend="claude")
        self.write_job(repo, "job-cc-r2", started + timedelta(minutes=6), None,
                       backend="claude")

        self.write_job(repo, "job-cp", started + timedelta(minutes=7),
                       started + timedelta(minutes=8, seconds=15), backend="copilot")

        emitted = self.make_receipt_fields(repo, "--started-at", stamp(started),
                                           "--codex-jobs", "1", "--cc-jobs", "2",
                                           "--copilot-jobs", "1")
        self.assertEqual("job-cx=1min 00sec", emitted["codex_job_durations"])
        self.assertEqual("job-cc=2min 30sec; job-cc-r2=running", emitted["cc_job_durations"])
        self.assertEqual("job-cp=1min 15sec", emitted["copilot_job_durations"])

    def test_an_unknown_backend_line_buckets_as_codex(self):
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=1)
        self.write_job(repo, "job-x", started + timedelta(minutes=1),
                       started + timedelta(minutes=2), backend="cursor")
        emitted = self.make_receipt_fields(repo, "--started-at", stamp(started),
                                           "--codex-jobs", "1")
        self.assertEqual("job-x=1min 00sec", emitted["codex_job_durations"])
        self.assertEqual("none", emitted["copilot_job_durations"])

    def test_ended_at_pins_the_far_end_of_the_duration(self):
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=9)
        ended = started + timedelta(minutes=74)
        emitted = self.make_receipt_fields(
            repo, "--started-at", stamp(started), "--ended-at", stamp(ended))
        self.assertEqual("74min 00sec", emitted["duration"])

    def test_ended_at_in_the_future_refuses_to_emit(self):
        made = self.run_make(
            "--repo", str(self.temp_repo()), *RECEIPT_ARGS,
            "--started-at", stamp(datetime.now(timezone.utc) - timedelta(hours=1)),
            "--ended-at", stamp(datetime.now(timezone.utc) + timedelta(hours=1)),
        )
        self.assertEqual(1, made.returncode)
        self.assertNotIn("[Handoff session receipt]", made.stdout)
        self.assertIn("is in the future", made.stderr)

    def test_a_job_submitted_after_the_end_is_a_later_run(self):
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=2)
        ended = started + timedelta(minutes=30)
        self.write_job(repo, "job-inside", started + timedelta(minutes=1),
                       started + timedelta(minutes=4))
        self.write_job(repo, "job-later-run", ended + timedelta(minutes=1),
                       ended + timedelta(minutes=2))
        emitted = self.make_receipt_fields(
            repo, "--started-at", stamp(started), "--ended-at", stamp(ended),
            "--codex-jobs", "1")
        self.assertEqual("job-inside=3min 00sec", emitted["codex_job_durations"])

    def test_a_count_the_durations_do_not_support_refuses_to_emit(self):
        """The defect that produced receipt-20260910T022551Z: a session-start
        marker stamped after the run's first job silently drops that job from
        the durations while the hand-passed count still claims it."""

        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=1)
        self.write_job(repo, "job-before-the-marker", started - timedelta(minutes=20),
                       started - timedelta(minutes=14))
        self.write_job(repo, "job-after", started + timedelta(minutes=1),
                       started + timedelta(minutes=3))

        made = self.run_make("--repo", str(repo), *RECEIPT_ARGS,
                             "--started-at", stamp(started), "--codex-jobs", "2")
        self.assertEqual(1, made.returncode)
        self.assertNotIn("[Handoff session receipt]", made.stdout)
        self.assertIn("codex_jobs is 2 but codex_job_durations has 1", made.stderr)

    def test_widening_the_window_makes_that_receipt_emit(self):
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=1)
        self.write_job(repo, "job-first", started - timedelta(minutes=20),
                       started - timedelta(minutes=14))
        self.write_job(repo, "job-after", started + timedelta(minutes=1),
                       started + timedelta(minutes=3))

        emitted = self.make_receipt_fields(
            repo, "--started-at", stamp(started - timedelta(minutes=20)),
            "--codex-jobs", "2")
        self.assertEqual(
            "job-first=6min 00sec; job-after=2min 00sec",
            emitted["codex_job_durations"],
        )


if __name__ == "__main__":
    unittest.main()
