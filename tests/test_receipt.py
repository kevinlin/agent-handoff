from __future__ import annotations

import importlib.util
import json
import os
import re
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
        "cursor_jobs": "1",
        "cursor_job_durations": "none",
        "antigravity_jobs": "1",
        "antigravity_job_durations": "none",
        "scope": "project",
        "config_source": "project",
        "roles_used": "none",
        "receipt_schema_version": "8",
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

    def test_a_uuid_and_none_are_valid_session_ids(self):
        for session in ("7ee39fd0-93b5-4118-827b-6b6e3ca081ba", "none", "abc123", "A.b_c-1"):
            with self.subTest(session=session):
                self.assertEqual([], validate_receipt.validate(fields(claude_session=session)))

    def test_an_unsafe_session_id_fails_naming_the_value(self):
        # The id names a folder, so a value that could leave .handoff/sessions/ fails here.
        for session in ("../x", "a/b", ".", "..", "-x", ".hidden", "a b", "a\\b"):
            with self.subTest(session=session):
                failures = validate_receipt.validate(fields(claude_session=session))
                self.assertEqual(1, len(failures), failures)
                self.assertIn("claude_session", failures[0])
                self.assertIn(repr(session), failures[0])

    def test_a_placeholder_session_is_reported_once_as_a_placeholder(self):
        failures = validate_receipt.validate(fields(claude_session="<session id>"))
        self.assertEqual(1, len(failures), failures)
        self.assertIn("template placeholder", failures[0])

    def test_the_session_grammar_is_the_runtime_helper_s_grammar(self):
        sys.path.insert(0, str(ROOT / "scripts"))
        self.addCleanup(sys.path.remove, str(ROOT / "scripts"))
        import handoff_runtime
        self.assertIs(handoff_runtime.SESSION_ID, validate_receipt.SESSION_ID)
        self.assertEqual(r"[A-Za-z0-9][A-Za-z0-9._-]*", handoff_runtime.SESSION_ID.pattern)

    def test_the_schema_pattern_accepts_exactly_what_the_validator_accepts(self):
        schema = json.loads((ROOT / "assets" / "receipt-schema.json").read_text(encoding="utf-8"))
        session = schema["properties"]["claude_session"]
        self.assertEqual("^(none|[A-Za-z0-9][A-Za-z0-9._-]*)$", session["pattern"])
        self.assertIn("session folder", session["description"])
        # The grammar narrows under the same schema version: every stored receipt conforms.
        self.assertEqual(8, schema["properties"]["receipt_schema_version"]["const"])
        for candidate in ("none", "abc123", "7ee39fd0-93b5-4118-827b-6b6e3ca081ba",
                          "../x", "a/b", ".", "..", "-x", ".hidden", "a b"):
            with self.subTest(candidate=candidate):
                by_schema = re.search(session["pattern"], candidate) is not None
                by_validator = validate_receipt.validate(fields(claude_session=candidate)) == []
                self.assertEqual(by_schema, by_validator)

    def test_non_integer_job_counts_fail(self):
        self.assert_one_failure("codex_jobs must be an integer", codex_jobs="two")
        self.assert_one_failure("cc_jobs must be an integer", cc_jobs="two")
        self.assert_one_failure("copilot_jobs must be an integer", copilot_jobs="two")
        self.assert_one_failure("cursor_jobs must be an integer", cursor_jobs="two")
        self.assert_one_failure("antigravity_jobs must be an integer", antigravity_jobs="two")

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
        self.assert_one_failure("receipt_schema_version must be 8", receipt_schema_version="6")
        self.assert_one_failure("receipt_schema_version must be 8", receipt_schema_version="7")

    def test_a_v7_receipt_no_longer_validates(self):
        v7 = fields(receipt_schema_version="7")
        del v7["antigravity_jobs"]
        del v7["antigravity_job_durations"]
        failures = " ".join(validate_receipt.validate(v7))
        self.assertIn("missing field: antigravity_jobs", failures)
        self.assertIn("missing field: antigravity_job_durations", failures)

    def test_a_v6_receipt_no_longer_validates(self):
        v6 = fields(receipt_schema_version="6")
        del v6["cursor_jobs"]
        del v6["cursor_job_durations"]
        failures = " ".join(validate_receipt.validate(v6))
        self.assertIn("missing field: cursor_jobs", failures)
        self.assertIn("missing field: cursor_job_durations", failures)

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

    def test_roles_used_accepts_a_cursor_host(self):
        self.assertEqual([], validate_receipt.validate(fields(
            roles_used='[{"role": "fast_worker", "host": "cursor", '
            '"model": "claude-opus-5-5-high", "effort": "model", "verified": true}]')))

    def test_an_antigravity_host_is_accepted_in_roles_used(self):
        # The host is the CLI that executed the role, here agy.
        self.assertEqual([], validate_receipt.validate(fields(
            roles_used='[{"role": "fast_worker", "host": "antigravity", '
            '"model": "gemini-3.8-flash-low", "effort": "model", "verified": true}]')))

    def test_roles_used_rejects_an_unknown_host(self):
        self.assert_one_failure(
            "unknown host",
            roles_used='[{"role": "fast_worker", "host": "gemini", '
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
                      "copilot_job_durations", "cursor_job_durations",
                      "antigravity_job_durations"):
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
    "--copilot-jobs", "0", "--cursor-jobs", "0",
    "--scope", "project", "--config-source", "project", "--roles-used", "[]",
)
# The same receipt with the session left to the generator.
NO_SESSION_ARGS = (
    "--phase", "review",
    "--checks", "unittest", "--codex-jobs", "0", "--cc-jobs", "0",
    "--copilot-jobs", "0", "--cursor-jobs", "0",
    "--scope", "project", "--config-source", "project", "--roles-used", "[]",
)
SESSION_ENV = "CLAUDE_CODE_SESSION_ID"


