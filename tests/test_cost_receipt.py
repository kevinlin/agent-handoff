from __future__ import annotations

import importlib.util
import json
import re
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
        "copilot_jobs": "1",
        "copilot_job_durations": "job-c=3min 00sec",
        "scope": "project",
        "config_source": "project",
        "roles_used": "none",
        "receipt_schema_version": "6",
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
        make_repo(self.tmp, {"job-a": "codex", "job-b": "claude", "job-c": "copilot"})

    def assert_refused(self, text, needle, repo=None):
        with self.assertRaises(rcr.ReceiptError) as caught:
            rcr.load_receipt(text, repo or self.tmp)
        self.assertIn(needle, str(caught.exception))

    def test_valid_receipt_loads_jobs_in_order(self):
        loaded = rcr.load_receipt(receipt_text(), self.tmp)
        self.assertEqual([j["job_id"] for j in loaded["jobs"]], ["job-a", "job-b", "job-c"])
        self.assertEqual([j["backend"] for j in loaded["jobs"]],
                         ["codex", "claude", "copilot"])
        self.assertEqual([j["running"] for j in loaded["jobs"]], [False, False, False])

    def test_two_receipt_blocks_are_refused(self):
        self.assert_refused(receipt_text() + "\n" + receipt_text(), "exactly one")

    def test_duplicate_field_key_is_refused(self):
        self.assert_refused(receipt_text() + "phase: review\n", "duplicate field")

    def test_schema_version_five_is_refused(self):
        self.assert_refused(receipt_text(receipt_schema_version="5"), "schema_version")

    def test_job_id_with_path_separator_is_refused(self):
        self.assert_refused(
            receipt_text(codex_job_durations="../../etc/passwd=1min 00sec"), "path")

    def test_job_id_duplicated_across_lists_is_refused(self):
        self.assert_refused(
            receipt_text(cc_job_durations="job-a=2min 00sec"), "duplicate job")

    def test_a_copilot_count_disagreeing_with_entries_is_refused(self):
        self.assert_refused(receipt_text(copilot_jobs="2"), "copilot_jobs")

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
        self.assertEqual([j["running"] for j in loaded["jobs"]], [False, True, False])

    def test_none_contributes_no_jobs(self):
        loaded = rcr.load_receipt(
            receipt_text(codex_jobs="0", codex_job_durations="none",
                         cc_jobs="0", cc_job_durations="none",
                         copilot_jobs="0", copilot_job_durations="none"), self.tmp)
        self.assertEqual(loaded["jobs"], [])

    def test_a_copilot_job_listed_under_the_wrong_pair_is_refused(self):
        self.assert_refused(
            receipt_text(cc_job_durations="job-c=2min 00sec",
                         copilot_job_durations="job-b=3min 00sec"), "backend")


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


# Probe-log 11: one parent copilot job and its fix round on the same session.
# tokenDetails and modelMetrics are per invocation; the top-level
# totalPremiumRequestCost and totalNanoAiu are cumulative for the session.
COPILOT_PARENT_USAGE = {
    "totalPremiumRequestCost": 1, "totalUserRequests": 1, "totalNanoAiu": 84828000,
    "tokenDetails": {"input": {"tokenCount": 587}, "cache_read": {"tokenCount": 31744},
                     "cache_write": {"tokenCount": 0}, "output": {"tokenCount": 80}},
    "modelMetrics": {"mai-code-1.1-flash": {
        "requests": {"count": 1, "cost": 1},
        "usage": {"reasoningTokens": 0},
        "totalNanoAiu": 84828000}},
}
COPILOT_RESUME_USAGE = {
    "totalPremiumRequestCost": 2, "totalUserRequests": 1, "totalNanoAiu": 130944000,
    "tokenDetails": {"input": {"tokenCount": 231}, "cache_read": {"tokenCount": 16128},
                     "cache_write": {"tokenCount": 0}, "output": {"tokenCount": 77}},
    "modelMetrics": {"mai-code-1.1-flash": {
        "requests": {"count": 1, "cost": 1},
        "usage": {"reasoningTokens": 0},
        "totalNanoAiu": 46116000}},
}
# Probe-log 12 and 15: a rejection before any session writes a real file of zeros.
COPILOT_ZEROED_USAGE = {
    "totalPremiumRequestCost": 0, "totalUserRequests": 0, "totalNanoAiu": 0,
    "modelMetrics": {}, "agentMetrics": {},
    "lastCallInputTokens": 0, "lastCallOutputTokens": 0,
}
COPILOT_DENIAL = {"type": "tool.execution_complete", "data": {
    "toolCallId": "t1",
    "error": {"message": "Permission to run this tool was denied", "code": "denied"}}}
