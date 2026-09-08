from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "render-cost-receipt.py"
SPEC = importlib.util.spec_from_file_location("render_cost_receipt", SCRIPT)
rcr = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = rcr
SPEC.loader.exec_module(rcr)


def receipt_text(**overrides) -> str:
    base = {
        "phase": "review",
        "claude_session": "abc123",
        "duration": "74min 05sec",
        "checks": "python3 -m unittest discover -s tests",
        "anomalies": "none",
        "codex_jobs": "1",
        "codex_job_durations": "job-a=1min 00sec",
        "cc_jobs": "1",
        "cc_job_durations": "job-b=2min 00sec",
        "scope": "project",
        "config_source": "project",
        "roles_used": "none",
        "receipt_schema_version": "5",
    }
    base.update(overrides)
    body = "\n".join(f"{k}: {v}" for k, v in base.items())
    return f"[Handoff session receipt]\n{body}\n"


def make_repo(tmp: Path, jobs: dict[str, str]) -> Path:
    """jobs maps jobId -> backend. Creates minimal job dirs."""
    for job_id, backend in jobs.items():
        job = tmp / ".handoff" / "jobs" / job_id
        job.mkdir(parents=True)
        (job / "meta").write_text(f"backend={backend}\nmodel=m\nrole=r\nlabel=l\n")
        (job / "exit_code").write_text("0\n")
    return tmp


class LoadReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        make_repo(self.tmp, {"job-a": "codex", "job-b": "claude"})

    def assert_refused(self, text, needle, repo=None):
        with self.assertRaises(rcr.ReceiptError) as caught:
            rcr.load_receipt(text, repo or self.tmp)
        self.assertIn(needle, str(caught.exception))

    def test_valid_receipt_loads_jobs_in_order(self):
        loaded = rcr.load_receipt(receipt_text(), self.tmp)
        self.assertEqual([j["job_id"] for j in loaded["jobs"]], ["job-a", "job-b"])
        self.assertEqual([j["backend"] for j in loaded["jobs"]], ["codex", "claude"])
        self.assertEqual([j["running"] for j in loaded["jobs"]], [False, False])

    def test_two_receipt_blocks_are_refused(self):
        self.assert_refused(receipt_text() + "\n" + receipt_text(), "exactly one")

    def test_duplicate_field_key_is_refused(self):
        self.assert_refused(receipt_text() + "phase: review\n", "duplicate field")

    def test_schema_version_four_is_refused(self):
        self.assert_refused(receipt_text(receipt_schema_version="4"), "schema_version")

    def test_job_id_with_path_separator_is_refused(self):
        self.assert_refused(
            receipt_text(codex_job_durations="../../etc/passwd=1min 00sec"), "path")

    def test_job_id_duplicated_across_lists_is_refused(self):
        self.assert_refused(
            receipt_text(cc_job_durations="job-a=2min 00sec"), "duplicate job")

    def test_count_disagreeing_with_entries_is_refused(self):
        self.assert_refused(receipt_text(codex_jobs="2"), "codex_jobs")

    def test_backend_disagreeing_with_meta_is_refused(self):
        self.assert_refused(
            receipt_text(codex_job_durations="job-b=1min 00sec",
                         cc_job_durations="job-a=2min 00sec"), "backend")

    def test_missing_job_directory_is_refused(self):
        self.assert_refused(
            receipt_text(codex_job_durations="job-gone=1min 00sec"), "job-gone")

    def test_running_entry_is_carried_not_dropped(self):
        loaded = rcr.load_receipt(receipt_text(cc_job_durations="job-b=running"), self.tmp)
        self.assertEqual([j["running"] for j in loaded["jobs"]], [False, True])

    def test_none_contributes_no_jobs(self):
        loaded = rcr.load_receipt(
            receipt_text(codex_jobs="0", codex_job_durations="none",
                         cc_jobs="0", cc_job_durations="none"), self.tmp)
        self.assertEqual(loaded["jobs"], [])


CODEX_TURN = {"type": "turn.completed", "usage": {
    "input_tokens": 100, "cached_input_tokens": 60, "cache_write_input_tokens": 0,
    "output_tokens": 10, "reasoning_output_tokens": 4}}

