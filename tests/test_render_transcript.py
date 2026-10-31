from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from datetime import datetime, timezone
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render-transcript.py"
SPEC = importlib.util.spec_from_file_location("render_transcript", SCRIPT)
render_transcript = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = render_transcript
SPEC.loader.exec_module(render_transcript)

rt = render_transcript
# render-transcript.py puts scripts/ on sys.path, which is what makes this importable.
hr = importlib.import_module("handoff_runtime")

SESSION_ENV = "CLAUDE_CODE_SESSION_ID"


@contextlib.contextmanager
def clock(day: str):
    """session_path's clock, pinned to noon UTC on `day` (YYYYMMDD)."""
    moment = datetime.strptime(day, "%Y%m%d").replace(hour=12, tzinfo=timezone.utc)
    with unittest.mock.patch.object(hr, "datetime") as fake:
        fake.now.return_value = moment
        yield


def folders_of(sessions: Path, session: str) -> list:
    """The folder names under `sessions` that belong to `session`."""
    if not sessions.is_dir():
        return []
    return sorted(p.name for p in sessions.iterdir() if hr.folder_session(p.name) == session)


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.jobs = self.repo / ".handoff" / "jobs"
        self.jobs.mkdir(parents=True)
        # Inside Claude Code the variable is set and in CI it is not; the
        # suite must give one answer, so it is removed unless a test sets it.
        patcher = unittest.mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(SESSION_ENV, None)

    def make_job(self, name, *, meta=None, log="", exit_code=None,
                 pid=None, cancelled=False, prompt=None):
        job = self.jobs / name
        job.mkdir()
        (job / "log.jsonl").write_text(log, encoding="utf-8")
        if prompt is not None:
            (job / "prompt.md").write_text(prompt, encoding="utf-8")
        lines = {"label": name.split("-")[-1], "submitted_at": "2026-09-08T10:00:00Z"}
        lines.update(meta or {})
        (job / "meta").write_text(
            "".join(f"{k}={v}\n" for k, v in lines.items()), encoding="utf-8")
        if exit_code is not None:
            (job / "exit_code").write_text(f"{exit_code}\n", encoding="utf-8")
        if pid is not None:
            (job / "pid").write_text(f"{pid}\n", encoding="utf-8")
        if cancelled:
            (job / "cancelled").touch()
        return job


class MetaTests(Base):
    def test_missing_meta_is_empty_dict(self):
        job = self.jobs / "bare"
        job.mkdir()
        self.assertEqual(rt.read_meta(job), {})

    def test_resumed_job_has_no_backend_or_role(self):
        job = self.make_job("job-a-resume", meta={
            "label": "resume", "model": "inherit", "mode": "resume",
            "parent": "job-parent"})
        meta = rt.read_meta(job)
        self.assertEqual(meta["model"], "inherit")
        self.assertNotIn("backend", meta)
        self.assertNotIn("role", meta)
        self.assertEqual(meta["parent"], "job-parent")


class StateTests(Base):
    def test_cancelled_marker_wins_over_exit_code(self):
        job = self.make_job("job-c", exit_code=0, cancelled=True)
        self.assertEqual(rt.job_state(job), "CANCELLED")

    def test_exit_zero_is_done(self):
        self.assertEqual(rt.job_state(self.make_job("job-d", exit_code=0)), "DONE")

    def test_nonzero_exit_is_failed(self):
        self.assertEqual(rt.job_state(self.make_job("job-e", exit_code=1)), "FAILED")

    def test_live_pid_without_exit_code_is_running(self):
        job = self.make_job("job-f", pid=os.getpid())
        self.assertEqual(rt.job_state(job), "RUNNING")

    def test_dead_pid_without_exit_code_is_failed(self):
        # A worker that died without writing exit_code is FAILED, never RUNNING.
        job = self.make_job("job-g", pid=999999)
        self.assertEqual(rt.job_state(job), "FAILED")


class ResolveTests(Base):
    def test_exact_directory_name(self):
        self.make_job("job-2026-09-08T10-00-00-1-alpha")
        got = rt.resolve(self.repo, "job-2026-09-08T10-00-00-1-alpha")
        self.assertEqual(got.name, "job-2026-09-08T10-00-00-1-alpha")

    def test_path_argument_resolves_directly(self):
        job = self.make_job("job-x-beta")
        self.assertEqual(rt.resolve(self.repo, str(job)), job)

    def test_omitted_argument_picks_newest_by_submitted_at(self):
        self.make_job("job-old", meta={"submitted_at": "2026-09-08T09:00:00Z"})
        self.make_job("job-new", meta={"submitted_at": "2026-09-08T11:00:00Z"})
        self.assertEqual(rt.resolve(self.repo, None).name, "job-new")

    def test_reserved_last_beats_a_job_labelled_last(self):
        # A job whose name ends in "last" must not intercept the selector.
        self.make_job("job-a-last", meta={"submitted_at": "2026-09-08T09:00:00Z"})
        self.make_job("job-b-newest", meta={"submitted_at": "2026-09-08T12:00:00Z"})
        self.assertEqual(rt.resolve(self.repo, "last").name, "job-b-newest")

    def test_job_labelled_last_reachable_by_full_name(self):
        self.make_job("job-a-last", meta={"submitted_at": "2026-09-08T09:00:00Z"})
        self.make_job("job-b-newest", meta={"submitted_at": "2026-09-08T12:00:00Z"})
        self.assertEqual(rt.resolve(self.repo, "job-a-last").name, "job-a-last")

    def test_pid_lookup(self):
        self.make_job("job-h-gamma", pid=4242)
        self.assertEqual(rt.resolve(self.repo, "4242").name, "job-h-gamma")

    def test_pid_and_label_collision_is_ambiguous(self):
        self.make_job("job-i-4242", pid=1)
        self.make_job("job-j-delta", pid=4242)
        with self.assertRaises(rt.Ambiguous):
            rt.resolve(self.repo, "4242")

    def test_suffix_match_returns_every_round(self):
        # The -r2 resume must not be hidden by matching only the original.
        self.make_job("job-k-capture")
        self.make_job("job-k-capture-r2")
        with self.assertRaises(rt.Ambiguous) as caught:
            rt.resolve(self.repo, "capture")
        names = sorted(p.name for p in caught.exception.candidates)
        self.assertEqual(names, ["job-k-capture", "job-k-capture-r2"])

    def test_unique_suffix_match_resolves(self):
        self.make_job("job-l-solo")
        self.assertEqual(rt.resolve(self.repo, "solo").name, "job-l-solo")

    def test_no_match_raises_not_found(self):
        self.make_job("job-m-one")
        with self.assertRaises(rt.NotFound):
            rt.resolve(self.repo, "nothing-like-this")

    def test_no_jobs_at_all_raises_not_found(self):
        with self.assertRaises(rt.NotFound):
            rt.resolve(self.repo, None)