COPILOT_OK_TOOL = {"type": "tool.execution_complete",
                   "data": {"toolCallId": "t2", "error": None}}


class CopilotFoldTests(unittest.TestCase):
    def test_tokens_come_from_token_details_and_model_metrics(self):
        folded = rcr.fold_usage([], "copilot", COPILOT_PARENT_USAGE)
        self.assertEqual(folded["usage"]["input"], 587)
        self.assertEqual(folded["usage"]["cache_read"], 31744)
        self.assertEqual(folded["usage"]["output"], 80)
        self.assertEqual(folded["usage"]["reasoning"], 0)
        self.assertEqual(folded["models"], ["mai-code-1.1-flash"])

    def test_a_copilot_job_reports_no_usd(self):
        self.assertIsNone(rcr.fold_usage([], "copilot", COPILOT_PARENT_USAGE)["cost_usd"])

    def test_credits_are_the_per_invocation_model_metrics(self):
        folded = rcr.fold_usage([], "copilot", COPILOT_RESUME_USAGE)
        # NOT the cumulative top-level 130944000.
        self.assertEqual(folded["credits"]["nano_aiu"], 46116000)
        self.assertEqual(folded["credits"]["premium_requests"], 1)

    def test_tokens_sum_across_a_parent_and_its_fix_round_credits_do_not(self):
        """Probe-log 11. Tokens are per invocation, so they add. The top-level
        credit meter is cumulative, so adding it double-counts the parent."""
        parent = rcr.fold_usage([], "copilot", COPILOT_PARENT_USAGE)
        resume = rcr.fold_usage([], "copilot", COPILOT_RESUME_USAGE)
        self.assertEqual(parent["usage"]["input"] + resume["usage"]["input"], 818)

        session_total = parent["credits"]["nano_aiu"] + resume["credits"]["nano_aiu"]
        self.assertEqual(session_total, COPILOT_RESUME_USAGE["totalNanoAiu"])
        self.assertNotEqual(
            session_total,
            COPILOT_PARENT_USAGE["totalNanoAiu"] + COPILOT_RESUME_USAGE["totalNanoAiu"])

    def test_absent_usage_file_is_unknown(self):
        folded = rcr.fold_usage([], "copilot", None)
        self.assertEqual(set(folded["usage"].values()), {None})
        self.assertIsNone(folded["credits"]["nano_aiu"])
        self.assertIsNone(folded["credits"]["premium_requests"])

    def test_a_zeroed_usage_file_is_a_measured_zero_not_unknown(self):
        folded = rcr.fold_usage([], "copilot", COPILOT_ZEROED_USAGE)
        self.assertEqual(folded["usage"]["input"], 0)
        self.assertEqual(folded["credits"]["nano_aiu"], 0)
        self.assertEqual(folded["credits"]["premium_requests"], 0)

    def test_denials_are_counted_from_the_typed_event(self):
        folded = rcr.fold_usage([COPILOT_DENIAL, COPILOT_OK_TOOL, COPILOT_DENIAL],
                                "copilot", COPILOT_PARENT_USAGE)
        self.assertEqual(folded["denials"], 2)

    def test_denials_are_counted_even_with_no_usage_file(self):
        self.assertEqual(rcr.fold_usage([COPILOT_DENIAL], "copilot", None)["denials"], 1)

    def test_a_copilot_result_event_is_not_read_as_a_claude_one(self):
        """Copilot's terminal event is also type "result", with a different
        payload. The backend decides the parser, never the event shape."""
        copilot_result = {"type": "result", "sessionId": "s1", "exitCode": 0,
                          "usage": {"premiumRequests": 1}}
        folded = rcr.fold_usage([copilot_result], "copilot", None)
        self.assertEqual(set(folded["usage"].values()), {None})
        self.assertIsNone(folded["cost_usd"])


class CopilotJobRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def write(self, job_id, usage=None, events=()):
        job = self.tmp / ".handoff" / "jobs" / job_id
        job.mkdir(parents=True)
        (job / "meta").write_text("backend=copilot\nmodel=mai-code-1.1-flash\n"
                                  "role=fast_worker\nlabel=impl\n")
        (job / "exit_code").write_text("0\n")
        if usage is not None:
            (job / "usage.json").write_text(json.dumps(usage))
        if events:
            (job / "log.jsonl").write_text("\n".join(json.dumps(e) for e in events) + "\n")
        return {"job_id": job_id, "backend": "copilot", "running": False}

    def test_row_carries_tokens_credits_and_denials(self):
        row = rcr.job_row(self.tmp, self.write("job-cp", COPILOT_PARENT_USAGE,
                                               [COPILOT_DENIAL]))
        self.assertEqual(row["usage"]["input"], 587)
        self.assertEqual(row["premium_requests"], 1)
        self.assertEqual(row["nano_aiu"], 84828000)
        self.assertEqual(row["denials"], 1)
        self.assertEqual(row["model"], "mai-code-1.1-flash")

    def test_missing_usage_file_leaves_the_credits_unknown(self):
        row = rcr.job_row(self.tmp, self.write("job-killed"))
        self.assertIsNone(row["nano_aiu"])
        self.assertIsNone(row["usage"]["input"])

    def test_a_corrupt_usage_file_is_unknown_not_a_crash(self):
        job = self.write("job-bad", COPILOT_PARENT_USAGE)
        (self.tmp / ".handoff" / "jobs" / "job-bad" / "usage.json").write_text('{"tok')
        self.assertIsNone(rcr.job_row(self.tmp, job)["nano_aiu"])

    def test_a_codex_row_reports_no_credits(self):
        job = self.tmp / ".handoff" / "jobs" / "job-cx"
        job.mkdir(parents=True)
        (job / "meta").write_text("backend=codex\nmodel=m\n")
        (job / "exit_code").write_text("0\n")
        row = rcr.job_row(self.tmp, {"job_id": "job-cx", "backend": "codex",
                                     "running": False})
        self.assertIsNone(row["premium_requests"])
        self.assertIsNone(row["nano_aiu"])


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