CLAUDE_RESULT = {"type": "result", "total_cost_usd": 6.633623999999999,
                 "usage": {"input_tokens": 160, "cache_creation_input_tokens": 132345,
                           "cache_read_input_tokens": 8222548, "output_tokens": 47924,
                           "output_tokens_details": {"thinking_tokens": 6602}},
                 "modelUsage": {"claude-opus-5": {"costUSD": 6.633623999999999}},
                 "permission_denials": [{"tool_name": "Bash"}] * 11}


class FoldUsageTests(unittest.TestCase):
    def test_codex_usage_sums_across_turns(self):
        folded = rcr.fold_usage([CODEX_TURN, CODEX_TURN], "codex")
        self.assertEqual(folded["usage"]["input"], 200)
        self.assertEqual(folded["usage"]["reasoning"], 8)
        self.assertIsNone(folded["cost_usd"])

    def test_codex_sums_completed_and_failed_turns(self):
        failed = {"type": "turn.failed", "usage": {"input_tokens": 5, "output_tokens": 1}}
        folded = rcr.fold_usage([CODEX_TURN, failed], "codex")
        self.assertEqual(folded["usage"]["input"], 105)
        self.assertEqual(folded["usage"]["output"], 11)

    def test_claude_result_yields_cost_models_and_denials(self):
        folded = rcr.fold_usage([CLAUDE_RESULT], "claude")
        self.assertEqual(folded["cost_usd"], 6.633623999999999)
        self.assertEqual(folded["models"], ["claude-opus-5"])
        self.assertEqual(folded["denials"], 11)
        self.assertEqual(folded["usage"]["cache_read"], 8222548)
        self.assertEqual(folded["usage"]["reasoning"], 6602)

    def test_model_usage_is_never_added_to_usage(self):
        folded = rcr.fold_usage([CLAUDE_RESULT], "claude")
        self.assertEqual(folded["usage"]["input"], 160)

    def test_repeated_claude_result_takes_the_last_and_flags_it(self):
        second = dict(CLAUDE_RESULT, total_cost_usd=1.5)
        folded = rcr.fold_usage([CLAUDE_RESULT, second], "claude")
        self.assertEqual(folded["cost_usd"], 1.5)
        self.assertTrue(folded["repeated"])

    def test_no_terminal_record_yields_unknown_counters(self):
        folded = rcr.fold_usage([], "claude")
        self.assertEqual(set(folded["usage"].values()), {None})

    def test_missing_individual_counter_is_unknown_not_zero(self):
        partial = {"type": "turn.completed", "usage": {"input_tokens": 7}}
        folded = rcr.fold_usage([partial], "codex")
        self.assertEqual(folded["usage"]["input"], 7)
        self.assertIsNone(folded["usage"]["output"])

    def test_measured_zero_is_kept_as_zero(self):
        zeroed = {"type": "turn.completed", "usage": {"input_tokens": 0, "output_tokens": 0}}
        folded = rcr.fold_usage([zeroed], "codex")
        self.assertEqual(folded["usage"]["input"], 0)


class JobRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.jobs = self.tmp / ".handoff" / "jobs"

    def write_job(self, job_id, meta_lines, events=(), exit_code="0"):
        job = self.jobs / job_id
        job.mkdir(parents=True)
        (job / "meta").write_text("\n".join(meta_lines) + "\n")
        if events:
            (job / "log.jsonl").write_text(
                "\n".join(json.dumps(e) for e in events) + "\n")
        if exit_code is not None:
            (job / "exit_code").write_text(exit_code + "\n")
        return job

    def test_done_codex_job_row(self):
        self.write_job("job-a", ["backend=codex", "model=gpt-6-astra",
                                 "role=deep_reasoner", "label=spec"], [CODEX_TURN])
        row = rcr.job_row(self.tmp, {"job_id": "job-a", "backend": "codex", "running": False})
        self.assertEqual(row["state"], "DONE")
        self.assertEqual(row["model"], "gpt-6-astra")
        self.assertEqual(row["usage"]["input"], 100)

    def test_no_exit_code_and_no_pid_is_failed_not_running(self):
        self.write_job("job-x", ["backend=codex", "model=m"], exit_code=None)
        row = rcr.job_row(self.tmp, {"job_id": "job-x", "backend": "codex", "running": False})
        self.assertEqual(row["state"], "FAILED")

    def test_cancelled_job(self):
        job = self.write_job("job-c", ["backend=codex", "model=m"], exit_code=None)
        (job / "cancelled").write_text("")
        row = rcr.job_row(self.tmp, {"job_id": "job-c", "backend": "codex", "running": False})
        self.assertEqual(row["state"], "CANCELLED")

    def test_truncated_jsonl_yields_unknown_not_a_crash(self):
        job = self.write_job("job-t", ["backend=codex", "model=m"])
        (job / "log.jsonl").write_text('{"type": "turn.compl')
        row = rcr.job_row(self.tmp, {"job_id": "job-t", "backend": "codex", "running": False})
        self.assertIsNone(row["usage"]["input"])

    def test_model_inherit_resolves_through_parent(self):
        self.write_job("job-p", ["backend=claude", "model=opus"])
        self.write_job("job-p-r2", ["backend=claude", "model=inherit", "parent=job-p"])
        row = rcr.job_row(self.tmp, {"job_id": "job-p-r2", "backend": "claude", "running": False})
        self.assertEqual(row["model"], "opus")

    def test_model_inherit_without_parent_is_named_unresolved(self):
        self.write_job("job-o", ["backend=claude", "model=inherit", "parent=gone"])
        row = rcr.job_row(self.tmp, {"job_id": "job-o", "backend": "claude", "running": False})
        self.assertEqual(row["model"], "inherit (unresolved)")

    def test_job_without_backend_line_is_codex_by_construction(self):
        self.write_job("job-legacy", ["model=m"], [CODEX_TURN])
        row = rcr.job_row(self.tmp, {"job_id": "job-legacy", "backend": "codex", "running": False})
        self.assertEqual(row["usage"]["input"], 100)


from datetime import datetime, timedelta, timezone


def transcript_line(ts: str, tokens: int) -> str:
    return json.dumps({
        "type": "assistant", "timestamp": ts,
        "message": {"model": "claude-opus-5",
                    "usage": {"input_tokens": tokens, "output_tokens": 1}}})


class IntervalTests(unittest.TestCase):
    def test_parse_duration(self):
        self.assertEqual(rcr.parse_duration("74min 05sec"), 74 * 60 + 5)
        self.assertEqual(rcr.parse_duration("0min 00sec"), 0)

    def test_saved_receipt_name_yields_an_interval(self):
        path = Path("/x/.handoff/receipts/receipt-20260908T155001Z.md")
        start, end = rcr.run_interval(path, "11min 01sec")
        self.assertEqual(end, datetime(2026, 9, 8, 15, 50, 1, tzinfo=timezone.utc))
        self.assertEqual((end - start).total_seconds(), 661)

    def test_unstamped_input_has_no_interval(self):
        self.assertIsNone(rcr.run_interval(Path("/x/notes.md"), "11min 01sec"))


class DriverRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.projects = self.tmp / "projects"
        (self.projects / "slug").mkdir(parents=True)
        self.path = self.projects / "slug" / "sid-1.jsonl"
        self.path.write_text("\n".join([
            transcript_line("2026-09-08T15:00:00.000Z", 10),   # before
            transcript_line("2026-09-08T15:45:00.000Z", 100),  # inside
            transcript_line("2026-09-08T16:30:00.000Z", 1000),  # after
        ]) + "\n")
        self.interval = (datetime(2026, 9, 8, 15, 39, 0, tzinfo=timezone.utc),
                         datetime(2026, 9, 8, 15, 50, 1, tzinfo=timezone.utc))

    def test_interval_scopes_the_row(self):
        row = rcr.driver_row("sid-1", self.interval, self.projects)
        self.assertEqual(row["state"], "measured")
        self.assertEqual(row["usage"]["input"], 100)
        self.assertEqual(row["models"], ["claude-opus-5"])
        self.assertIsNone(row["cost_usd"])

    def test_transcript_growth_after_the_receipt_does_not_change_the_row(self):
        before = rcr.driver_row("sid-1", self.interval, self.projects)
        with self.path.open("a") as handle:
            handle.write(transcript_line("2026-09-08T17:00:00.000Z", 9999) + "\n")
        self.assertEqual(rcr.driver_row("sid-1", self.interval, self.projects), before)

    def test_two_intervals_in_one_session_give_different_rows(self):
        later = (datetime(2026, 9, 8, 16, 0, 0, tzinfo=timezone.utc),
                 datetime(2026, 9, 8, 17, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(rcr.driver_row("sid-1", later, self.projects)["usage"]["input"], 1000)

    def test_no_interval_is_unscoped_and_totals_the_session(self):
        row = rcr.driver_row("sid-1", None, self.projects)
        self.assertEqual(row["state"], "unscoped")
        self.assertEqual(row["usage"]["input"], 1110)

    def test_missing_transcript_is_unavailable(self):
        row = rcr.driver_row("sid-absent", self.interval, self.projects)
        self.assertEqual(row["state"], "unavailable")
        self.assertEqual(set(row["usage"].values()), {None})

    def test_session_id_is_a_literal_not_a_glob(self):
        row = rcr.driver_row("sid-*", self.interval, self.projects)
        self.assertEqual(row["state"], "unavailable")


def row(job_id, backend, input_tokens, cost=None, state="DONE", denials=None):
    usage = {c: None for c in rcr.COUNTERS}
    usage["input"] = input_tokens
    return {"job_id": job_id, "label": "", "role": "", "backend": backend,
            "model": "m", "state": state, "usage": usage, "cost_usd": cost,
            "denials": denials, "models": [], "repeated": False}


class SummaryTests(unittest.TestCase):
    def test_codex_jobs_appear_in_both_figures(self):
        summary = rcr.summarize([row("a", "codex", 100), row("b", "claude", 10, cost=2.5)])
        self.assertEqual(summary["codex_subscription"]["usage"]["input"]["value"], 100)
        self.assertEqual(summary["codex_subscription"]["jobs"], 1)
        self.assertEqual(summary["outside_driver"]["usage"]["input"]["value"], 110)
        self.assertEqual(summary["outside_driver"]["jobs"], 2)

    def test_codex_figure_never_carries_a_cost(self):
        summary = rcr.summarize([row("a", "codex", 100)])
        self.assertIsNone(summary["codex_subscription"]["cost_usd"])

    def test_cost_is_the_sum_of_claude_jobs_only_and_unrounded(self):
        summary = rcr.summarize([row("a", "codex", 1),
                                 row("b", "claude", 1, cost=6.633623999999999)])
        self.assertEqual(summary["outside_driver"]["cost_usd"], 6.633623999999999)

    def test_the_two_figures_are_not_addends(self):
        summary = rcr.summarize([row("a", "codex", 100), row("b", "claude", 10)])
        combined = (summary["codex_subscription"]["usage"]["input"]["value"]
                    + summary["outside_driver"]["usage"]["input"]["value"])
        self.assertNotEqual(summary["outside_driver"]["usage"]["input"]["value"], combined)

    def test_an_unmeasured_row_marks_the_column_incomplete(self):
        summary = rcr.summarize([row("a", "codex", 100),
                                 row("b", "codex", None, state="RUNNING")])
        column = summary["codex_subscription"]["usage"]["input"]
        self.assertEqual(column["value"], 100)
        self.assertFalse(column["complete"])
        self.assertEqual((column["measured"], column["total"]), (1, 2))

    def test_a_fully_measured_column_is_complete(self):
        column = rcr.summarize([row("a", "codex", 100)])["codex_subscription"]["usage"]["input"]
        self.assertTrue(column["complete"])

    def test_missing_cost_on_a_claude_job_marks_cost_incomplete(self):
        summary = rcr.summarize([row("b", "claude", 1, cost=None)])
        self.assertFalse(summary["outside_driver"]["cost_complete"])

    def test_denials_sum_across_claude_jobs(self):
        summary = rcr.summarize([row("b", "claude", 1, denials=11),
                                 row("c", "claude", 1, denials=2)])
        self.assertEqual(summary["denials"], 13)

    def test_denials_are_none_when_no_claude_job_reported_any(self):
        self.assertIsNone(rcr.summarize([row("a", "codex", 1)])["denials"])


if __name__ == "__main__":
    unittest.main()