class PayloadTests(Base):
    def test_payload_carries_state_and_meta(self):
        job = self.make_job("job-p-one", log='{"type":"turn.started"}\n', exit_code=0,
                            meta={"backend": "codex", "model": "gpt-6-astra"})
        payload = rt.build_payload(job)
        self.assertEqual(payload["job_id"], "job-p-one")
        self.assertEqual(payload["state"], "DONE")
        self.assertEqual(payload["exit_code"], "0")
        self.assertEqual(payload["meta"]["backend"], "codex")
        self.assertIn("turn.started", payload["log_text"])
        self.assertIsNotNone(payload["exit_code_mtime"])
        self.assertIsNotNone(payload["generated_at"])

    def test_running_job_has_no_exit_code_mtime(self):
        job = self.make_job("job-p-two", pid=os.getpid())
        payload = rt.build_payload(job)
        self.assertEqual(payload["state"], "RUNNING")
        self.assertIsNone(payload["exit_code_mtime"])

    def test_parent_job_id_recorded_for_resumes(self):
        job = self.make_job("job-p-three", meta={"parent": "job-parent", "mode": "resume"})
        self.assertEqual(rt.build_payload(job)["parent_job_id"], "job-parent")

    def test_prompt_md_becomes_the_first_user_message(self):
        job = self.make_job("job-p-five", prompt="# Task\nShip it.\n")
        self.assertEqual(rt.build_payload(job)["prompt_text"], "# Task\nShip it.\n")

    def test_missing_prompt_is_empty_string(self):
        job = self.make_job("job-p-six")
        self.assertEqual(rt.build_payload(job)["prompt_text"], "")

    def test_copilot_job_payload_declares_its_backend(self):
        # The viewer picks its parser from meta.backend, because copilot's
        # terminal event shares the `result` type name with claude's.
        log = (Path(__file__).resolve().parent / "fixtures" / "transcript"
               / "copilot-basic.jsonl").read_text(encoding="utf-8")
        job = self.make_job("job-p-copilot", log=log, exit_code=0,
                            meta={"backend": "copilot",
                                  "model": "mai-code-1.1-flash"})
        payload = rt.build_payload(job)
        self.assertEqual(payload["meta"]["backend"], "copilot")
        # The generator filters nothing: ephemeral events reach the page and
        # the browser-side parser drops them.
        self.assertEqual(payload["log_text"], log)
        self.assertIn('"ephemeral":true', payload["log_text"])

    def test_missing_log_is_empty_not_an_error(self):
        job = self.jobs / "job-p-four"
        job.mkdir()
        self.assertEqual(rt.build_payload(job)["log_text"], "")


class InjectTests(unittest.TestCase):
    TEMPLATE = (
        '<html><script id="handoff-payload" type="application/json">null</script>'
        "<p>body</p></html>"
    )

    def test_slot_is_replaced_and_payload_round_trips(self):
        out = rt.inject(self.TEMPLATE, {"job_id": "j", "log_text": "hello"})
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            out, re.S)
        self.assertIsNotNone(match)
        self.assertEqual(json.loads(match.group(1))["log_text"], "hello")

    def test_closing_script_in_log_cannot_break_out_of_the_slot(self):
        hostile = 'before </script><img src=x onerror=alert(1)> after'
        out = rt.inject(self.TEMPLATE, {"log_text": hostile})
        # The literal sequence must not survive into the document.
        self.assertNotIn("</script><img", out)
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            out, re.S)
        self.assertEqual(json.loads(match.group(1))["log_text"], hostile)

    def test_template_without_a_slot_is_an_error(self):
        with self.assertRaises(ValueError):
            rt.inject("<html>no slot</html>", {})