def row(job_id, backend, input_tokens, cost=None, state="DONE", denials=None,
        premium_requests=None, nano_aiu=None):
    usage = {c: None for c in rcr.COUNTERS}
    usage["input"] = input_tokens
    return {"job_id": job_id, "label": "", "role": "", "backend": backend,
            "model": "m", "state": state, "usage": usage, "cost_usd": cost,
            "denials": denials, "models": [], "repeated": False,
            "premium_requests": premium_requests, "nano_aiu": nano_aiu}


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

    def test_credit_figure_sums_the_per_invocation_readings(self):
        summary = rcr.summarize([
            row("job-cp", "copilot", 587, premium_requests=1, nano_aiu=84828000),
            row("job-cp-r2", "copilot", 231, premium_requests=1, nano_aiu=46116000)])
        credits = summary["copilot_credits"]
        self.assertEqual(credits["jobs"], 2)
        self.assertEqual(credits["nano_aiu"]["value"], 130944000)
        self.assertEqual(credits["premium_requests"]["value"], 2)
        self.assertTrue(credits["nano_aiu"]["complete"])

    def test_an_unread_copilot_job_marks_the_credit_column_incomplete(self):
        summary = rcr.summarize([
            row("job-cp", "copilot", 587, premium_requests=1, nano_aiu=84828000),
            row("job-dead", "copilot", None, state="FAILED")])
        column = summary["copilot_credits"]["nano_aiu"]
        self.assertEqual(column["value"], 84828000)
        self.assertFalse(column["complete"])

    def test_a_run_without_copilot_has_no_credit_jobs(self):
        self.assertEqual(rcr.summarize([row("a", "codex", 1)])["copilot_credits"]["jobs"], 0)

    def test_a_copilot_job_contributes_no_usd(self):
        summary = rcr.summarize([row("job-cp", "copilot", 587, nano_aiu=1)])
        self.assertFalse(summary["outside_driver"]["cost_applicable"])
        self.assertIsNone(summary["outside_driver"]["cost_usd"])

    def test_denials_sum_across_claude_and_copilot_jobs(self):
        summary = rcr.summarize([row("b", "claude", 1, denials=11),
                                 row("c", "copilot", 1, denials=2)])
        self.assertEqual(summary["denials"], 13)

    def test_denials_are_none_when_no_claude_job_reported_any(self):
        self.assertIsNone(rcr.summarize([row("a", "codex", 1)])["denials"])

    def test_codex_only_run_has_no_applicable_cost(self):
        summary = rcr.summarize([row("a", "codex", 100)])
        self.assertFalse(summary["outside_driver"]["cost_applicable"])

    def test_a_claude_job_makes_the_cost_applicable_even_when_unread(self):
        summary = rcr.summarize([row("b", "claude", 1, cost=None)])
        self.assertTrue(summary["outside_driver"]["cost_applicable"])
        self.assertIsNone(summary["outside_driver"]["cost_usd"])