class MakeReceiptTests(unittest.TestCase):
    def temp_repo(self) -> Path:
        repo = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, repo)
        return Path(repo)

    def run_make(self, *arguments, session_env: str | None = None):
        """Inside Claude Code the variable is set and in CI it is not, so every
        run states it: removed unless `session_env` sets it (possibly to "")."""
        env = {key: value for key, value in os.environ.items() if key != SESSION_ENV}
        if session_env is not None:
            env[SESSION_ENV] = session_env
        return subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make-receipt.py"), *arguments],
            capture_output=True,
            text=True,
            check=False,
            env=env,
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

        saved = sorted((repo / ".handoff" / "sessions" / f"{ended:%Y%m%d}_abc123").glob("receipt-*.md"))
        self.assertEqual(1, len(saved), saved)
        self.assertEqual(f"receipt-{ended.strftime('%Y%m%dT%H%M%SZ')}.md", saved[0].name)

        checked = subprocess.run(
            [sys.executable, str(SCRIPT), str(saved[0])],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, checked.returncode, checked.stdout)

    def make_with_session(self, repo: Path, *flag, session_env: str | None = None, save: bool = False):
        """Run the generator with the session left to `flag` and the environment."""
        extra = () if save else ("--no-save",)
        return self.run_make(
            "--repo", str(repo), *NO_SESSION_ARGS, *flag,
            "--started-at", stamp(datetime.now(timezone.utc)), *extra,
            session_env=session_env,
        )

    def recorded_session(self, made) -> str:
        self.assertEqual(0, made.returncode, made.stderr)
        return validate_receipt.extract_block(made.stdout)["claude_session"]

    def test_an_omitted_flag_records_the_env_session(self):
        made = self.make_with_session(self.temp_repo(), session_env="env-session-1")
        self.assertEqual("env-session-1", self.recorded_session(made))

    def test_an_omitted_flag_without_the_env_value_is_refused(self):
        for session_env in (None, ""):
            with self.subTest(session_env=session_env):
                repo = self.temp_repo()
                made = self.make_with_session(repo, session_env=session_env, save=True)
                self.assertEqual(1, made.returncode)
                self.assertEqual("", made.stdout)
                self.assertIn(
                    "FAIL no Claude Code session id: CLAUDE_CODE_SESSION_ID is not set in this "
                    "shell. Pass --claude-session <id>, or --claude-session none for a run no "
                    "Claude Code session drove.", made.stderr)
                self.assertFalse((repo / ".handoff" / "sessions").exists())

    def test_none_inside_a_claude_code_session_is_refused(self):
        repo = self.temp_repo()
        made = self.make_with_session(repo, "--claude-session", "none",
                                      session_env="env-session-1", save=True)
        self.assertEqual(1, made.returncode)
        self.assertEqual("", made.stdout)
        self.assertIn(
            "FAIL --claude-session none, but this shell runs inside Claude Code session "
            "env-session-1. Omit --claude-session to record it. To regenerate a receipt for a "
            "run no Claude Code session drove, unset CLAUDE_CODE_SESSION_ID for that one "
            "command.", made.stderr)
        self.assertFalse((repo / ".handoff" / "sessions").exists())

    def test_none_outside_a_claude_code_session_records_none(self):
        for session_env in (None, ""):
            with self.subTest(session_env=session_env):
                repo = self.temp_repo()
                made = self.make_with_session(repo, "--claude-session", "none",
                                              session_env=session_env, save=True)
                self.assertEqual("none", self.recorded_session(made))
                self.assertEqual(1, len(list((repo / ".handoff" / "sessions" / "none")
                                             .glob("receipt-*.md"))))

    def test_an_explicit_id_is_recorded_whatever_the_env_says(self):
        for session_env in (None, "", "env-session-1"):
            with self.subTest(session_env=session_env):
                made = self.make_with_session(self.temp_repo(), "--claude-session", "past-run-7",
                                              session_env=session_env)
                self.assertEqual("past-run-7", self.recorded_session(made))

    def test_the_save_lands_in_the_session_folder_named_for_the_stamp(self):
        repo = self.temp_repo()
        ended = datetime.now(timezone.utc)
        made = self.run_make(
            "--repo", str(repo), *NO_SESSION_ARGS,
            "--started-at", stamp(ended - timedelta(minutes=5)), "--ended-at", stamp(ended),
            session_env="env-session-1",
        )
        self.assertEqual(0, made.returncode, made.stderr)
        expected = (repo / ".handoff" / "sessions" / f"{ended:%Y%m%d}_env-session-1"
                    / f"receipt-{ended.strftime('%Y%m%dT%H%M%SZ')}.md")
        self.assertTrue(expected.is_file())
        self.assertEqual(made.stdout.rstrip("\n"), expected.read_text(encoding="utf-8").rstrip("\n"))
        self.assertIn(f"saved: {expected.resolve()}", made.stderr)
        self.assertEqual([], list((repo / ".handoff").glob("receipts*")))

    def run_ended(self, repo: Path, ended: datetime, *session_args, session_env: str | None = None):
        """A save whose receipt ends at `ended`, five minutes after it started."""
        return self.run_make(
            "--repo", str(repo), *NO_SESSION_ARGS, *session_args,
            "--started-at", stamp(ended - timedelta(minutes=5)), "--ended-at", stamp(ended),
            session_env=session_env)

    def test_a_new_session_folder_takes_the_date_the_receipt_ended_on(self):
        repo = self.temp_repo()
        ended = datetime.now(timezone.utc) - timedelta(days=3)
        made = self.run_ended(repo, ended, session_env="env-session-1")
        self.assertEqual(0, made.returncode, made.stderr)
        sessions = repo / ".handoff" / "sessions"
        self.assertEqual([f"{ended:%Y%m%d}_env-session-1"], sorted(p.name for p in sessions.iterdir()))
        self.assertEqual([f"receipt-{ended:%Y%m%dT%H%M%SZ}.md"],
                         sorted(p.name for p in (sessions / f"{ended:%Y%m%d}_env-session-1").iterdir()))

    def test_a_later_run_on_a_later_date_lands_in_the_first_runs_folder(self):
        repo = self.temp_repo()
        first = datetime.now(timezone.utc) - timedelta(days=3)
        second = first + timedelta(days=1)
        for ended in (first, second):
            made = self.run_ended(repo, ended, "--claude-session", "abc123")
            self.assertEqual(0, made.returncode, made.stderr)
        sessions = repo / ".handoff" / "sessions"
        # One session, one folder: the date it was created with, never the second run's.
        self.assertEqual([f"{first:%Y%m%d}_abc123"], sorted(p.name for p in sessions.iterdir()))
        self.assertEqual([f"receipt-{first:%Y%m%dT%H%M%SZ}.md", f"receipt-{second:%Y%m%dT%H%M%SZ}.md"],
                         sorted(p.name for p in (sessions / f"{first:%Y%m%d}_abc123").iterdir()))
        self.assertIn(f"saved: {(sessions / f'{first:%Y%m%d}_abc123').resolve()}", made.stderr)

    def test_a_none_receipt_stays_in_the_one_bare_folder_whatever_its_date(self):
        repo = self.temp_repo()
        first = datetime.now(timezone.utc) - timedelta(days=3)
        for ended in (first, first + timedelta(days=1)):
            made = self.run_ended(repo, ended, "--claude-session", "none")
            self.assertEqual(0, made.returncode, made.stderr)
        sessions = repo / ".handoff" / "sessions"
        self.assertEqual(["none"], sorted(p.name for p in sessions.iterdir()))
        self.assertEqual(2, len(list((sessions / "none").glob("receipt-*.md"))))

    def test_an_unsafe_id_is_refused_before_anything_is_printed_or_written(self):
        for unsafe in ("../x", "a/b", ".", "-x", " abc"):
            for extra in ((), ("--no-save",)):
                with self.subTest(unsafe=unsafe, extra=extra):
                    repo = self.temp_repo()
                    made = self.run_make(
                        "--repo", str(repo), *NO_SESSION_ARGS, f"--claude-session={unsafe}",
                        "--started-at", stamp(datetime.now(timezone.utc)), *extra,
                    )
                    self.assertEqual(1, made.returncode, made.stderr)
                    self.assertEqual("", made.stdout)
                    self.assertIn("FAIL", made.stderr)
                    self.assertFalse((repo / ".handoff" / "sessions").exists())
                    self.assertFalse((repo / ".handoff" / "x").exists())
                    self.assertFalse((repo / "x").exists())

    def test_a_symlinked_component_is_refused_before_anything_is_printed(self):
        for kind in ("sessions", "session-folder", "file"):
            with self.subTest(kind=kind):
                repo = self.temp_repo()
                outside = repo / "outside"
                outside.mkdir()
                ended = datetime.now(timezone.utc)
                sessions = repo / ".handoff" / "sessions"
                sessions.mkdir(parents=True)
                if kind == "sessions":
                    sessions.rmdir()
                    sessions.symlink_to(outside, target_is_directory=True)
                elif kind == "session-folder":
                    (sessions / f"{ended:%Y%m%d}_abc123").symlink_to(outside, target_is_directory=True)
                else:
                    (sessions / f"{ended:%Y%m%d}_abc123").mkdir()
                    (sessions / f"{ended:%Y%m%d}_abc123"
                     / f"receipt-{ended.strftime('%Y%m%dT%H%M%SZ')}.md"
                     ).symlink_to(outside / "elsewhere.md")
                made = self.run_make(
                    "--repo", str(repo), *RECEIPT_ARGS,
                    "--started-at", stamp(ended - timedelta(minutes=5)), "--ended-at", stamp(ended),
                )
                self.assertEqual(1, made.returncode, made.stderr)
                self.assertEqual("", made.stdout)
                self.assertIn("FAIL", made.stderr)
                self.assertEqual([], list(outside.iterdir()))

    def test_a_legacy_folder_adds_a_migration_note_after_a_save(self):
        repo = self.temp_repo()
        (repo / ".handoff" / "receipts").mkdir(parents=True)
        made = self.make_with_session(repo, "--claude-session", "abc123", save=True)
        self.assertEqual(0, made.returncode, made.stderr)
        notes = [line for line in made.stderr.splitlines() if line.startswith("note: ")]
        self.assertEqual(1, len(notes), made.stderr)
        self.assertIn(".handoff/receipts", notes[0])
        self.assertIn("migrate-sessions.py --repo", notes[0])
        self.assertLess(made.stderr.index("saved: "), made.stderr.index("note: "))

    def test_no_note_without_a_legacy_folder_or_without_a_save(self):
        repo = self.temp_repo()
        made = self.make_with_session(repo, "--claude-session", "abc123", save=True)
        self.assertNotIn("note:", made.stderr)
        legacy = self.temp_repo()
        (legacy / ".handoff" / "transcripts").mkdir(parents=True)
        made = self.make_with_session(legacy, "--claude-session", "abc123")
        self.assertNotIn("note:", made.stderr)

    def test_start_needs_no_session_id(self):
        repo = self.temp_repo()
        self.assertEqual(0, self.run_make("--start", "--repo", str(repo)).returncode)
        self.assertEqual(0, self.run_make("--start", "--repo", str(repo),
                                          session_env="env-session-1").returncode)

    def test_phase_and_checks_stay_required_without_start(self):
        made = self.run_make("--repo", str(self.temp_repo()), "--claude-session", "abc123")
        self.assertEqual(2, made.returncode)
        self.assertIn("--phase and --checks are required unless --start", made.stderr)

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
        self.assertFalse((repo / ".handoff" / "sessions").exists())

    def test_no_save_leaves_nothing_on_disk(self):
        repo = self.temp_repo()
        made = self.run_make(
            "--repo", str(repo), *RECEIPT_ARGS,
            "--started-at", stamp(datetime.now(timezone.utc)), "--no-save",
        )
        self.assertEqual(0, made.returncode, made.stderr)
        self.assertIn("[Handoff session receipt]", made.stdout)
        self.assertFalse((repo / ".handoff" / "sessions").exists())

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

        # The subprocess reads the clock after the marker is stamped; allow a slow runner a few seconds.
        self.assertRegex(self.make_receipt_fields(repo)["duration"], r"^0min 0[0-4]sec$")

        marker.write_text(stamp(datetime.now(timezone.utc) - timedelta(minutes=74)) + "\n")
        self.assertRegex(self.make_receipt_fields(repo)["duration"], r"^74min 0[0-4]sec$")

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
        self.write_job(repo, "job-cu", started + timedelta(minutes=9),
                       started + timedelta(minutes=13), backend="cursor")

        emitted = self.make_receipt_fields(repo, "--started-at", stamp(started),
                                           "--codex-jobs", "1", "--cc-jobs", "2",
                                           "--copilot-jobs", "1", "--cursor-jobs", "1")
        self.assertEqual("job-cx=1min 00sec", emitted["codex_job_durations"])
        self.assertEqual("job-cc=2min 30sec; job-cc-r2=running", emitted["cc_job_durations"])
        self.assertEqual("job-cp=1min 15sec", emitted["copilot_job_durations"])
        self.assertEqual("job-cu=4min 00sec", emitted["cursor_job_durations"])

    def test_jobs_split_five_ways(self):
        """R7.2: one job directory per backend gives five correct counts."""
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=1)
        for offset, backend in enumerate(("codex", "claude", "copilot", "cursor", "antigravity")):
            self.write_job(repo, f"job-{backend}", started + timedelta(minutes=1 + 2 * offset),
                           started + timedelta(minutes=2 + 2 * offset), backend=backend)

        counts = ("--codex-jobs", "1", "--cc-jobs", "1", "--copilot-jobs", "1",
                  "--cursor-jobs", "1", "--antigravity-jobs", "1")
        emitted = self.make_receipt_fields(repo, "--started-at", stamp(started), *counts)
        for field, backend in (("codex", "codex"), ("cc", "claude"), ("copilot", "copilot"),
                               ("cursor", "cursor"), ("antigravity", "antigravity")):
            self.assertEqual("1", emitted[f"{field}_jobs"], field)
            self.assertEqual(f"job-{backend}=1min 00sec", emitted[f"{field}_job_durations"], field)
        self.assertEqual("8", emitted["receipt_schema_version"])
        checked = subprocess.run(
            [sys.executable, str(SCRIPT), "-"],
            input="[Handoff session receipt]\n" + "\n".join(f"{k}: {v}" for k, v in emitted.items()),
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(0, checked.returncode, checked.stdout)

        # An antigravity job is never folded into the codex count: declaring it
        # there, as an engine older than v8 would have counted it, is refused.
        made = self.run_make("--repo", str(repo), *RECEIPT_ARGS, "--started-at", stamp(started),
                             "--codex-jobs", "2", "--cc-jobs", "1", "--copilot-jobs", "1",
                             "--cursor-jobs", "1", "--antigravity-jobs", "0")
        self.assertEqual(1, made.returncode)
        self.assertIn("antigravity_jobs is 0 but antigravity_job_durations has 1", made.stderr)

    def test_an_unknown_backend_line_buckets_as_codex(self):
        repo = self.temp_repo()
        started = datetime.now(timezone.utc) - timedelta(hours=1)
        self.write_job(repo, "job-x", started + timedelta(minutes=1),
                       started + timedelta(minutes=2), backend="gemini")
        emitted = self.make_receipt_fields(repo, "--started-at", stamp(started),
                                           "--codex-jobs", "1")
        self.assertEqual("job-x=1min 00sec", emitted["codex_job_durations"])
        self.assertEqual("none", emitted["copilot_job_durations"])
        self.assertEqual("none", emitted["cursor_job_durations"])
        self.assertEqual("none", emitted["antigravity_job_durations"])

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