class CliTests(Base):
    def _template(self):
        assets = self.repo / "assets"
        assets.mkdir(exist_ok=True)
        path = assets / "transcript-viewer.html"
        path.write_text(InjectTests.TEMPLATE, encoding="utf-8")
        return path

    def test_no_open_writes_the_page_and_prints_its_path(self):
        self.make_job("job-q-one", log='{"type":"turn.started"}\n', exit_code=0)
        template = self._template()
        status = rt.main(["--repo", str(self.repo), "--no-open",
                          "--template", str(template)])
        self.assertEqual(status, 0)
        out = self.repo / ".handoff" / "sessions" / "none" / "transcript-job-q-one.html"
        self.assertTrue(out.is_file())
        self.assertIn("job-q-one", out.read_text(encoding="utf-8"))

    def test_a_relative_repo_still_opens_the_page(self):
        """A relative --repo made as_uri() raise after the page was written."""
        self.make_job("job-q-two", log='{"type":"turn.started"}\n', exit_code=0)
        template = self._template()
        opened = []
        with unittest.mock.patch.object(rt.webbrowser, "open", opened.append):
            cwd = os.getcwd()
            os.chdir(self.repo.parent)
            self.addCleanup(os.chdir, cwd)
            status = rt.main(["--repo", self.repo.name,
                              "--template", str(template)])
        self.assertEqual(status, 0)
        self.assertEqual(len(opened), 1)
        self.assertTrue(opened[0].startswith("file://"))

    def test_ambiguous_argument_lists_candidates_and_fails(self):
        self.make_job("job-r-capture")
        self.make_job("job-r-capture-r2")
        template = self._template()
        status = rt.main(["--repo", str(self.repo), "--no-open", "capture",
                          "--template", str(template)])
        self.assertEqual(status, 2)

    def test_unknown_job_fails_cleanly(self):
        template = self._template()
        status = rt.main(["--repo", str(self.repo), "--no-open", "ghost",
                          "--template", str(template)])
        self.assertEqual(status, 2)

    def run_main(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            status = rt.main(["--repo", str(self.repo), "--no-open", *extra,
                              "--template", str(self._template())])
        return status, out.getvalue(), err.getvalue()

    def test_the_page_is_filed_under_the_session_that_indexes_its_job(self):
        self.make_job("job-t-one", log="", exit_code=0)
        folder = self.repo / ".handoff" / "sessions" / "20260908_owner-1"
        folder.mkdir(parents=True)
        (folder / "receipt-20260908T100000Z.md").write_text(
            receipt_text("owner-1", codex="job-t-one=1min 00sec"), encoding="utf-8")
        os.environ[SESSION_ENV] = "later-session"
        status, out, _ = self.run_main()
        self.assertEqual(0, status)
        written = folder / "transcript-job-t-one.html"
        self.assertTrue(written.is_file())
        self.assertEqual([], folders_of(self.repo / ".handoff" / "sessions", "later-session"))
        self.assertEqual(written.resolve(), Path(out.strip()).resolve())

    def test_an_unindexed_job_is_filed_under_the_env_session(self):
        self.make_job("job-t-two", log="", exit_code=0)
        os.environ[SESSION_ENV] = "env-session"
        with clock("20261031"):
            status, out, _ = self.run_main()
        self.assertEqual(0, status)
        # No receipt and no folder yet, so the folder takes today's date.
        written = (self.repo / ".handoff" / "sessions" / "20261031_env-session"
                   / "transcript-job-t-two.html")
        self.assertTrue(written.is_file())
        self.assertEqual(written.resolve(), Path(out.strip()).resolve())

    def test_an_unsafe_env_session_is_an_error_and_writes_nothing(self):
        self.make_job("job-t-three", log="", exit_code=0)
        for value in ("../x", "a/b", " abc"):
            with self.subTest(value=value):
                os.environ[SESSION_ENV] = value
                status, out, err = self.run_main()
                self.assertEqual(2, status)
                self.assertTrue(err.startswith("ERROR: "), err)
                self.assertEqual("", out)
                self.assertFalse((self.repo / ".handoff" / "sessions").exists())
                self.assertFalse((self.repo / ".handoff" / "x").exists())

    def test_a_symlinked_sessions_folder_is_refused_with_exit_two(self):
        self.make_job("job-t-four", log="", exit_code=0)
        outside = self.repo / "outside"
        outside.mkdir()
        (self.repo / ".handoff" / "sessions").symlink_to(outside, target_is_directory=True)
        status, out, err = self.run_main()
        self.assertEqual(2, status)
        self.assertIn("ERROR: ", err)
        self.assertEqual([], list(outside.iterdir()))

    def test_an_unreadable_receipt_is_an_error_not_a_traceback(self):
        self.make_job("job-t-five", log="", exit_code=0)
        with unittest.mock.patch.object(rt, "transcript_path", side_effect=PermissionError("denied")):
            status, out, err = self.run_main()
        self.assertEqual(2, status)
        self.assertIn("ERROR: ", err)

    def test_the_not_ignored_warning_names_the_path_that_was_written(self):
        self.make_job("job-t-six", log="", exit_code=0)
        status, out, err = self.run_main()
        self.assertEqual(0, status)
        written = Path(out.strip())
        self.assertEqual("transcript-job-t-six.html", written.name)
        self.assertIn(f"WARNING: {written} is not ignored by Git", err)

    def test_main_does_not_warn_when_the_written_path_is_ignored(self):
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / ".gitignore").write_text(".handoff/\n", encoding="utf-8")
        self.make_job("job-t-seven", log="", exit_code=0)
        status, out, err = self.run_main()
        self.assertEqual(0, status)
        self.assertNotIn("WARNING", err)


class IgnoredTests(Base):
    def test_ignored_output_in_a_folder_not_yet_created(self):
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / ".gitignore").write_text(".handoff/\n", encoding="utf-8")
        out = self.repo / ".handoff" / "sessions" / "none" / "transcript-job.html"
        self.assertFalse(out.parent.exists())
        self.assertTrue(rt._is_ignored(out))


def count_of(durations: str) -> int:
    return 0 if durations == "none" else len(durations.split("; "))


def receipt_text(session: str = "abc", *, codex: str = "none", cc: str = "none",
                 copilot: str = "none", version: str = "8") -> str:
    """A receipt block shaped like the given schema: v6 predates the cursor and
    antigravity fields, which is exactly what a stored receipt looks like."""
    lines = [
        "[Handoff session receipt]", "phase: review", f"claude_session: {session}",
        "duration: 5min 00sec", "checks: unittest", "anomalies: none",
        f"codex_jobs: {count_of(codex)}", f"codex_job_durations: {codex}",
        f"cc_jobs: {count_of(cc)}", f"cc_job_durations: {cc}",
        f"copilot_jobs: {count_of(copilot)}", f"copilot_job_durations: {copilot}",
    ]
    if int(version) >= 7:
        lines += ["cursor_jobs: 0", "cursor_job_durations: none"]
    if int(version) >= 8:
        lines += ["antigravity_jobs: 0", "antigravity_job_durations: none"]
    lines += ["scope: project", "config_source: project", "roles_used: none",
              f"receipt_schema_version: {version}"]
    return "\n".join(lines) + "\n"


class EvidenceBase(unittest.TestCase):
    """A bare temp repo: the helpers read receipts and need no job directory."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        patcher = unittest.mock.patch.dict(os.environ)
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(SESSION_ENV, None)
        self.sessions = self.repo / ".handoff" / "sessions"

    def folder_name(self, session: str, stamp: str = "", date: str | None = None) -> str:
        """The folder `save` files a session under: the bare `none`, else a date and the id.
        The date is the stamp's unless one is given, and a made-up one when there is neither."""
        if session == "none":
            return "none"
        return f"{date or stamp[:8] or '20260908'}_{session}"

    def save(self, session: str, stamp: str, *, name: str | None = None,
             date: str | None = None, **jobs) -> Path:
        folder = self.sessions / self.folder_name(session, stamp, date)
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / (name or f"receipt-{stamp}.md")
        path.write_text(receipt_text(session, **jobs), encoding="utf-8")
        return path

    def assertSamePath(self, expected: Path, actual: Path) -> None:
        # tempfile gives /var/..., which resolves to /private/var/... on macOS.
        self.assertEqual(Path(expected).resolve(), Path(actual).resolve())


