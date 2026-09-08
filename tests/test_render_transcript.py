from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "render-transcript.py"
SPEC = importlib.util.spec_from_file_location("render_transcript", SCRIPT)
render_transcript = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = render_transcript
SPEC.loader.exec_module(render_transcript)

rt = render_transcript


class Base(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.repo = Path(temporary.name)
        self.jobs = self.repo / ".handoff" / "jobs"
        self.jobs.mkdir(parents=True)

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
        out = self.repo / ".handoff" / "transcripts" / "job-q-one.html"
        self.assertTrue(out.is_file())
        self.assertIn("job-q-one", out.read_text(encoding="utf-8"))

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


if __name__ == "__main__":
    unittest.main()