class MarkdownTests(unittest.TestCase):
    def payload(self, rows=None, driver=None):
        rows = rows if rows is not None else [
            row("job-a", "codex", 779279), row("job-b", "claude", 160, cost=6.633623999999999)]
        driver = driver or {"state": "measured", "usage": {c: None for c in rcr.COUNTERS},
                            "models": ["claude-opus-5"], "cost_usd": None}
        return rcr.build_payload(
            {"fields": {"claude_session": "sid-1", "duration": "11min 01sec"}},
            rows, driver, None, ["/x/.handoff/jobs/job-a/log.jsonl"])

    def test_both_figures_are_present_with_their_labels(self):
        out = rcr.render_markdown(self.payload())
        self.assertIn("Ran on a Codex subscription", out)
        self.assertIn("Ran outside the driver session", out)

    def test_cost_is_unrounded(self):
        self.assertIn("6.633623999999999", rcr.render_markdown(self.payload()))

    def test_cost_is_labelled_cli_reported(self):
        self.assertIn("CLI-reported cost", rcr.render_markdown(self.payload()))

    def test_no_savings_language_anywhere(self):
        out = rcr.render_markdown(self.payload()).lower()
        for banned in ("saved", "savings", "avoided", "cheaper", "instead of"):
            self.assertNotIn(banned, out)

    def test_the_overlap_is_stated(self):
        self.assertIn("not addends", rcr.render_markdown(self.payload()))

    def test_no_line_equals_the_sum_of_the_two_figures(self):
        out = rcr.render_markdown(self.payload())
        self.assertNotIn(str(779279 + 779279 + 160), out)

    def test_incomplete_column_renders_a_floor(self):
        rows = [row("job-a", "codex", 100), row("job-b", "codex", None, state="RUNNING")]
        self.assertIn("≥ 100", rcr.render_markdown(self.payload(rows=rows)))

    def test_unscoped_driver_row_is_labelled(self):
        driver = {"state": "unscoped", "usage": {c: None for c in rcr.COUNTERS},
                  "models": [], "cost_usd": None}
        self.assertIn("unscoped", rcr.render_markdown(self.payload(driver=driver)))

    def test_pipe_in_a_label_is_escaped(self):
        self.assertEqual(rcr.md_cell("a|b"), "a\\|b")

    def test_none_renders_as_unknown(self):
        self.assertEqual(rcr.md_cell(None), "unknown")

    def test_measured_zero_renders_as_zero(self):
        self.assertEqual(rcr.md_cell(0), "0")

    def test_a_single_job_figure_is_not_pluralised(self):
        out = rcr.render_markdown(self.payload(rows=[row("job-a", "codex", 100)]))
        self.assertIn("(1 job)", out)
        self.assertNotIn("1 jobs", out)

    def test_two_jobs_are_pluralised(self):
        self.assertIn("(2 jobs)", rcr.render_markdown(self.payload()))

    def test_codex_only_run_states_there_were_no_claude_jobs(self):
        out = rcr.render_markdown(self.payload(rows=[row("job-a", "codex", 100)]))
        self.assertIn("no claude-backed jobs", out)
        self.assertNotIn("cost of the claude-backed jobs: unknown", out)

    def test_an_unread_claude_cost_still_renders_unknown(self):
        out = rcr.render_markdown(self.payload(rows=[row("job-b", "claude", 1, cost=None)]))
        self.assertIn("cost of the claude-backed jobs: unknown", out)

    def test_credit_meter_sentence_names_credits_not_dollars(self):
        rows = [row("job-cp", "copilot", 587, premium_requests=1, nano_aiu=84828000)]
        out = rcr.render_markdown(self.payload(rows=rows))
        self.assertIn("Copilot AI-credit meter", out)
        self.assertIn("84,828,000", out)
        self.assertIn("AI credits", out)
        self.assertNotIn("$", out)

    def test_a_run_without_copilot_says_nothing_was_metered(self):
        self.assertIn("No job ran on the Copilot AI-credit meter",
                      rcr.render_markdown(self.payload()))

    def test_job_table_carries_the_credit_columns(self):
        rows = [row("job-cp", "copilot", 587, premium_requests=1, nano_aiu=84828000),
                row("job-a", "codex", 100)]
        out = rcr.render_markdown(self.payload(rows=rows))
        self.assertIn("| Premium requests | Nano-AIU |", out)
        # A codex row has no credit meter to read, which is not an unknown.
        self.assertRegex(out, r"\| codex \|.*\| n/a \| n/a \|")

    def test_a_copilot_job_reports_no_currency_rather_than_unknown(self):
        rows = [row("job-cp", "copilot", 587, premium_requests=1, nano_aiu=1)]
        out = rcr.render_markdown(self.payload(rows=rows))
        job_line = next(l for l in out.splitlines() if l.startswith("| `job-cp`"))
        self.assertTrue(job_line.endswith("| n/a - AI credits |"), job_line)

    def test_denials_are_attributed_to_both_reporting_backends(self):
        rows = [row("job-cp", "copilot", 1, denials=2)]
        out = rcr.render_markdown(self.payload(rows=rows))
        self.assertIn("claude-backed and copilot-backed jobs", out)

    def test_denial_command_strings_never_reach_the_output(self):
        rows = [row("job-b", "claude", 1, denials=11)]
        out = rcr.render_markdown(self.payload(rows=rows))
        self.assertIn("11", out)
        self.assertNotIn("curl", out)


TEMPLATE = ROOT / "assets" / "cost-receipt.html"


class TemplateTests(unittest.TestCase):
    def setUp(self):
        self.html = TEMPLATE.read_text(encoding="utf-8")

    def test_template_has_a_payload_slot(self):
        self.assertIn('<script id="handoff-payload" type="application/json">', self.html)

    def test_template_is_self_contained(self):
        for banned in ("http://", "https://cdn", "<link rel=\"stylesheet\""):
            self.assertNotIn(banned, self.html)

    def test_template_supports_both_themes(self):
        self.assertIn("light-dark(", self.html)

    def test_template_renders_the_credit_columns_and_summary(self):
        for needle in ("copilot_credits", "Premium requests", "Nano-AIU",
                       "Copilot AI-credit meter", "j.premium_requests", "j.nano_aiu"):
            self.assertIn(needle, self.html)

    def test_template_attributes_denials_to_both_reporting_backends(self):
        self.assertIn("claude-backed and ", self.html)
        self.assertIn("copilot-backed jobs", self.html)
        self.assertNotIn("denials across claude-backed jobs", self.html)

    def test_page_never_uses_inner_html(self):
        self.assertNotIn("innerHTML", self.html)

    def test_closing_script_in_a_value_cannot_break_out_of_the_slot(self):
        hostile = 'x </script><img src=x onerror=alert(1)>'
        out = rcr.inject(self.html, {"claude_session": hostile})
        self.assertNotIn("</script><img", out)
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            out, re.S)
        self.assertEqual(json.loads(match.group(1))["claude_session"], hostile)


class SourcePathTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.sources = [str(self.tmp / ".handoff" / "receipts" / "receipt-x.md"),
                        str(self.tmp / ".handoff" / "jobs" / "job-a" / "log.jsonl"),
                        "/somewhere/else/outside.jsonl"]
        self.payload = rcr.build_payload(
            {"fields": {"claude_session": "sid-1", "duration": "11min 01sec"}},
            [row("job-a", "codex", 100)],
            {"state": "measured", "usage": {c: None for c in rcr.COUNTERS},
             "models": [], "cost_usd": None},
            None, self.sources, self.tmp)

    def test_no_source_is_absolute_or_names_the_repo_root(self):
        for source in self.payload["sources"]:
            self.assertFalse(source.startswith("/"))
            self.assertNotIn(str(self.tmp), source)

    def test_a_path_outside_the_repo_keeps_only_its_basename(self):
        self.assertIn("outside.jsonl", self.payload["sources"])

    def test_markdown_carries_no_absolute_path(self):
        out = rcr.render_markdown(self.payload)
        self.assertNotIn(str(self.tmp), out)
        self.assertIn(".handoff/jobs/job-a/log.jsonl", out)

    def test_injected_html_payload_carries_no_absolute_path(self):
        html = rcr.inject(TEMPLATE.read_text(encoding="utf-8"), self.payload)
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            html, re.S)
        for source in json.loads(match.group(1))["sources"]:
            self.assertFalse(source.startswith("/"))
            self.assertNotIn(str(self.tmp), source)


import contextlib
import io