class SessionPathTests(EvidenceBase):
    def test_builds_the_path_under_the_resolved_repo_and_creates_nothing(self):
        built = hr.session_path(self.repo, "abc-1", "diagram", "session-timeline-x.svg",
                                date="20261008")
        self.assertEqual(
            self.repo.resolve() / ".handoff" / "sessions" / "20261008_abc-1" / "diagram"
            / "session-timeline-x.svg", built)
        self.assertFalse((self.repo / ".handoff").exists())

    def test_a_string_repo_and_no_parts_name_the_session_folder(self):
        self.assertEqual(self.sessions.resolve() / "20261008_abc",
                         hr.session_path(str(self.repo), "abc", date="20261008"))

    def test_none_and_a_uuid_are_valid_ids(self):
        for session in ("none", "7ee39fd0-93b5-4118-827b-6b6e3ca081ba", "a", "A.b_c-1"):
            with self.subTest(session=session):
                folder = "none" if session == "none" else f"20261008_{session}"
                self.assertEqual(self.sessions.resolve() / folder,
                                 hr.session_path(self.repo, session, date="20261008"))

    def test_a_new_folder_takes_the_given_date(self):
        self.assertEqual(self.sessions.resolve() / "20261008_abc",
                         hr.session_path(self.repo, "abc", date="20261008"))
        # An id with an underscore stays whole after the date.
        self.assertEqual(self.sessions.resolve() / "19990101_a_b" / "receipt-x.md",
                         hr.session_path(self.repo, "a_b", "receipt-x.md", date="19990101"))

    def test_a_none_date_gives_today_in_utc_with_the_clock_patched(self):
        with clock("20261231"):
            self.assertEqual(self.sessions.resolve() / "20261231_abc",
                             hr.session_path(self.repo, "abc"))

    def test_the_clock_is_read_in_utc(self):
        with unittest.mock.patch.object(hr, "datetime") as fake:
            fake.now.return_value = datetime(2026, 12, 31, 23, 59, tzinfo=timezone.utc)
            hr.session_path(self.repo, "abc")
        fake.now.assert_called_once_with(timezone.utc)

    def test_an_existing_folder_wins_over_a_different_given_date(self):
        (self.sessions / "20261008_abc").mkdir(parents=True)
        self.assertEqual(self.sessions.resolve() / "20261008_abc" / "receipt-x.md",
                         hr.session_path(self.repo, "abc", "receipt-x.md", date="20270101"))
        with clock("20300101"):
            self.assertEqual(self.sessions.resolve() / "20261008_abc",
                             hr.session_path(self.repo, "abc"))

    def test_two_folders_for_one_id_pick_the_first_sorted_name(self):
        for name in ("20261021_abc", "20261020_abc", "20261022_abc"):
            (self.sessions / name).mkdir(parents=True, exist_ok=True)
        self.assertEqual(self.sessions.resolve() / "20261020_abc",
                         hr.session_path(self.repo, "abc", date="20270101"))

    def test_the_lookup_is_by_id_not_by_suffix_of_the_name(self):
        # `x_abc` is the session `x_abc`, and a folder for `abc` is not it.
        (self.sessions / "20261008_x_abc").mkdir(parents=True)
        (self.sessions / "20261008_abcd").mkdir()
        self.assertEqual(self.sessions.resolve() / "20270101_abc",
                         hr.session_path(self.repo, "abc", date="20270101"))
        self.assertEqual(self.sessions.resolve() / "20261008_x_abc",
                         hr.session_path(self.repo, "x_abc", date="20270101"))

    def test_none_ignores_the_date_and_any_dated_look_alike(self):
        (self.sessions / "20261008_none").mkdir(parents=True)
        self.assertEqual(self.sessions.resolve() / "none",
                         hr.session_path(self.repo, "none", date="20261008"))
        self.assertEqual(self.sessions.resolve() / "none" / "receipt-x.md",
                         hr.session_path(self.repo, "none", "receipt-x.md"))

    def test_a_bare_id_folder_is_not_the_sessions_folder(self):
        (self.sessions / "abc").mkdir(parents=True)
        self.assertEqual(self.sessions.resolve() / "20261008_abc",
                         hr.session_path(self.repo, "abc", date="20261008"))

    def test_a_bad_date_is_refused(self):
        for date in ("", "2026100", "202610081", "2026-10-8", "abcdefgh", "２０２６１００８",
                     "20261008\n", " 20261008", 20261008):
            with self.subTest(date=date), self.assertRaises(hr.EvidencePathError):
                hr.session_path(self.repo, "abc", date=date)
        # Also for `none`, which ignores a good date: a wrong argument is still wrong.
        with self.assertRaises(hr.EvidencePathError):
            hr.session_path(self.repo, "none", date="soon")

    def test_a_symlinked_sessions_folder_is_refused_before_it_is_listed(self):
        outside = self.repo / "outside"
        (outside / "20261008_abc").mkdir(parents=True)
        (self.repo / ".handoff").mkdir()
        self.sessions.symlink_to(outside, target_is_directory=True)
        listed = AssertionError("sessions/ was listed before it was refused")
        with unittest.mock.patch.object(Path, "iterdir", side_effect=listed), \
                unittest.mock.patch.object(os, "listdir", side_effect=listed), \
                unittest.mock.patch.object(os, "scandir", side_effect=listed):
            for session in ("abc", "none"):
                with self.subTest(session=session), self.assertRaises(hr.EvidencePathError):
                    hr.session_path(self.repo, session)

    def test_a_symlinked_dot_handoff_is_refused_before_sessions_is_listed(self):
        outside = self.repo / "outside"
        (outside / "sessions" / "20261008_abc").mkdir(parents=True)
        (self.repo / ".handoff").symlink_to(outside, target_is_directory=True)
        listed = AssertionError("sessions/ was listed before it was refused")
        with unittest.mock.patch.object(Path, "iterdir", side_effect=listed), \
                unittest.mock.patch.object(os, "listdir", side_effect=listed), \
                unittest.mock.patch.object(os, "scandir", side_effect=listed), \
                self.assertRaises(hr.EvidencePathError):
            hr.session_path(self.repo, "abc")

    def test_a_dangling_symlink_named_for_the_session_is_refused_not_stepped_around(self):
        # Skipping it would open a second folder for the same session.
        self.sessions.mkdir(parents=True)
        (self.sessions / "20261008_abc").symlink_to(self.repo / "missing", target_is_directory=True)
        with self.assertRaises(hr.EvidencePathError):
            hr.session_path(self.repo, "abc", date="20270101")

    def test_an_unsafe_id_is_refused(self):
        for session in ("../x", "a/b", ".", "..", "-x", ".hidden", "", "a b", "a\\b",
                        "a\x00b", "/abs", "abc\n"):
            with self.subTest(session=session), self.assertRaises(hr.EvidencePathError):
                hr.session_path(self.repo, session)

    def test_the_raw_id_is_checked_not_a_stripped_copy(self):
        for session in (" abc", "abc ", "\tabc", "abc\n"):
            with self.subTest(session=session), self.assertRaises(hr.EvidencePathError):
                hr.session_path(self.repo, session)

    def test_an_evidence_path_error_is_a_value_error(self):
        # The callers' existing `except ValueError` handlers must catch it.
        self.assertTrue(issubclass(hr.EvidencePathError, ValueError))

    def test_a_part_must_be_one_safe_segment(self):
        for part in ("", ".", "..", "a/b", "/abs", "a\\b", "../x", "a\x00b"):
            with self.subTest(part=part), self.assertRaises(hr.EvidencePathError):
                hr.session_path(self.repo, "abc", part)
        with self.assertRaises(hr.EvidencePathError):
            hr.session_path(self.repo, "abc", "diagram", "../escape.svg")

    def test_an_existing_regular_tree_is_accepted(self):
        target = self.sessions / "20261008_abc" / "diagram" / "session-timeline-x.svg"
        target.parent.mkdir(parents=True)
        target.write_text("<svg/>")
        self.assertSamePath(target, hr.session_path(
            self.repo, "abc", "diagram", "session-timeline-x.svg"))

    def test_a_symlink_at_any_component_is_refused(self):
        outside = self.repo / "outside"
        (outside / "diagram").mkdir(parents=True)
        (outside / "file.md").write_text("x")
        handoff = self.repo / ".handoff"

        def build(kind: str) -> tuple:
            """Return the session_path arguments that walk through the symlink."""
            if kind == "dot-handoff":
                handoff.symlink_to(outside, target_is_directory=True)
                return ("abc", "receipt-x.md")
            handoff.mkdir()
            if kind == "sessions":
                (handoff / "sessions").symlink_to(outside, target_is_directory=True)
                return ("abc", "receipt-x.md")
            (handoff / "sessions").mkdir()
            if kind == "session-folder":
                (handoff / "sessions" / "20261008_abc").symlink_to(outside, target_is_directory=True)
                return ("abc", "receipt-x.md")
            (handoff / "sessions" / "20261008_abc").mkdir()
            if kind == "diagram":
                (handoff / "sessions" / "20261008_abc" / "diagram").symlink_to(
                    outside / "diagram", target_is_directory=True)
                return ("abc", "diagram", "session-timeline-x.svg")
            if kind == "file":
                (handoff / "sessions" / "20261008_abc" / "receipt-x.md").symlink_to(
                    outside / "file.md")
                return ("abc", "receipt-x.md")
            # A dangling link is a symlink too: writing through it would create
            # the file wherever it points.
            (handoff / "sessions" / "20261008_abc" / "receipt-x.md").symlink_to(
                outside / "missing.md")
            return ("abc", "receipt-x.md")

        for kind in ("dot-handoff", "sessions", "session-folder", "diagram", "file",
                     "dangling-file"):
            with self.subTest(kind=kind):
                shutil.rmtree(handoff, ignore_errors=True)
                if handoff.is_symlink():
                    handoff.unlink()
                arguments = build(kind)
                with self.assertRaises(hr.EvidencePathError):
                    hr.session_path(self.repo, *arguments)

    def test_a_symlinked_session_folder_is_refused_without_parts_too(self):
        # make-receipt.py checks the folder before it prints anything.
        outside = self.repo / "outside"
        outside.mkdir()
        self.sessions.mkdir(parents=True)
        (self.sessions / "20261008_abc").symlink_to(outside, target_is_directory=True)
        with self.assertRaises(hr.EvidencePathError):
            hr.session_path(self.repo, "abc")

    def test_components_above_dot_handoff_are_not_checked(self):
        # macOS temp dirs live under the /var -> /private/var symlink, so a
        # symlink above .handoff has to be the normal case, not a refusal.
        real = self.repo / "real"
        real.mkdir()
        link = self.repo / "link"
        link.symlink_to(real, target_is_directory=True)
        built = hr.session_path(link, "abc", "receipt-x.md", date="20261008")
        self.assertEqual(
            real.resolve() / ".handoff" / "sessions" / "20261008_abc" / "receipt-x.md", built)


class RunStampTests(unittest.TestCase):
    def test_a_saved_receipt_name_yields_its_stamp(self):
        self.assertEqual("20260908T155001Z",
                         hr.run_stamp(Path("/x/sessions/a/receipt-20260908T155001Z.md")))

    def test_any_other_name_yields_its_stem(self):
        self.assertEqual("notes", hr.run_stamp(Path("/x/notes.md")))
        self.assertEqual("receipt-20260908T155001Z", hr.run_stamp(Path("receipt-20260908T155001Z.txt")))

    def test_the_stamp_pattern_moved_here_unchanged(self):
        self.assertEqual(r"^receipt-(\d{8}T\d{6}Z)\.md$", hr.RECEIPT_STAMP.pattern)


class RunDateTests(unittest.TestCase):
    def test_a_stamped_name_yields_the_date_of_its_stamp(self):
        self.assertEqual("20260908", hr.run_date(Path("/x/sessions/a/receipt-20260908T155001Z.md")))
        self.assertEqual("20261231", hr.run_date("receipt-20261231T235959Z.md"))

    def test_any_other_name_yields_none(self):
        for name in ("/x/notes.md", "receipt-notes.md", "receipt-20260908T155001Z.txt",
                     "receipt-20260908T155001Z.md.bak", "cost-receipt-20260908T155001Z.md"):
            with self.subTest(name=name):
                self.assertIsNone(hr.run_date(Path(name)))

    def test_a_stamp_in_other_scripts_digits_is_not_a_date(self):
        self.assertIsNone(hr.run_date(Path("receipt-２０２６０９０８T155001Z.md")))

    def test_the_date_is_the_one_run_stamp_starts_with(self):
        path = Path("receipt-20260908T155001Z.md")
        self.assertEqual(hr.run_stamp(path)[:8], hr.run_date(path))


class FolderSessionTests(unittest.TestCase):
    def test_none_is_the_one_bare_session_folder(self):
        self.assertEqual("none", hr.folder_session("none"))

    def test_a_date_and_an_id_name_the_id(self):
        self.assertEqual("1c9ced0c-a654-4734-a856-d98a6aa1f7de",
                         hr.folder_session("20261008_1c9ced0c-a654-4734-a856-d98a6aa1f7de"))
        self.assertEqual("abc", hr.folder_session("20261008_abc"))

    def test_an_id_may_contain_an_underscore(self):
        self.assertEqual("a_b", hr.folder_session("20261008_a_b"))
        self.assertEqual("x_20261008_y", hr.folder_session("19990101_x_20261008_y"))

    def test_a_name_that_is_not_a_session_folder_is_none(self):
        for name in ("1c9ced0c-a654-4734-a856-d98a6aa1f7de",   # a bare id
                     "abc", "20261008_none", "2026100_x", "202610081_x", "20261008_",
                     "20261008_../x", "20261008_.hidden", "20261008_-x", "20261008_a b",
                     "20261008_a/b", "20261008_abc\n", "20261008", "20261008abc", "",
                     "２０２６１００８_abc", "None"):
            with self.subTest(name=name):
                self.assertIsNone(hr.folder_session(name))

    def test_the_date_is_not_checked_as_a_calendar_date(self):
        # It only sorts the listing, so eight digits are all that is asked.
        self.assertEqual("abc", hr.folder_session("99999999_abc"))

    def test_an_id_that_is_a_dated_none_look_alike_is_still_a_session(self):
        self.assertEqual("Nones", hr.folder_session("20261008_Nones"))
        self.assertEqual("none_", hr.folder_session("20261008_none_"))