class CliTests(unittest.TestCase):
    """One job per backend, so the CLI path exercises the three-way partition."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        jobs = self.tmp / ".handoff" / "jobs"
        codex = jobs / "job-a"
        codex.mkdir(parents=True)
        (codex / "meta").write_text("backend=codex\nmodel=m\nrole=r\nlabel=l\n")
        (codex / "exit_code").write_text("0\n")
        (codex / "log.jsonl").write_text(json.dumps(CODEX_TURN) + "\n")
        claude = jobs / "job-b"
        claude.mkdir(parents=True)
        (claude / "meta").write_text("backend=claude\nmodel=opus\nrole=r\nlabel=l\n")
        (claude / "exit_code").write_text("0\n")
        (claude / "log.jsonl").write_text(json.dumps(CLAUDE_RESULT) + "\n")
        copilot = jobs / "job-c"
        copilot.mkdir(parents=True)
        (copilot / "meta").write_text(
            "backend=copilot\nmodel=mai-code-1.1-flash\nrole=fast_worker\nlabel=l\n")
        (copilot / "exit_code").write_text("0\n")
        (copilot / "log.jsonl").write_text(json.dumps(COPILOT_DENIAL) + "\n")
        (copilot / "usage.json").write_text(json.dumps(COPILOT_PARENT_USAGE))
        self.receipts = self.tmp / ".handoff" / "receipts"
        self.receipts.mkdir(parents=True)
        self.text = receipt_text(
            codex_jobs="1", codex_job_durations="job-a=1min 00sec",
            cc_jobs="1", cc_job_durations="job-b=2min 00sec",
            copilot_jobs="1", copilot_job_durations="job-c=3min 00sec",
            roles_used='[{"role":"fast_worker","host":"copilot",'
                       '"model":"mai-code-1.1-flash","effort":"medium","verified":true}]')

    def write(self, name):
        path = self.receipts / name
        path.write_text(self.text)
        return path

    def run_cli(self, *args):
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            code = rcr.main([*args, "--repo", str(self.tmp), "--no-open"])
        return code, err.getvalue()

    def test_last_picks_the_greatest_stamp_not_the_newest_mtime(self):
        old = self.write("receipt-20260908T100000Z.md")
        new = self.write("receipt-20260908T155001Z.md")
        Path(old).touch()  # older stamp, newer mtime
        self.assertEqual(rcr.resolve_receipt(self.tmp, None), new)

    def test_explicit_missing_path_exits_two(self):
        code, err = self.run_cli(str(self.tmp / "nope.md"))
        self.assertEqual(code, 2)
        self.assertIn("no such receipt file", err)

    def test_writes_both_outputs_named_for_the_stamp(self):
        self.write("receipt-20260908T155001Z.md")
        code, _ = self.run_cli()
        self.assertEqual(code, 0)
        out = self.tmp / ".handoff" / "cost-receipts"
        self.assertTrue((out / "cost-receipt-20260908T155001Z.md").is_file())
        self.assertTrue((out / "cost-receipt-20260908T155001Z.html").is_file())

    def test_arbitrary_input_is_named_for_its_basename(self):
        other = self.tmp / "notes.md"
        other.write_text(self.text)
        self.assertEqual(self.run_cli(str(other))[0], 0)
        self.assertTrue((self.tmp / ".handoff" / "cost-receipts" / "cost-receipt-notes.md").is_file())

    def test_no_receipt_and_no_selector_exits_two(self):
        code, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("`make-receipt.py --save`", err)

    def test_invalid_receipt_exits_two_and_writes_nothing(self):
        self.text = receipt_text(receipt_schema_version="4")
        self.write("receipt-20260908T155001Z.md")
        code, err = self.run_cli()
        self.assertEqual(code, 2)
        self.assertIn("schema_version", err)
        self.assertFalse((self.tmp / ".handoff" / "cost-receipts").exists())

    def test_rerun_overwrites_in_place(self):
        self.write("receipt-20260908T155001Z.md")
        self.run_cli()
        out = self.tmp / ".handoff" / "cost-receipts" / "cost-receipt-20260908T155001Z.md"
        first = out.read_text()
        self.run_cli()
        self.assertIn("Handoff Cost Receipt", out.read_text())
        self.assertEqual(first.splitlines()[0], out.read_text().splitlines()[0])

    def payload_from_html(self):
        html = (self.tmp / ".handoff" / "cost-receipts" / "cost-receipt-20260908T155001Z.html").read_text()
        match = re.search(
            r'<script id="handoff-payload" type="application/json">(.*?)</script>',
            html, re.S)
        return json.loads(match.group(1))

    def test_html_output_carries_the_payload(self):
        self.write("receipt-20260908T155001Z.md")
        self.run_cli()
        self.assertEqual(self.payload_from_html()["jobs"][0]["job_id"], "job-a")

    def test_a_mixed_three_backend_run_renders(self):
        self.write("receipt-20260908T155001Z.md")
        self.assertEqual(self.run_cli()[0], 0)
        payload = self.payload_from_html()
        self.assertEqual([j["backend"] for j in payload["jobs"]],
                         ["codex", "claude", "copilot"])
        copilot = payload["jobs"][2]
        self.assertEqual(copilot["premium_requests"], 1)
        self.assertEqual(copilot["nano_aiu"], 84828000)
        self.assertEqual(copilot["denials"], 1)
        self.assertEqual(payload["summary"]["copilot_credits"]["nano_aiu"]["value"],
                         84828000)
        # The copilot job contributes no USD; the figure is the claude job's.
        self.assertEqual(payload["summary"]["outside_driver"]["cost_usd"],
                         6.633623999999999)

    def test_markdown_of_a_mixed_run_names_the_credit_meter(self):
        self.write("receipt-20260908T155001Z.md")
        self.run_cli()
        out = (self.tmp / ".handoff" / "cost-receipts"
               / "cost-receipt-20260908T155001Z.md").read_text()
        self.assertIn("Copilot AI-credit meter", out)
        self.assertIn("84,828,000", out)
        self.assertIn("claude-backed and copilot-backed jobs", out)


if __name__ == "__main__":
    unittest.main()