class OwnerKeyTests(unittest.TestCase):
    def test_the_key_is_stamp_then_session(self):
        self.assertEqual(("20260908T155001Z", "abc"), hr.owner_key("20260908T155001Z", "abc"))

    def test_a_greater_stamp_wins_then_the_greater_session_id(self):
        older = hr.owner_key("20260908T100000Z", "zzz")
        newer = hr.owner_key("20260908T100001Z", "aaa")
        self.assertGreater(newer, older)
        self.assertGreater(hr.owner_key("20260908T100000Z", "b"),
                           hr.owner_key("20260908T100000Z", "a"))

    def test_a_non_stamped_receipt_sorts_first(self):
        self.assertLess(hr.owner_key("", "zzz"), hr.owner_key("20260908T100000Z", "aaa"))


class SavedReceiptsTests(EvidenceBase):
    def listed(self) -> list:
        """`saved_receipts` with the paths resolved: tempfile gives /var/..., which is /private/var/...."""
        return [(key, path.resolve()) for key, path in hr.saved_receipts(self.repo)]

    def test_no_sessions_folder_lists_nothing(self):
        self.assertEqual([], hr.saved_receipts(self.repo))

    def test_each_receipt_is_keyed_by_its_stamp_and_the_session_id_not_the_folder_name(self):
        a = self.save("sess-a", "20260908T100000Z")
        none = self.save("none", "20260909T100000Z")
        self.assertEqual([(("20260908T100000Z", "sess-a"), a.resolve()),
                          (("20260909T100000Z", "none"), none.resolve())], self.listed())
        self.assertEqual("20260908_sess-a", a.parent.name)

    def test_the_order_follows_the_stamp_whatever_the_folder_dates(self):
        # The older receipt is in the later-dated folder, so a folder-name order would flip these.
        older = self.save("sess-old", "20260908T100000Z", date="20261231")
        newer = self.save("sess-new", "20260909T100000Z", date="20260101")
        self.assertEqual([(("20260908T100000Z", "sess-old"), older.resolve()),
                          (("20260909T100000Z", "sess-new"), newer.resolve())], self.listed())

    def test_a_tied_stamp_goes_to_the_greater_session_id_whatever_the_folder_dates(self):
        low = self.save("aaa", "20260908T100000Z", date="20261231")
        high = self.save("bbb", "20260908T100000Z", date="20260101")
        self.assertEqual([low.resolve(), high.resolve()], [path for _, path in self.listed()])

    def test_a_folder_that_is_not_a_session_folder_is_skipped(self):
        real = self.save("real", "20260908T100000Z")
        for name in ("zzz", "20260909_none", "2026090_zzz", "20260909_", "20260909_.hidden",
                     "20260909_a b"):
            folder = self.sessions / name
            folder.mkdir()
            (folder / "receipt-20261231T000000Z.md").write_text(receipt_text("x"), encoding="utf-8")
        (self.sessions / "20260910_afile").write_text("not a folder")
        self.assertEqual([real.resolve()], [path for _, path in self.listed()])

    def test_a_non_stamped_receipt_has_an_empty_stamp(self):
        notes = self.save("sess-a", "", name="receipt-notes.md")
        self.assertEqual([(("", "sess-a"), notes.resolve())], self.listed())


class ReceiptJobIdsTests(unittest.TestCase):
    def test_reads_every_backend_field_of_a_v8_receipt(self):
        text = receipt_text(codex="job-a=1min 00sec; job-b=running", cc="job-c=2min 00sec",
                            copilot="job-d=3min 00sec")
        self.assertEqual({"job-a", "job-b", "job-c", "job-d"}, hr.receipt_job_ids(text))

    def test_a_v6_receipt_still_indexes(self):
        text = receipt_text(version="6", codex="job-a=1min 00sec", cc="job-c=2min 00sec")
        self.assertEqual({"job-a", "job-c"}, hr.receipt_job_ids(text))

    def test_a_receipt_older_than_any_backend_split_is_read_too(self):
        text = ("[Handoff session receipt]\nphase: review\nclaude_session: abc\n"
                "codex_job_durations: job-a=1min 00sec\nreceipt_schema_version: 3\n")
        self.assertEqual({"job-a"}, hr.receipt_job_ids(text))

    def test_a_receipt_with_no_jobs_or_no_block_is_empty(self):
        self.assertEqual(set(), hr.receipt_job_ids(receipt_text()))
        self.assertEqual(set(), hr.receipt_job_ids("no receipt here"))
        self.assertEqual(set(), hr.receipt_job_ids(""))

    def test_the_block_is_found_inside_markdown(self):
        text = "# notes\n\n" + receipt_text(codex="job-a=1min 00sec") + "\nmore prose\n"
        self.assertEqual({"job-a"}, hr.receipt_job_ids(text))

    def test_no_job_directory_is_needed_and_nothing_is_validated(self):
        # A v8 receipt whose counts disagree with its durations would be refused
        # by validate() and by load_receipt(); here it still indexes.
        text = receipt_text(codex="job-a=1min 00sec").replace("codex_jobs: 1", "codex_jobs: 9")
        self.assertEqual({"job-a"}, hr.receipt_job_ids(text))


class JobOwnerTests(EvidenceBase):
    def test_no_sessions_folder_means_no_owner(self):
        self.assertIsNone(hr.job_owner(self.repo, "job-a"))

    def test_the_session_folder_of_the_indexing_receipt_owns_the_job(self):
        self.save("sess-a", "20260908T100000Z", codex="job-a=1min 00sec")
        self.save("sess-b", "20260908T110000Z", codex="job-b=1min 00sec")
        self.assertEqual("sess-a", hr.job_owner(self.repo, "job-a"))
        self.assertEqual("sess-b", hr.job_owner(self.repo, "job-b"))
        self.assertIsNone(hr.job_owner(self.repo, "job-unindexed"))

    def test_the_newest_of_two_indexing_receipts_wins(self):
        self.save("sess-old", "20260908T100000Z", codex="job-a=1min 00sec")
        self.save("sess-new", "20260909T100000Z", codex="job-a=1min 00sec")
        self.assertEqual("sess-new", hr.job_owner(self.repo, "job-a"))

    def test_a_greater_stamp_beats_a_greater_session_id(self):
        self.save("zzz", "20260908T100000Z", codex="job-a=1min 00sec")
        self.save("aaa", "20260908T100001Z", codex="job-a=1min 00sec")
        self.assertEqual("aaa", hr.job_owner(self.repo, "job-a"))

    def test_a_tied_stamp_goes_to_the_greater_session_id(self):
        self.save("aaa", "20260908T100000Z", codex="job-a=1min 00sec")
        self.save("bbb", "20260908T100000Z", codex="job-a=1min 00sec")
        self.assertEqual("bbb", hr.job_owner(self.repo, "job-a"))

    def test_a_non_stamped_receipt_loses_to_any_stamped_one(self):
        self.save("zzz", "", name="receipt-notes.md", codex="job-a=1min 00sec")
        self.save("aaa", "20260908T100000Z", codex="job-a=1min 00sec")
        self.assertEqual("aaa", hr.job_owner(self.repo, "job-a"))

    def test_a_v6_receipt_still_indexes(self):
        self.save("old", "20260908T100000Z", version="6", codex="job-a=1min 00sec")
        self.assertEqual("old", hr.job_owner(self.repo, "job-a"))

    def test_a_folder_that_fails_the_id_grammar_is_skipped(self):
        for folder in (".hidden", "-x", "a b"):
            self.save(folder, "20260909T100000Z", codex="job-a=1min 00sec")
        self.save("real", "20260908T100000Z", codex="job-a=1min 00sec")
        self.assertEqual("real", hr.job_owner(self.repo, "job-a"))

    def test_a_folder_date_that_disagrees_with_the_stamps_has_no_effect_on_the_owner(self):
        # Each receipt is in a folder dated against its stamp: the older run in the later folder.
        self.save("sess-old", "20260908T100000Z", date="20261231", codex="job-a=1min 00sec")
        self.save("sess-new", "20260909T100000Z", date="20260101", codex="job-a=1min 00sec")
        self.assertEqual("sess-new", hr.job_owner(self.repo, "job-a"))

    def test_a_tied_stamp_goes_to_the_greater_session_id_whatever_the_folder_dates(self):
        self.save("aaa", "20260908T100000Z", date="20261231", codex="job-a=1min 00sec")
        self.save("bbb", "20260908T100000Z", date="20260101", codex="job-a=1min 00sec")
        self.assertEqual("bbb", hr.job_owner(self.repo, "job-a"))

    def test_the_owner_is_the_session_id_never_the_folder_name(self):
        self.save("sess-a", "20260908T100000Z", codex="job-a=1min 00sec")
        self.assertEqual("sess-a", hr.job_owner(self.repo, "job-a"))
        self.assertEqual(["20260908_sess-a"], sorted(p.name for p in self.sessions.iterdir()))

    def test_a_none_folder_owns_as_none(self):
        self.save("none", "20260908T100000Z", codex="job-a=1min 00sec")
        self.assertEqual("none", hr.job_owner(self.repo, "job-a"))

    def test_a_bare_id_folder_and_the_other_non_session_names_are_skipped(self):
        for name in ("zzz", "20260909_none", "2026090_zzz", "20260909_", "20260909_.hidden"):
            folder = self.sessions / name
            folder.mkdir(parents=True)
            (folder / "receipt-20261231T000000Z.md").write_text(
                receipt_text(name, codex="job-a=1min 00sec"), encoding="utf-8")
        self.assertIsNone(hr.job_owner(self.repo, "job-a"))
        self.save("real", "20260908T100000Z", codex="job-a=1min 00sec")
        self.assertEqual("real", hr.job_owner(self.repo, "job-a"))

    def test_a_readable_file_with_no_block_indexes_nothing(self):
        folder = self.sessions / "20260908_abc"
        folder.mkdir(parents=True)
        (folder / "receipt-20260908T100000Z.md").write_text("no block in this file\n")
        self.assertIsNone(hr.job_owner(self.repo, "job-a"))

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "permission bits do not stop root")
    def test_an_unreadable_receipt_raises_rather_than_reading_as_unindexed(self):
        # Silently skipping it could hand the job to an older session.
        self.save("sess-old", "20260908T100000Z", codex="job-a=1min 00sec")
        unreadable = self.save("sess-new", "20260909T100000Z", codex="job-a=1min 00sec")
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o644)
        with self.assertRaises(OSError):
            hr.job_owner(self.repo, "job-a")


class TranscriptPathTests(EvidenceBase):
    def expected(self, folder: str, job_id: str) -> Path:
        return self.repo.resolve() / ".handoff" / "sessions" / folder / f"transcript-{job_id}.html"

    def test_the_owner_comes_from_an_indexing_receipt(self):
        self.save("sess-a", "20260908T100000Z", codex="job-a=1min 00sec")
        # The env value is only a fallback: an indexing receipt outranks it.
        env = {SESSION_ENV: "from-env"}
        self.assertEqual(self.expected("20260908_sess-a", "job-a"),
                         hr.transcript_path(self.repo, "job-a", env=env))

    def test_an_unsafe_env_value_is_not_read_when_a_receipt_owns_the_job(self):
        self.save("sess-a", "20260908T100000Z", codex="job-a=1min 00sec")
        self.assertEqual(self.expected("20260908_sess-a", "job-a"),
                         hr.transcript_path(self.repo, "job-a", env={SESSION_ENV: "../x"}))

    def test_the_env_value_files_a_job_nothing_indexes(self):
        # Nothing owns the job and the session has no folder, so it takes today's date.
        with clock("20261031"):
            self.assertEqual(self.expected("20261031_from-env", "job-a"),
                             hr.transcript_path(self.repo, "job-a", env={SESSION_ENV: "from-env"}))

    def test_the_env_session_keeps_the_folder_it_already_has(self):
        self.save("from-env", "20260908T100000Z")
        with clock("20261031"):
            self.assertEqual(self.expected("20260908_from-env", "job-a"),
                             hr.transcript_path(self.repo, "job-a", env={SESSION_ENV: "from-env"}))

    def test_none_when_there_is_neither_an_owner_nor_an_env_value(self):
        with clock("20261031"):
            self.assertEqual(self.expected("none", "job-a"),
                             hr.transcript_path(self.repo, "job-a", env={}))
            # An empty value is as good as unset.
            self.assertEqual(self.expected("none", "job-a"),
                             hr.transcript_path(self.repo, "job-a", env={SESSION_ENV: ""}))

    def test_the_newest_of_two_indexing_receipts_in_two_sessions_wins(self):
        self.save("sess-old", "20260908T100000Z", codex="job-a=1min 00sec")
        self.save("sess-new", "20260909T100000Z", codex="job-a=1min 00sec")
        self.assertEqual(self.expected("20260909_sess-new", "job-a"),
                         hr.transcript_path(self.repo, "job-a", env={}))

    def test_the_newest_by_stamp_wins_whatever_the_folder_dates(self):
        self.save("sess-old", "20260908T100000Z", date="20261231", codex="job-a=1min 00sec")
        self.save("sess-new", "20260909T100000Z", date="20260101", codex="job-a=1min 00sec")
        self.assertEqual(self.expected("20260101_sess-new", "job-a"),
                         hr.transcript_path(self.repo, "job-a", env={}))

    def test_a_v6_receipt_still_indexes(self):
        self.save("sess-old", "20260908T100000Z", version="6", cc="job-a=1min 00sec")
        self.assertEqual(self.expected("20260908_sess-old", "job-a"),
                         hr.transcript_path(self.repo, "job-a", env={SESSION_ENV: "other"}))

    @unittest.skipIf(os.name == "nt" or (hasattr(os, "geteuid") and os.geteuid() == 0),
                     "permission bits do not stop root")
    def test_an_unreadable_receipt_raises_rather_than_falling_back(self):
        unreadable = self.save("sess-a", "20260908T100000Z", codex="job-a=1min 00sec")
        unreadable.chmod(0)
        self.addCleanup(unreadable.chmod, 0o644)
        with self.assertRaises(OSError):
            hr.transcript_path(self.repo, "job-a", env={SESSION_ENV: "from-env"})

    def test_an_unsafe_env_value_raises(self):
        for value in ("../x", "a/b", ".", "-x", " abc"):
            with self.subTest(value=value), self.assertRaises(hr.EvidencePathError):
                hr.transcript_path(self.repo, "job-a", env={SESSION_ENV: value})

    def test_a_symlinked_sessions_folder_is_refused(self):
        outside = self.repo / "outside"
        outside.mkdir()
        (self.repo / ".handoff").mkdir()
        self.sessions.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(hr.EvidencePathError):
            hr.transcript_path(self.repo, "job-a", env={})
        with self.assertRaises(hr.EvidencePathError):
            hr.transcript_path(self.repo, "job-a", env={SESSION_ENV: "from-env"})

    def test_a_symlinked_session_folder_that_owns_the_job_is_refused(self):
        real = self.save("real", "20260908T100000Z", codex="job-a=1min 00sec").parent
        (self.sessions / "20260908_zzz-link").symlink_to(real, target_is_directory=True)
        with self.assertRaises(hr.EvidencePathError):
            hr.transcript_path(self.repo, "job-a", env={})

    def test_a_job_id_that_is_not_one_segment_is_refused(self):
        with self.assertRaises(hr.EvidencePathError):
            hr.transcript_path(self.repo, "../job-a", env={})

    def test_the_default_env_is_the_process_environment(self):
        os.environ[SESSION_ENV] = "from-process"
        with clock("20261031"):
            self.assertEqual(self.expected("20261031_from-process", "job-a"),
                             hr.transcript_path(self.repo, "job-a"))
        del os.environ[SESSION_ENV]
        self.assertEqual(self.expected("none", "job-a"), hr.transcript_path(self.repo, "job-a"))


class LegacyLayoutNoteTests(EvidenceBase):
    def test_no_note_when_no_legacy_folder_exists(self):
        self.assertIsNone(hr.legacy_layout_note(self.repo))
        (self.repo / ".handoff" / "sessions").mkdir(parents=True)
        self.assertIsNone(hr.legacy_layout_note(self.repo))

    def test_a_file_named_like_a_legacy_folder_is_not_a_legacy_folder(self):
        (self.repo / ".handoff").mkdir()
        (self.repo / ".handoff" / "receipts").write_text("not a folder")
        self.assertIsNone(hr.legacy_layout_note(self.repo))

    def test_one_line_names_the_folders_present_and_the_migration_command(self):
        for name in ("receipts", "transcripts"):
            (self.repo / ".handoff" / name).mkdir(parents=True)
        note = hr.legacy_layout_note(self.repo)
        self.assertNotIn("\n", note)
        self.assertIn(".handoff/receipts", note)
        self.assertIn(".handoff/transcripts", note)
        self.assertNotIn("cost-receipts", note)
        script = Path(hr.__file__).resolve().parent / "migrate-sessions.py"
        self.assertIn(f"python3 {script} --repo {self.repo.resolve()} --apply", note)

    def test_every_legacy_folder_is_named(self):
        for name in ("receipts", "cost-receipts", "transcripts"):
            (self.repo / ".handoff" / name).mkdir(parents=True)
        note = hr.legacy_layout_note(self.repo)
        for name in (".handoff/receipts", ".handoff/cost-receipts", ".handoff/transcripts"):
            self.assertIn(name, note)


class RenderFunctionTests(Base):
    """`render` is the write, split out so a caller can fix the path first."""

    def test_render_writes_the_page_and_reports_a_non_ignored_output(self):
        job = self.make_job("job-s-one", log='{"type":"turn.started"}\n', exit_code=0)
        out = self.repo / ".handoff" / "sessions" / "none" / "transcript-job-s-one.html"
        ignored = rt.render(job, out, InjectTests.TEMPLATE)
        self.assertFalse(ignored)
        self.assertIn("job-s-one", out.read_text(encoding="utf-8"))
        self.assertIn('"output_is_ignored": false', out.read_text(encoding="utf-8"))

    def test_render_reports_an_ignored_output(self):
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        (self.repo / ".gitignore").write_text(".handoff/\n", encoding="utf-8")
        job = self.make_job("job-s-two", log="", exit_code=0)
        out = self.repo / ".handoff" / "sessions" / "none" / "transcript-job-s-two.html"
        self.assertTrue(rt.render(job, out, InjectTests.TEMPLATE))
        self.assertTrue(out.is_file())


if __name__ == "__main__":
    unittest.main()
