"""Tests for scripts/migrate-sessions.py.

Every tree is built in a temp dir, with a fake --projects-root. Nothing here
touches a real repo or ~/.claude. Each test that runs the script sets or
removes CLAUDE_CODE_SESSION_ID itself (`env=` for a subprocess, `patch.dict`
in process), so the result is the same inside Claude Code and in CI.
"""

from __future__ import annotations

import builtins
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
SCRIPT = SCRIPTS / "migrate-sessions.py"

if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
import handoff_runtime  # noqa: E402

SPEC = importlib.util.spec_from_file_location("migrate_sessions", SCRIPT)
mig = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = mig
SPEC.loader.exec_module(mig)

SESSION_ENV = handoff_runtime.SESSION_ENV
VALIDATOR = handoff_runtime._validator()

S1 = "20260101T010101Z"
S2 = "20260102T020202Z"
S3 = "20260103T030303Z"
S4 = "20260104T040404Z"
S9 = "20260109T090909Z"  # a stamp no receipt has

COST_NONE = "# Handoff Cost Receipt\n\n- Session: `none`\n- Run duration: 1min 00sec\n"


def receipt_text(session: str, schema: int = 8, jobs=(), footer: str = "") -> str:
    """A receipt block in the shape of schema 3 to 8. The migration never
    validates it, so older shapes only need to be what extract_block reads."""
    durations = "; ".join(f"{job}=1min 00sec" for job in jobs) or "none"
    lines = [
        "[Handoff session receipt]",
        "phase: review",
        f"claude_session: {session}",
        "duration: 12min 00sec",
        "checks: unit",
        "anomalies: none",
    ]
    if schema >= 4:
        lines += [f"codex_jobs: {len(jobs)}", f"codex_job_durations: {durations}",
                  "cc_jobs: 0", "cc_job_durations: none",
                  "copilot_jobs: 0", "copilot_job_durations: none"]
    if schema >= 7:
        lines += ["cursor_jobs: 0", "cursor_job_durations: none"]
    if schema >= 8:
        lines += ["antigravity_jobs: 0", "antigravity_job_durations: none"]
    if schema >= 4:
        lines += ["scope: project", "config_source: project", "roles_used: none"]
    lines.append(f"receipt_schema_version: {schema}")
    return "# Saved receipt\n\n" + "\n".join(lines) + "\n" + footer


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def dated(session: str, stamp: str) -> str:
    """The session folder a receipt stamped `stamp` creates: dated by that stamp, `none` bare."""
    return session if session == "none" else f"{stamp[:8]}_{session}"


def snapshot(root: Path) -> dict:
    """Every entry under root: kind plus content hash or link target."""
    found = {}
    for dirpath, dirnames, filenames in os.walk(root):
        for name in dirnames + filenames:
            path = Path(dirpath) / name
            rel = path.relative_to(root).as_posix()
            if path.is_symlink():
                found[rel] = ("link", os.readlink(path))
            elif path.is_dir():
                found[rel] = ("dir",)
            else:
                found[rel] = ("file", sha(path))
    return found


def by_action(output: str) -> dict:
    kinds = {"move": [], "rewrite": [], "skip": [], "leave": [], "delete": []}
    for line in output.splitlines():
        kind = line.split(" ", 1)[0]
        if kind in kinds:
            kinds[kind].append(line)
    return kinds


class MigrationCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.repo = self.tmp / "repo"
        self.handoff = self.repo / ".handoff"
        self.handoff.mkdir(parents=True)
        self.projects = self.tmp / "projects"
        self.projects.mkdir()
        self.outside = self.tmp / "outside"
        self.outside.mkdir()
        # tempfile paths sit under /var, which resolves to /private/var on macOS
        self.real = self.repo.resolve()

    # --- builders ---

    def put(self, rel: str, text="x") -> Path:
        path = self.handoff / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            path.write_bytes(text)
        else:
            path.write_text(text, encoding="utf-8")
        return path

    def receipt(self, folder: str, stamp: str, session: str, schema: int = 8,
                jobs=(), footer: str = "") -> Path:
        return self.put(f"{folder}/receipt-{stamp}.md",
                        receipt_text(session, schema, jobs, footer))

    def transcript_file(self, project: str, session: str, *lines: str) -> Path:
        path = self.projects / project / f"{session}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            "".join(json.dumps({"type": "user", "message": line}) + "\n" for line in lines),
            encoding="utf-8")
        return path

    def saved_line(self, stamp: str) -> str:
        return f"saved: {self.real}/.handoff/receipts/receipt-{stamp}.md\n"

    # --- running ---

    def env(self, session=None) -> dict:
        env = {key: value for key, value in os.environ.items() if key != SESSION_ENV}
        if session:
            env[SESSION_ENV] = session
        return env

    def run_script(self, *args, session=None, apply=False, cwd=None, repo=True):
        command = [sys.executable, str(SCRIPT)]
        if repo:
            command += ["--repo", str(self.repo)]
        command += ["--projects-root", str(self.projects), *args]
        if apply:
            command.append("--apply")
        return subprocess.run(command, capture_output=True, text=True,
                              env=self.env(session), cwd=cwd)

    def in_process(self, *argv, session=None):
        """main() in this process, with the session variable set or removed."""
        out, err = io.StringIO(), io.StringIO()
        with unittest.mock.patch.dict(os.environ):
            os.environ.pop(SESSION_ENV, None)
            if session:
                os.environ[SESSION_ENV] = session
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = mig.main(["--repo", str(self.repo),
                                 "--projects-root", str(self.projects), *argv])
        return code, out.getvalue(), err.getvalue()

    def sessions_files(self) -> list:
        root = self.handoff / "sessions"
        if not root.exists():
            return []
        return sorted(p.relative_to(root).as_posix() for p in root.rglob("*")
                      if p.is_file() or p.is_symlink())

    def session_folders(self, session: str) -> list:
        """The folder names under sessions/ that belong to `session`."""
        root = self.handoff / "sessions"
        if not root.is_dir():
            return []
        return sorted(p.name for p in root.iterdir() if handoff_runtime.folder_session(p.name) == session)

    def legacy_files(self) -> list:
        found = []
        for name in ("receipts", "cost-receipts", "transcripts"):
            base = self.handoff / name
            if base.exists():
                found += [p.relative_to(self.handoff).as_posix() for p in base.rglob("*")
                          if p.is_file() or p.is_symlink()]
        return sorted(found)

    def rendered(self, receipt: Path) -> str:
        script = (SCRIPTS / "render-cost-receipt.py").resolve()
        return (f"re-render: python3 {shlex.quote(str(script))} {shlex.quote(str(receipt))} "
                f"--repo {shlex.quote(str(self.real))} --no-open")


# --- the main conversion -----------------------------------------------------


class LayoutTests(MigrationCase):
    def build_legacy_tree(self):
        self.receipt("receipts", S1, "s-one", 6, jobs=["job-a1"])
        self.receipt("receipts", S2, "s-one", 7, jobs=["job-a2"])
        self.receipt("receipts", S3, "s-two", 8, jobs=["job-b1"])
        self.put(f"receipts/receipt-{S3}-cost.md", "stray cost note")
        self.put(f"receipts/diagram/receipt-{S1}_handoff-session-timeline.svg", "<svg 1/>")
        self.put(f"receipts/diagram/receipt-{S2}-handoff-session-timeline.svg", "<svg 2/>")
        self.put(f"receipts/diagram/receipt-{S3}.svg", "<svg 3/>")
        self.put(f"receipts/diagram/receipt-{S3}_handoff-session-timeline@2x.png", b"\x89PNG")
        self.put(f"receipts/diagram/receipt-{S9}_handoff-session-timeline.svg", "<svg orphan/>")
        self.put("receipts/diagram/.DS_Store", b"junk")
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost 1 md")
        self.put(f"cost-receipts/cost-receipt-{S1}.html", "cost 1 html")
        self.put(f"cost-receipts/cost-receipt-{S3}.md", "cost 3 md")
        self.put(f"cost-receipts/cost-receipt-{S9}.md", "cost orphan md")
        self.put("transcripts/job-a1.html", "transcript a1")
        self.put("transcripts/job-b1.html", "transcript b1")
        self.put("transcripts/job-orphan.html", "transcript orphan")

    def test_legacy_tree_becomes_expected_sessions_tree(self):
        self.build_legacy_tree()
        # s-one has receipts on two dates (S1, S2) and so one folder, named for the earlier.
        one, two = dated("s-one", S1), dated("s-two", S3)
        moved_from = {
            f"receipts/receipt-{S1}.md": f"sessions/{one}/receipt-{S1}.md",
            f"receipts/receipt-{S2}.md": f"sessions/{one}/receipt-{S2}.md",
            f"receipts/receipt-{S3}.md": f"sessions/{two}/receipt-{S3}.md",
            f"receipts/diagram/receipt-{S1}_handoff-session-timeline.svg":
                f"sessions/{one}/diagram/session-timeline-{S1}.svg",
            f"receipts/diagram/receipt-{S2}-handoff-session-timeline.svg":
                f"sessions/{one}/diagram/session-timeline-{S2}.svg",
            f"receipts/diagram/receipt-{S3}.svg":
                f"sessions/{two}/diagram/session-timeline-{S3}.svg",
            f"receipts/diagram/receipt-{S3}_handoff-session-timeline@2x.png":
                f"sessions/{two}/diagram/session-timeline-{S3}@2x.png",
            f"receipts/diagram/receipt-{S9}_handoff-session-timeline.svg":
                f"sessions/none/diagram/session-timeline-{S9}.svg",
            f"cost-receipts/cost-receipt-{S1}.md": f"sessions/{one}/cost-receipt-{S1}.md",
            f"cost-receipts/cost-receipt-{S1}.html": f"sessions/{one}/cost-receipt-{S1}.html",
            f"cost-receipts/cost-receipt-{S3}.md": f"sessions/{two}/cost-receipt-{S3}.md",
            f"cost-receipts/cost-receipt-{S9}.md": f"sessions/none/cost-receipt-{S9}.md",
            "transcripts/job-a1.html": f"sessions/{one}/transcript-job-a1.html",
            "transcripts/job-b1.html": f"sessions/{two}/transcript-job-b1.html",
            "transcripts/job-orphan.html": "sessions/none/transcript-job-orphan.html",
        }
        before = {rel: (sha(self.handoff / rel), (self.handoff / rel).stat().st_ino)
                  for rel in moved_from}

        result = self.run_script(apply=True)

        # the stray file is left, so the run reports it
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual(sorted(moved_from.values()),
                         sorted(f"sessions/{rel}" for rel in self.sessions_files()))
        for src, dst in moved_from.items():
            self.assertFalse((self.handoff / src).exists(), src)
            # same bytes and the same inode: a move is a link and an unlink, never a copy
            self.assertEqual(before[src], (sha(self.handoff / dst),
                                           (self.handoff / dst).stat().st_ino), dst)
        self.assertEqual([f"receipts/receipt-{S3}-cost.md"], self.legacy_files())
        self.assertFalse((self.handoff / "receipts/diagram").exists())
        self.assertFalse((self.handoff / "cost-receipts").exists())
        self.assertFalse((self.handoff / "transcripts").exists())
        kinds = by_action(result.stdout)
        self.assertEqual(15, len(kinds["move"]))
        self.assertEqual(1, len(kinds["leave"]))
        self.assertIn(f"leave {self.real}/.handoff/receipts/receipt-{S3}-cost.md: unrecognised",
                      kinds["leave"])
        self.assertEqual(1, len(kinds["delete"]))
        self.assertIn(f"move {self.real}/.handoff/receipts/receipt-{S1}.md -> "
                      f"{self.real}/.handoff/sessions/{one}/receipt-{S1}.md", kinds["move"])
        # Two sessions and `none`: one folder each, and none of them for the later date.
        self.assertEqual([one], self.session_folders("s-one"))
        self.assertEqual([two], self.session_folders("s-two"))
        self.assertEqual(sorted([one, two, "none"]),
                         sorted(p.name for p in (self.handoff / "sessions").iterdir()))

    def test_clean_legacy_tree_exits_zero_and_removes_the_empty_folders(self):
        self.receipt("receipts", S1, "s-one", jobs=["job-a1"])
        self.put(f"receipts/diagram/receipt-{S1}_handoff-session-timeline.svg", "<svg/>")
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        self.put("transcripts/job-a1.html", "t")
        self.put("receipts/.DS_Store", b"junk")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for name in ("receipts", "cost-receipts", "transcripts"):
            self.assertFalse((self.handoff / name).exists(), name)
        self.assertEqual(4, len(self.sessions_files()))

    def test_schema_3_to_8_receipts_all_move_and_validate_is_never_called(self):
        for stamp, schema in ((S1, 3), (S2, 5), (S3, 6), (S4, 8)):
            self.receipt("receipts", stamp, f"s-{schema}", schema)
        with unittest.mock.patch.object(
                VALIDATOR, "validate", side_effect=AssertionError("validate() was called")):
            code, out, _ = self.in_process("--apply")
        self.assertEqual(0, code, out)
        self.assertEqual([f"{dated('s-3', S1)}/receipt-{S1}.md", f"{dated('s-5', S2)}/receipt-{S2}.md",
                          f"{dated('s-6', S3)}/receipt-{S3}.md", f"{dated('s-8', S4)}/receipt-{S4}.md"],
                         self.sessions_files())

    def test_receipt_that_is_not_stamp_named_is_left(self):
        self.put("receipts/notes.md", receipt_text("s-one"))
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode)
        self.assertIn(f"leave {self.real}/.handoff/receipts/notes.md: unrecognised", result.stdout)
        self.assertTrue((self.handoff / "receipts/notes.md").is_file())

    def test_unusable_claude_session_stays_and_keeps_its_dependents(self):
        self.receipt("receipts", S1, "../x", jobs=["job-a1"])
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        self.put(f"receipts/diagram/receipt-{S1}.svg", "<svg/>")
        self.put("transcripts/job-a1.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode)
        self.assertEqual([], self.sessions_files())
        self.assertEqual(4, len(self.legacy_files()))
        self.assertEqual(4, len(by_action(result.stdout)["skip"]), result.stdout)
        self.assertFalse((self.handoff / "x").exists())
        self.assertFalse((self.repo / "x").exists())

    def test_receipt_without_a_block_stays_and_keeps_its_dependents(self):
        self.put(f"receipts/receipt-{S1}.md", "no block in here\n")
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode)
        self.assertEqual([], self.sessions_files())
        self.assertEqual(2, len(by_action(result.stdout)["skip"]), result.stdout)

    def test_cost_receipt_and_diagram_with_unmapped_stamp_go_to_none(self):
        self.put(f"cost-receipts/cost-receipt-{S9}.html", "cost")
        self.put(f"receipts/diagram/receipt-{S9}_handoff-session-timeline@2x.png", b"\x89PNG")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([f"none/cost-receipt-{S9}.html",
                          f"none/diagram/session-timeline-{S9}@2x.png"], self.sessions_files())

    def test_a_stamp_in_two_sessions_is_ambiguous_and_keeps_its_dependents(self):
        self.receipt("receipts", S1, "s-one")
        self.receipt(f"sessions/{dated('s-two', S1)}", S1, "s-two")
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        self.put(f"receipts/diagram/receipt-{S1}.svg", "<svg/>")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode)
        self.assertEqual(2, len(by_action(result.stdout)["skip"]), result.stdout)
        self.assertIn("s-two", result.stdout)
        self.assertEqual([f"cost-receipts/cost-receipt-{S1}.md",
                          f"receipts/diagram/receipt-{S1}.svg"], self.legacy_files())


# --- recovery of a none receipt ----------------------------------------------


class RecoveryTests(MigrationCase):
    def none_receipt(self, stamp=S1, jobs=("job-n1",), schema=8, footer=""):
        path = self.receipt("receipts", stamp, "none", schema, jobs, footer)
        self.put(f"cost-receipts/cost-receipt-{stamp}.md", COST_NONE)
        self.put(f"receipts/diagram/receipt-{stamp}_handoff-session-timeline.svg", "<svg/>")
        for job in jobs:
            self.put(f"transcripts/{job}.html", "t")
        return path

    def test_one_matching_session_rewrites_the_receipt_and_files_it_there(self):
        footer = "\nclaude_session: none\n"  # outside the block, must survive untouched
        source = self.none_receipt(footer=footer)
        original = source.read_text(encoding="utf-8")
        match = self.transcript_file("proj-a", "sess-A", "hello", self.saved_line(S1))
        self.transcript_file("proj-a", "sess-B", "no mention here")

        result = self.run_script(apply=True)

        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        target = self.handoff / f"sessions/{dated('sess-A', S1)}/receipt-{S1}.md"
        new = target.read_text(encoding="utf-8")
        old_lines, new_lines = original.splitlines(), new.splitlines()
        self.assertEqual(len(old_lines), len(new_lines))
        changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
        self.assertEqual([("claude_session: none", "claude_session: sess-A")], changed)
        self.assertTrue(new.endswith(footer))
        old_block = VALIDATOR.extract_block(original)
        new_block = VALIDATOR.extract_block(new)
        self.assertEqual({**old_block, "claude_session": "sess-A"}, new_block)
        self.assertEqual("8", new_block["receipt_schema_version"])
        self.assertEqual([], VALIDATOR.validate(new_block))
        self.assertFalse(source.exists())
        self.assertIn(f"rewrite {self.real}/.handoff/receipts/receipt-{S1}.md: "
                      f"claude_session none -> sess-A (matched {match})", result.stdout)
        # the dependents follow the recovered session, not none
        sess_a = dated("sess-A", S1)
        self.assertEqual([f"{sess_a}/cost-receipt-{S1}.md",
                          f"{sess_a}/diagram/session-timeline-{S1}.svg",
                          f"{sess_a}/receipt-{S1}.md",
                          f"{sess_a}/transcript-job-n1.html"], self.sessions_files())
        # the cost receipt was rendered while the receipt said none
        self.assertIn(self.rendered(target.resolve()), result.stdout)

    def test_zero_matches_leaves_the_receipt_untouched_under_none(self):
        source = self.none_receipt()
        original = source.read_bytes()
        self.transcript_file("proj-a", "sess-A", "nothing relevant")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(original, (self.handoff / f"sessions/none/receipt-{S1}.md").read_bytes())
        self.assertEqual(0, len(by_action(result.stdout)["rewrite"]))
        self.assertEqual([f"none/cost-receipt-{S1}.md",
                          f"none/diagram/session-timeline-{S1}.svg",
                          f"none/receipt-{S1}.md",
                          "none/transcript-job-n1.html"], self.sessions_files())
        self.assertNotIn("re-render:", result.stdout)

    def test_two_matching_sessions_leave_the_receipt_untouched_under_none(self):
        source = self.none_receipt()
        original = source.read_bytes()
        self.transcript_file("proj-a", "sess-A", self.saved_line(S1))
        self.transcript_file("proj-b", "sess-B", self.saved_line(S1))
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(original, (self.handoff / f"sessions/none/receipt-{S1}.md").read_bytes())
        self.assertEqual(0, len(by_action(result.stdout)["rewrite"]))

    def test_match_only_in_the_current_session_file_is_excluded(self):
        source = self.none_receipt()
        original = source.read_bytes()
        self.transcript_file("proj-a", "sess-current", self.saved_line(S1))
        result = self.run_script(apply=True, session="sess-current")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(original, (self.handoff / f"sessions/none/receipt-{S1}.md").read_bytes())
        self.assertEqual([], self.session_folders("sess-current"))

    def test_current_session_file_is_dropped_before_distinct_sessions_are_counted(self):
        self.none_receipt()
        self.transcript_file("proj-a", "sess-current", self.saved_line(S1))
        self.transcript_file("proj-a", "sess-other", self.saved_line(S1))
        result = self.run_script(apply=True, session="sess-current")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.handoff / f"sessions/{dated('sess-other', S1)}/receipt-{S1}.md").is_file())

    def test_transcript_mentioning_the_path_without_the_saved_prefix_is_not_a_match(self):
        source = self.none_receipt()
        original = source.read_bytes()
        self.transcript_file(
            "proj-a", "sess-A",
            f"I looked at {self.real}/.handoff/receipts/receipt-{S1}.md and it was fine",
            f"cat {self.real}/.handoff/receipts/receipt-{S1}.md")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(original, (self.handoff / f"sessions/none/receipt-{S1}.md").read_bytes())

    def test_saved_line_for_another_repo_is_not_a_match(self):
        self.none_receipt()
        self.transcript_file(
            "proj-a", "sess-A",
            f"saved: {self.tmp}/elsewhere/.handoff/receipts/receipt-{S1}.md\n")
        result = self.run_script(apply=True)
        self.assertEqual(0, len(by_action(result.stdout)["rewrite"]), result.stdout)

    def test_same_session_id_in_two_project_folders_counts_once(self):
        self.none_receipt()
        self.transcript_file("proj-a", "sess-A", self.saved_line(S1))
        self.transcript_file("proj-b", "sess-A", self.saved_line(S1))
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.handoff / f"sessions/{dated('sess-A', S1)}/receipt-{S1}.md").is_file())

    def test_a_matching_stem_that_is_not_a_session_id_is_not_used(self):
        self.none_receipt()
        self.transcript_file("proj-a", "bad stem", self.saved_line(S1))
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(0, len(by_action(result.stdout)["rewrite"]))
        self.assertTrue((self.handoff / f"sessions/none/receipt-{S1}.md").is_file())

    def test_each_projects_file_is_read_once_for_all_none_receipts(self):
        for stamp in (S1, S2, S3):
            self.none_receipt(stamp, jobs=())
        a = self.transcript_file("proj-a", "sess-A", self.saved_line(S1), self.saved_line(S2))
        b = self.transcript_file("proj-a", "sess-B", self.saved_line(S3))
        real_open = builtins.open
        opened = []

        def spy(file, *args, **kwargs):
            if str(file).endswith(".jsonl"):
                opened.append(str(file))
            return real_open(file, *args, **kwargs)

        with unittest.mock.patch("builtins.open", spy):
            code, out, _ = self.in_process("--apply")
        self.assertEqual(0, code, out)
        self.assertEqual(sorted([str(a), str(b)]), sorted(opened))
        # sess-A holds receipts of two dates and still gets one folder, named for the earlier.
        self.assertEqual([f"{dated('sess-A', S1)}/receipt-{S1}.md", f"{dated('sess-A', S1)}/receipt-{S2}.md",
                          f"{dated('sess-B', S3)}/receipt-{S3}.md"],
                         [p for p in self.sessions_files() if "/receipt-" in p])

    def test_unreadable_projects_file_withholds_recovery(self):
        source = self.none_receipt()
        original = source.read_bytes()
        self.transcript_file("proj-a", "sess-A", self.saved_line(S1))
        blocked = self.transcript_file("proj-a", "sess-B", "x")
        blocked.chmod(0)
        self.addCleanup(blocked.chmod, 0o644)
        if os.access(blocked, os.R_OK):
            self.skipTest("running as a user that can read a mode 000 file")
        result = self.run_script(apply=True)
        # one session matched, but a file that cannot be read could hold a second
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual(original, source.read_bytes())
        self.assertEqual([], self.sessions_files())

    def test_rewrite_is_in_the_dry_run_plan_and_writes_nothing(self):
        self.none_receipt()
        self.transcript_file("proj-a", "sess-A", self.saved_line(S1))
        before = snapshot(self.handoff)
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1, len(by_action(result.stdout)["rewrite"]))
        self.assertEqual(before, snapshot(self.handoff))


# --- dry run and re-run -------------------------------------------------------


class DryRunAndRerunTests(MigrationCase):
    def build(self):
        self.receipt("receipts", S1, "s-one", 6, jobs=["job-a1"])
        self.receipt("receipts", S2, "none", 7, jobs=["job-n2"])
        self.put(f"receipts/receipt-{S3}-cost.md", "stray")
        self.put(f"receipts/diagram/receipt-{S1}_handoff-session-timeline.svg", "<svg/>")
        self.put("receipts/diagram/.DS_Store", b"junk")
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        self.put("transcripts/job-a1.html", "t")
        self.put("transcripts/job-n2.html", "t")
        self.put("transcripts/job-orphan.html", "t")
        self.transcript_file("proj-a", "sess-A", self.saved_line(S2))

    def test_dry_run_writes_nothing(self):
        self.build()
        before = snapshot(self.handoff)
        result = self.run_script()
        self.assertEqual(before, snapshot(self.handoff))
        self.assertEqual(1, result.returncode)  # the stray is left
        kinds = by_action(result.stdout)
        self.assertEqual((6, 1, 0, 1, 1),
                         (len(kinds["move"]), len(kinds["rewrite"]), len(kinds["skip"]),
                          len(kinds["leave"]), len(kinds["delete"])), result.stdout)
        self.assertFalse((self.handoff / "sessions").exists())

    def test_dry_run_of_a_clean_tree_exits_zero(self):
        self.receipt("receipts", S1, "s-one")
        before = snapshot(self.handoff)
        result = self.run_script()
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(before, snapshot(self.handoff))

    def test_second_apply_exits_zero_and_changes_nothing(self):
        self.receipt("receipts", S1, "s-one", jobs=["job-a1"])
        self.receipt("receipts", S2, "none", jobs=["job-n2"])
        self.put(f"cost-receipts/cost-receipt-{S2}.md", COST_NONE)
        self.put(f"receipts/diagram/receipt-{S1}.svg", "<svg/>")
        self.put("transcripts/job-a1.html", "t")
        self.put("transcripts/job-n2.html", "t")
        self.transcript_file("proj-a", "sess-A", self.saved_line(S2))
        first = self.run_script(apply=True)
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        after_first = snapshot(self.handoff)
        second = self.run_script(apply=True)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(after_first, snapshot(self.handoff))
        self.assertEqual([], [k for k, v in by_action(second.stdout).items() if v])

    def test_reminders_print_in_dry_run_and_apply_from_the_folders_alone(self):
        abc, none, dee = (f"sessions/{dated('abc', S1)}", "sessions/none", f"sessions/{dated('def', S3)}")
        receipt = self.receipt(abc, S1, "abc")
        self.put(f"{abc}/cost-receipt-{S1}.md", COST_NONE)
        self.put(f"{abc}/cost-receipt-{S1}.html", "<html/>")
        # not reminded: session none, a session that is named, and no receipt beside it
        self.receipt(none, S2, "none")
        self.put(f"{none}/cost-receipt-{S2}.md", COST_NONE)
        self.receipt(dee, S3, "def")
        self.put(f"{dee}/cost-receipt-{S3}.md", "# Handoff Cost Receipt\n\n- Session: `def`\n")
        self.put(f"{abc}/cost-receipt-{S4}.md", COST_NONE)
        # not reminded either: a bare-id folder and a dated `none` are not session folders
        for bare in ("sessions/bare-id", "sessions/20260105_none"):
            self.receipt(bare, S1, "abc")
            self.put(f"{bare}/cost-receipt-{S1}.md", COST_NONE)
        before = snapshot(self.handoff)

        dry = self.run_script()
        self.assertEqual(before, snapshot(self.handoff))
        applied = self.run_script(apply=True)

        for result in (dry, applied):
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
            lines = [l for l in result.stdout.splitlines() if l.startswith("re-render:")]
            self.assertEqual([self.rendered(receipt.resolve())], lines)

    def test_exit_code_is_zero_when_there_is_no_legacy_folder_at_all(self):
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)


# --- interruptions, each built as a partial tree ----------------------------


class InterruptionTests(MigrationCase):
    def test_receipts_moved_but_nothing_else_dependents_follow_the_migrated_receipts(self):
        one, two = dated("s-one", S1), dated("s-two", S3)
        self.receipt(f"sessions/{one}", S1, "s-one", jobs=["job-a1"])
        self.receipt(f"sessions/{two}", S3, "s-two", jobs=["job-b1"])
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        self.put(f"cost-receipts/cost-receipt-{S1}.html", "cost")
        self.put(f"receipts/diagram/receipt-{S3}_handoff-session-timeline.svg", "<svg/>")
        self.put("transcripts/job-a1.html", "t")
        self.put("transcripts/job-b1.html", "t")
        self.put("transcripts/job-orphan.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([f"{one}/cost-receipt-{S1}.html", f"{one}/cost-receipt-{S1}.md",
                          f"{one}/receipt-{S1}.md", f"{one}/transcript-job-a1.html",
                          f"{two}/diagram/session-timeline-{S3}.svg",
                          f"{two}/receipt-{S3}.md", f"{two}/transcript-job-b1.html",
                          "none/transcript-job-orphan.html"],
                         self.sessions_files())
        self.assertEqual([], self.legacy_files())

    def test_some_receipts_moved_and_some_still_legacy_all_dependents_land_correctly(self):
        one, two = dated("s-one", S1), dated("s-two", S2)
        self.receipt(f"sessions/{one}", S1, "s-one", jobs=["job-a1"])
        self.receipt("receipts", S2, "s-two", jobs=["job-b2"])
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "c1")
        self.put(f"cost-receipts/cost-receipt-{S2}.md", "c2")
        self.put("transcripts/job-a1.html", "t")
        self.put("transcripts/job-b2.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([f"{one}/cost-receipt-{S1}.md", f"{one}/receipt-{S1}.md",
                          f"{one}/transcript-job-a1.html",
                          f"{two}/cost-receipt-{S2}.md", f"{two}/receipt-{S2}.md",
                          f"{two}/transcript-job-b2.html"], self.sessions_files())

    def test_rewritten_receipt_already_moved_while_cost_receipt_is_legacy_prints_reminder(self):
        sess_a = dated("sess-A", S1)
        receipt = self.receipt(f"sessions/{sess_a}", S1, "sess-A", jobs=["job-n1"])
        self.put(f"cost-receipts/cost-receipt-{S1}.md", COST_NONE)
        self.put(f"cost-receipts/cost-receipt-{S1}.html", "<html/>")
        self.put("transcripts/job-n1.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([f"{sess_a}/cost-receipt-{S1}.html", f"{sess_a}/cost-receipt-{S1}.md",
                          f"{sess_a}/receipt-{S1}.md", f"{sess_a}/transcript-job-n1.html"],
                         self.sessions_files())
        self.assertIn(self.rendered(receipt.resolve()), result.stdout)

    def test_cost_receipt_moved_while_transcripts_are_still_legacy(self):
        one = dated("s-one", S1)
        self.receipt(f"sessions/{one}", S1, "s-one", jobs=["job-a1", "job-a1b"])
        self.put(f"sessions/{one}/cost-receipt-{S1}.md", "c")
        self.put("transcripts/job-a1.html", "t")
        self.put("transcripts/job-a1b.html", "t")
        self.put("transcripts/job-stray.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual([f"{one}/cost-receipt-{S1}.md", f"{one}/receipt-{S1}.md",
                          f"{one}/transcript-job-a1.html", f"{one}/transcript-job-a1b.html",
                          "none/transcript-job-stray.html"],
                         self.sessions_files())
        self.assertEqual([], self.legacy_files())

    def test_interrupted_between_link_and_unlink_leaves_a_duplicate_that_is_reported(self):
        # The receipt was linked into place and the process died before the unlink.
        source = self.receipt("receipts", S1, "s-one", jobs=["job-a1"])
        target = self.handoff / f"sessions/{dated('s-one', S1)}/receipt-{S1}.md"
        target.parent.mkdir(parents=True)
        os.link(source, target)
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "c")
        self.put("transcripts/job-a1.html", "t")
        result = self.run_script(apply=True)
        # a file at the target is never taken as proof, so nothing is dropped or overwritten
        self.assertEqual(1, result.returncode)
        self.assertTrue(source.is_file())
        self.assertTrue(target.is_file())
        self.assertEqual(["cost-receipts/cost-receipt-" + S1 + ".md",
                          f"receipts/receipt-{S1}.md", "transcripts/job-a1.html"],
                         self.legacy_files())


# --- conflicts ----------------------------------------------------------------


class ConflictTests(MigrationCase):
    def build_conflict(self, make_target):
        self.receipt("receipts", S1, "s-one", jobs=["job-a1", "job-shared"])
        self.receipt("receipts", S2, "s-two", jobs=["job-shared"])
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost 1")
        self.put(f"cost-receipts/cost-receipt-{S1}.html", "cost 1")
        self.put(f"receipts/diagram/receipt-{S1}_handoff-session-timeline.svg", "<svg/>")
        self.put("transcripts/job-a1.html", "t-a1")
        self.put("transcripts/job-shared.html", "t-shared")
        make_target(self.handoff / f"sessions/{dated('s-one', S1)}/receipt-{S1}.md")

    def assert_source_and_dependents_stayed(self, result):
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual(
            [f"cost-receipts/cost-receipt-{S1}.html", f"cost-receipts/cost-receipt-{S1}.md",
             f"receipts/diagram/receipt-{S1}_handoff-session-timeline.svg",
             f"receipts/receipt-{S1}.md", "transcripts/job-a1.html"],
            self.legacy_files())
        # the other receipt is newer and owns job-shared, so that transcript still moves
        two = dated("s-two", S2)
        self.assertEqual([f"{two}/receipt-{S2}.md", f"{two}/transcript-job-shared.html"],
                         [p for p in self.sessions_files() if p.startswith(two)])
        self.assertEqual(5, len(by_action(result.stdout)["skip"]), result.stdout)

    def test_existing_target_keeps_the_source_and_its_dependents(self):
        def occupy(target):
            target.parent.mkdir(parents=True)
            target.write_text("a different receipt\n", encoding="utf-8")
        self.build_conflict(occupy)
        source_before = (self.handoff / f"receipts/receipt-{S1}.md").read_bytes()
        result = self.run_script(apply=True)
        self.assert_source_and_dependents_stayed(result)
        target = self.handoff / f"sessions/{dated('s-one', S1)}/receipt-{S1}.md"
        self.assertEqual("a different receipt\n", target.read_text(encoding="utf-8"))
        self.assertEqual(source_before, (self.handoff / f"receipts/receipt-{S1}.md").read_bytes())

    def test_dangling_symlink_at_the_target_is_a_conflict(self):
        def dangle(target):
            target.parent.mkdir(parents=True)
            os.symlink(self.tmp / "does-not-exist", target)
        self.build_conflict(dangle)
        result = self.run_script(apply=True)
        self.assert_source_and_dependents_stayed(result)
        target = self.handoff / f"sessions/{dated('s-one', S1)}/receipt-{S1}.md"
        self.assertTrue(target.is_symlink())
        self.assertFalse((self.tmp / "does-not-exist").exists())

    def test_two_diagram_sources_mapping_to_one_target_second_is_a_conflict(self):
        self.receipt("receipts", S1, "s-one")
        first = self.put(f"receipts/diagram/receipt-{S1}-handoff-session-timeline.svg", "first")
        second = self.put(f"receipts/diagram/receipt-{S1}_handoff-session-timeline.svg", "second")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual("first", (self.handoff / f"sessions/{dated('s-one', S1)}"
                                   / f"diagram/session-timeline-{S1}.svg")
                         .read_text(encoding="utf-8"))
        self.assertFalse(first.exists())
        self.assertTrue(second.is_file())
        self.assertEqual("second", second.read_text(encoding="utf-8"))
        skips = by_action(result.stdout)["skip"]
        self.assertEqual(1, len(skips))
        self.assertIn(str(self.real / ".handoff/receipts/diagram" / second.name), skips[0])

    def test_target_that_appears_after_the_inventory_is_not_overwritten(self):
        self.receipt("receipts", S1, "s-one", jobs=["job-a1"])
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        self.put("transcripts/job-a1.html", "t")
        plan = mig.build_plan(self.real, self.projects, {})
        late = self.handoff / f"sessions/{dated('s-one', S1)}/receipt-{S1}.md"
        late.parent.mkdir(parents=True)
        late.write_text("arrived after the inventory\n", encoding="utf-8")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            failures = mig.apply_plan(plan)
        self.assertEqual("arrived after the inventory\n", late.read_text(encoding="utf-8"))
        self.assertTrue((self.handoff / f"receipts/receipt-{S1}.md").is_file())
        self.assertTrue(failures)
        # the receipt did not install, so what follows it stays too
        self.assertEqual([f"cost-receipts/cost-receipt-{S1}.md", f"receipts/receipt-{S1}.md",
                          "transcripts/job-a1.html"], self.legacy_files())
        self.assertEqual([f"{dated('s-one', S1)}/receipt-{S1}.md"], self.sessions_files())


# --- symlinks -----------------------------------------------------------------


class SymlinkTests(MigrationCase):
    def test_symlinked_source_is_left_and_reported(self):
        target = self.outside / "real-receipt.md"
        target.write_text(receipt_text("s-one"), encoding="utf-8")
        link = self.handoff / f"receipts/receipt-{S1}.md"
        link.parent.mkdir(parents=True)
        os.symlink(target, link)
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        (self.handoff / "transcripts").mkdir()
        os.symlink(target, self.handoff / "transcripts/job-l.html")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertTrue(link.is_symlink())
        self.assertTrue((self.handoff / "transcripts/job-l.html").is_symlink())
        self.assertEqual(receipt_text("s-one"), target.read_text(encoding="utf-8"))
        # a receipt that stays keeps its dependents with it
        self.assertTrue((self.handoff / f"cost-receipts/cost-receipt-{S1}.md").is_file())
        self.assertEqual([], self.sessions_files())
        self.assertEqual(3, len(by_action(result.stdout)["skip"]), result.stdout)

    def test_symlinked_sessions_folder_is_refused(self):
        self.receipt("receipts", S1, "s-one", jobs=["job-a1"])
        self.put(f"cost-receipts/cost-receipt-{S1}.md", "cost")
        self.put("transcripts/job-a1.html", "t")
        os.symlink(self.outside, self.handoff / "sessions")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual([], list(self.outside.iterdir()))
        self.assertEqual(3, len(self.legacy_files()))
        self.assertEqual(3, len(by_action(result.stdout)["skip"]), result.stdout)
        self.assertIn("symlink", result.stdout)

    def test_symlinked_session_folder_is_refused_for_that_session_only(self):
        self.receipt("receipts", S1, "s-one")
        self.receipt("receipts", S2, "s-two")
        (self.handoff / "sessions").mkdir()
        os.symlink(self.outside, self.handoff / f"sessions/{dated('s-one', S1)}")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual([], list(self.outside.iterdir()))
        self.assertTrue((self.handoff / f"sessions/{dated('s-two', S2)}/receipt-{S2}.md").is_file())
        self.assertTrue((self.handoff / f"receipts/receipt-{S1}.md").is_file())
        # Refused, not stepped around: no second folder was opened for the same session.
        self.assertEqual([dated("s-one", S1)], self.session_folders("s-one"))

    def test_a_dangling_symlink_named_for_a_session_is_refused_for_that_session(self):
        self.receipt("receipts", S1, "s-one")
        (self.handoff / "sessions").mkdir()
        os.symlink(self.tmp / "does-not-exist", self.handoff / f"sessions/{dated('s-one', S1)}")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.handoff / f"receipts/receipt-{S1}.md").is_file())
        self.assertFalse((self.tmp / "does-not-exist").exists())
        self.assertEqual([dated("s-one", S1)], self.session_folders("s-one"))

    def test_symlinked_legacy_folder_is_skipped_whole(self):
        (self.outside / "x.html").write_text("outside", encoding="utf-8")
        os.symlink(self.outside, self.handoff / "transcripts")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.handoff / "transcripts").is_symlink())
        self.assertEqual("outside", (self.outside / "x.html").read_text(encoding="utf-8"))


# --- the rewrite is narrow and checked ---------------------------------------


class RewriteSafetyTests(MigrationCase):
    def prepare(self, text=None):
        source = self.put(f"receipts/receipt-{S1}.md", text or receipt_text("none"))
        self.transcript_file("proj-a", "sess-A", self.saved_line(S1))
        return source

    def assert_nothing_written(self, source, original):
        self.assertEqual(original, source.read_bytes())
        self.assertEqual([], self.sessions_files())
        leftovers = [p for p in (self.handoff / "sessions").rglob("*") if p.is_file()] \
            if (self.handoff / "sessions").exists() else []
        self.assertEqual([], leftovers)

    def test_a_rewrite_whose_block_check_fails_leaves_the_source_byte_identical(self):
        source = self.prepare()
        original = source.read_bytes()

        def damaged(text, new_id):
            return text.replace("claude_session: none", f"claude_session: {new_id}") \
                       .replace("duration: 12min 00sec", "duration: 99min 00sec")

        with unittest.mock.patch.object(mig, "replace_session_line", damaged):
            code, out, _ = self.in_process("--apply")
        self.assertEqual(1, code, out)
        self.assertIn("FAILED", out)
        self.assert_nothing_written(source, original)

    def test_a_rewrite_that_changes_the_schema_version_is_refused(self):
        source = self.prepare()
        original = source.read_bytes()

        def bumped(text, new_id):
            return text.replace("claude_session: none", f"claude_session: {new_id}") \
                       .replace("receipt_schema_version: 8", "receipt_schema_version: 9")

        with unittest.mock.patch.object(mig, "replace_session_line", bumped):
            code, out, _ = self.in_process("--apply")
        self.assertEqual(1, code, out)
        self.assert_nothing_written(source, original)

    def test_two_none_lines_in_the_block_fail_the_rewrite_and_keep_the_source(self):
        text = receipt_text("none").replace(
            "claude_session: none\n", "claude_session: none\nclaude_session: none\n", 1)
        source = self.prepare(text)
        original = source.read_bytes()
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertIn("FAILED", result.stdout)
        self.assert_nothing_written(source, original)

    def test_a_failed_link_removes_the_temp_file_and_keeps_the_source(self):
        source = self.prepare()
        original = source.read_bytes()
        real_link = os.link

        def refuse_the_final_link(src, dst, *args, **kwargs):
            if Path(dst).name == f"receipt-{S1}.md":
                raise PermissionError("no links here")
            return real_link(src, dst, *args, **kwargs)

        with unittest.mock.patch.object(os, "link", refuse_the_final_link):
            code, out, _ = self.in_process("--apply")
        self.assertEqual(1, code, out)
        self.assert_nothing_written(source, original)

    def test_a_rewrite_never_overwrites_an_existing_target(self):
        source = self.prepare()
        original = source.read_bytes()
        target = self.handoff / f"sessions/{dated('sess-A', S1)}/receipt-{S1}.md"
        target.parent.mkdir(parents=True)
        target.write_text("already here\n", encoding="utf-8")
        result = self.run_script(apply=True)
        self.assertEqual(1, result.returncode, result.stdout + result.stderr)
        self.assertEqual(original, source.read_bytes())
        self.assertEqual("already here\n", target.read_text(encoding="utf-8"))
        self.assertEqual([f"{dated('sess-A', S1)}/receipt-{S1}.md"], self.sessions_files())

    def test_a_rewrite_target_that_appears_after_the_inventory_is_not_overwritten(self):
        source = self.prepare()
        original = source.read_bytes()
        plan = mig.build_plan(self.real, self.projects, {})
        self.assertEqual(1, len([a for a in plan.actions if a.kind == "rewrite"]))
        late = self.handoff / f"sessions/{dated('sess-A', S1)}/receipt-{S1}.md"
        late.parent.mkdir(parents=True)
        late.write_text("arrived after the inventory\n", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            failures = mig.apply_plan(plan)
        self.assertTrue(failures)
        self.assertEqual(original, source.read_bytes())
        self.assertEqual("arrived after the inventory\n", late.read_text(encoding="utf-8"))
        self.assertEqual([f"{dated('sess-A', S1)}/receipt-{S1}.md"], self.sessions_files())

    def test_replace_session_line_changes_only_the_one_block_line(self):
        text = receipt_text("none", footer="\nclaude_session: none\n")
        new = mig.replace_session_line(text, "sess-A")
        self.assertEqual(text.replace("claude_session: none\nduration", "claude_session: sess-A\nduration"), new)

    def test_replace_session_line_needs_exactly_one_none_line_inside_the_block(self):
        two = receipt_text("none").replace(
            "claude_session: none\n", "claude_session: none\nclaude_session: none\n", 1)
        with self.assertRaises(mig.RewriteError):
            mig.replace_session_line(two, "sess-A")
        # a none line outside the block does not count, and the block has none
        outside_only = receipt_text("abc", footer="\nclaude_session: none\n")
        with self.assertRaises(mig.RewriteError):
            mig.replace_session_line(outside_only, "sess-A")
        with self.assertRaises(mig.RewriteError):
            mig.replace_session_line("no block\nclaude_session: none\n", "sess-A")

    def test_replace_session_line_keeps_the_line_ending_and_other_bytes(self):
        text = receipt_text("none").replace("\n", "\r\n")
        new = mig.replace_session_line(text, "sess-A")
        self.assertIn("claude_session: sess-A\r\n", new)
        self.assertEqual(text.count("\r\n"), new.count("\r\n"))


# --- ownership ----------------------------------------------------------------


class OwnershipTests(MigrationCase):
    def test_newest_receipt_across_legacy_and_migrated_owns_the_transcript(self):
        self.receipt(f"sessions/{dated('s-one', S1)}", S1, "s-one", jobs=["job-x", "job-y"])
        self.receipt("receipts", S2, "s-two", jobs=["job-x"])
        self.receipt("receipts", S3, "s-three", jobs=["job-y"])
        self.receipt(f"sessions/{dated('s-four', S4)}", S4, "s-four", jobs=["job-y"])
        for job in ("job-x", "job-y"):
            self.put(f"transcripts/{job}.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.handoff / f"sessions/{dated('s-two', S2)}/transcript-job-x.html").is_file())
        self.assertTrue((self.handoff / f"sessions/{dated('s-four', S4)}/transcript-job-y.html").is_file())

    def test_migration_orders_owners_exactly_like_job_owner(self):
        # same stamp in two sessions: the greater session id wins, as in job_owner
        self.receipt("receipts", S1, "alpha", jobs=["job-tie", "job-old"])
        self.receipt(f"sessions/{dated('beta', S1)}", S1, "beta", jobs=["job-tie"])
        self.receipt("receipts", S2, "gamma", jobs=["job-old", "job-new"])
        self.receipt(f"sessions/{dated('delta', S3)}", S3, "delta", jobs=["job-new"])
        # a stamped receipt always outranks one whose name has no stamp
        self.put(f"sessions/{dated('aaa', S1)}/receipt-notes.md", receipt_text("aaa", jobs=["job-notes"]))
        self.receipt("receipts", S4, "zzz", jobs=["job-notes"])
        jobs = ["job-tie", "job-old", "job-new", "job-notes", "job-orphan"]
        for job in jobs:
            self.put(f"transcripts/{job}.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        for job in jobs:
            owner = handoff_runtime.job_owner(self.repo, job) or "none"
            landed = [p for p in self.sessions_files() if p.endswith(f"transcript-{job}.html")]
            # The owner is a session id; the transcript sits in that session's one folder.
            self.assertEqual([owner], [handoff_runtime.folder_session(p.split("/")[0]) for p in landed], job)
            self.assertEqual([f"{self.session_folders(owner)[0]}/transcript-{job}.html"], landed, job)
        self.assertTrue((self.handoff / f"sessions/{dated('beta', S1)}/transcript-job-tie.html").is_file())

    def test_a_blocked_receipt_wins_an_exact_tie_whatever_the_order(self):
        blocked = mig.Rec(S1, "s-one", {"job-x"}, blocked="target exists")
        free = mig.Rec(S1, "s-one", {"job-x"})
        newer = mig.Rec(S2, "s-two", {"job-x"})
        for recs in ([blocked, free], [free, blocked]):
            self.assertIs(blocked, mig.newest_owners(recs)["job-x"])
        # a newer receipt still beats an older one that is blocked
        self.assertIs(newer, mig.newest_owners([blocked, newer])["job-x"])
        self.assertIs(newer, mig.newest_owners([newer, blocked])["job-x"])

    def test_a_job_no_receipt_indexes_goes_to_none_not_the_current_session(self):
        self.put("transcripts/job-orphan.html", "t")
        result = self.run_script(apply=True, session="sess-current")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["none/transcript-job-orphan.html"], self.sessions_files())

    def test_a_receipt_that_cannot_be_read_holds_every_transcript(self):
        self.receipt(f"sessions/{dated('s-one', S1)}", S1, "s-one", jobs=["job-a1"])
        self.put("transcripts/job-a1.html", "t")
        real = Path.read_bytes

        def unreadable(path):
            if path.name == f"receipt-{S1}.md":
                raise PermissionError("denied")
            return real(path)

        with unittest.mock.patch.object(Path, "read_bytes", unreadable):
            code, out, _ = self.in_process("--apply")
        self.assertEqual(1, code, out)
        self.assertTrue((self.handoff / "transcripts/job-a1.html").is_file())
        self.assertEqual(1, len(by_action(out)["skip"]), out)

    def test_ownership_is_decided_before_anything_moves(self):
        self.receipt("receipts", S1, "none", jobs=["job-n1"])
        self.put("transcripts/job-n1.html", "t")
        self.put(f"cost-receipts/cost-receipt-{S1}.md", COST_NONE)
        self.transcript_file("proj-a", "sess-A", self.saved_line(S1))
        plan = mig.build_plan(self.real, self.projects, {})
        self.assertEqual({str(a.dst) for a in plan.actions if a.kind in ("move", "rewrite")},
                         {str(self.real / f".handoff/sessions/{dated('sess-A', S1)}/{name}") for name in
                          (f"receipt-{S1}.md", f"cost-receipt-{S1}.md", "transcript-job-n1.html")})
        self.assertEqual({}, {k: v for k, v in snapshot(self.handoff).items()
                              if k.startswith("sessions")})


# --- the date prefix on session folders ---------------------------------------

EARLY = "20261020T100000Z"
LATE = "20261021T100000Z"


class DatePrefixTests(MigrationCase):
    def two_date_session(self, session="s-x"):
        """A session with a run on 20261020 and another on 20261021, each with dependents."""
        self.receipt("receipts", EARLY, session, jobs=["job-early"])
        self.receipt("receipts", LATE, session, jobs=["job-late"])
        for stamp in (EARLY, LATE):
            self.put(f"cost-receipts/cost-receipt-{stamp}.md", "cost")
            self.put(f"receipts/diagram/receipt-{stamp}.svg", "<svg/>")
        for job in ("job-early", "job-late"):
            self.put(f"transcripts/{job}.html", "t")

    def one_folder_files(self, folder_name):
        return sorted([
            f"{folder_name}/cost-receipt-{EARLY}.md", f"{folder_name}/cost-receipt-{LATE}.md",
            f"{folder_name}/diagram/session-timeline-{EARLY}.svg",
            f"{folder_name}/diagram/session-timeline-{LATE}.svg",
            f"{folder_name}/receipt-{EARLY}.md", f"{folder_name}/receipt-{LATE}.md",
            f"{folder_name}/transcript-job-early.html", f"{folder_name}/transcript-job-late.html"])

    def test_a_session_with_receipts_on_two_dates_lands_in_one_folder_named_for_the_earlier(self):
        self.two_date_session()
        dry = self.run_script()
        self.assertEqual(0, dry.returncode, dry.stdout + dry.stderr)
        # The plan already names the one folder, for the later receipt as well.
        self.assertIn(f"-> {self.real}/.handoff/sessions/20261020_s-x/receipt-{LATE}.md", dry.stdout)
        self.assertNotIn("20261021_s-x", dry.stdout)
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(self.one_folder_files("20261020_s-x"), self.sessions_files())
        self.assertEqual(["20261020_s-x"], self.session_folders("s-x"))

    def test_the_dates_are_worked_out_before_the_first_place_call(self):
        self.two_date_session("s-x")
        self.receipt("receipts", "20261015T100000Z", "s-y")
        self.receipt("receipts", "20261030T100000Z", "s-y")
        real_place = mig.Planner.place
        seen = []

        def spy(planner, *args, **kwargs):
            seen.append(dict(planner.dates))
            return real_place(planner, *args, **kwargs)

        with unittest.mock.patch.object(mig.Planner, "place", spy):
            plan = mig.build_plan(self.real, self.projects, {})
        # Both sessions have their date at the first placement, which is s-x's, not s-y's.
        self.assertEqual({"s-x": "20261020", "s-y": "20261015"}, seen[0])
        self.assertEqual({"s-x": "20261020", "s-y": "20261015"}, seen[-1])
        # And each action carries it, so apply_plan rebuilds the same path.
        self.assertEqual({"20261020"}, {a.date for a in plan.moves if a.sid == "s-x"})
        self.assertEqual({"20261015"}, {a.date for a in plan.moves if a.sid == "s-y"})

    def test_each_session_takes_the_date_of_its_own_earliest_receipt_when_runs_interleave(self):
        self.receipt("receipts", "20261001T100000Z", "s-a")
        self.receipt("receipts", "20261002T100000Z", "s-b")
        self.receipt("receipts", "20261003T100000Z", "s-a")
        self.receipt("receipts", "20261004T100000Z", "s-b")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["20261001_s-a"], self.session_folders("s-a"))
        self.assertEqual(["20261002_s-b"], self.session_folders("s-b"))
        self.assertEqual(4, len(self.sessions_files()))

    def test_interrupted_between_the_two_receipts_the_later_still_joins_the_earlier_folder(self):
        # The earlier receipt moved and the process died: the later one is still legacy.
        self.receipt("sessions/20261020_s-x", EARLY, "s-x", jobs=["job-early"])
        self.receipt("receipts", LATE, "s-x", jobs=["job-late"])
        self.put(f"cost-receipts/cost-receipt-{LATE}.md", "cost")
        self.put("transcripts/job-late.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["20261020_s-x"], self.session_folders("s-x"))
        self.assertEqual(sorted([f"20261020_s-x/receipt-{EARLY}.md", f"20261020_s-x/receipt-{LATE}.md",
                                 f"20261020_s-x/cost-receipt-{LATE}.md",
                                 "20261020_s-x/transcript-job-late.html"]), self.sessions_files())

    def test_two_runs_interrupted_between_the_receipts_still_land_in_one_folder(self):
        # Run one cannot install the earlier receipt, so the later one is the first file
        # to create the folder. It must still be named for the earlier date.
        self.two_date_session()
        real_link = os.link

        def refuse_the_earlier_receipt(src, dst, *args, **kwargs):
            if Path(dst).name == f"receipt-{EARLY}.md":
                raise PermissionError("interrupted here")
            return real_link(src, dst, *args, **kwargs)

        with unittest.mock.patch.object(os, "link", refuse_the_earlier_receipt):
            code, out, _ = self.in_process("--apply")
        self.assertEqual(1, code, out)
        self.assertTrue((self.handoff / f"receipts/receipt-{EARLY}.md").is_file())
        self.assertEqual(["20261020_s-x"], self.session_folders("s-x"))
        self.assertTrue((self.handoff / f"sessions/20261020_s-x/receipt-{LATE}.md").is_file())

        second = self.run_script(apply=True)
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        self.assertEqual(["20261020_s-x"], self.session_folders("s-x"))
        self.assertEqual(self.one_folder_files("20261020_s-x"), self.sessions_files())
        self.assertEqual([], self.legacy_files())

    def test_a_session_that_already_has_a_folder_keeps_that_name(self):
        # Created later by a runtime save, so it is dated after the legacy receipts it now receives.
        self.receipt("sessions/20261101_s-x", "20261101T100000Z", "s-x")
        self.receipt("receipts", EARLY, "s-x")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["20261101_s-x"], self.session_folders("s-x"))
        self.assertEqual(sorted([f"20261101_s-x/receipt-20261101T100000Z.md",
                                 f"20261101_s-x/receipt-{EARLY}.md"]), self.sessions_files())

    def test_two_folders_for_one_id_send_a_legacy_receipt_to_the_first_sorted_name(self):
        for name, stamp in (("20261101_s-x", "20261101T100000Z"), ("20261020_s-x", EARLY)):
            self.receipt(f"sessions/{name}", stamp, "s-x")
        self.receipt("receipts", "20261215T100000Z", "s-x")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.handoff / "sessions/20261020_s-x/receipt-20261215T100000Z.md").is_file())
        self.assertFalse((self.handoff / "sessions/20261101_s-x/receipt-20261215T100000Z.md").exists())

    def test_a_none_receipt_whose_session_is_recovered_takes_its_earliest_receipts_date(self):
        # The none receipt is the earlier run: it fixes the folder's date for the named one too.
        self.receipt("receipts", "20261015T100000Z", "none")
        self.receipt("receipts", EARLY, "sess-R")
        self.transcript_file("proj-a", "sess-R", self.saved_line("20261015T100000Z"))
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["20261015_sess-R"], self.session_folders("sess-R"))
        self.assertEqual(sorted(["20261015_sess-R/receipt-20261015T100000Z.md",
                                 f"20261015_sess-R/receipt-{EARLY}.md"]), self.sessions_files())
        self.assertEqual(1, len(by_action(result.stdout)["rewrite"]))

    def test_a_recovered_none_receipt_that_is_the_later_run_does_not_move_the_date(self):
        self.receipt("receipts", "20261010T100000Z", "sess-R")
        self.receipt("receipts", "20261020T100000Z", "none")
        self.transcript_file("proj-a", "sess-R", self.saved_line("20261020T100000Z"))
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["20261010_sess-R"], self.session_folders("sess-R"))
        self.assertEqual(2, len(self.sessions_files()))

    def test_a_recovered_session_alone_takes_the_date_of_its_one_receipt(self):
        self.receipt("receipts", "20261015T100000Z", "none", jobs=["job-n1"])
        self.put("transcripts/job-n1.html", "t")
        self.transcript_file("proj-a", "sess-R", self.saved_line("20261015T100000Z"))
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["20261015_sess-R/receipt-20261015T100000Z.md",
                          "20261015_sess-R/transcript-job-n1.html"], self.sessions_files())

    def test_none_stays_one_bare_folder_whatever_the_dates(self):
        self.receipt("receipts", EARLY, "none", jobs=["job-n1"])
        self.receipt("receipts", LATE, "none")
        self.put(f"cost-receipts/cost-receipt-{LATE}.md", "cost")
        self.put("transcripts/job-n1.html", "t")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(["none"], sorted(p.name for p in (self.handoff / "sessions").iterdir()))
        self.assertEqual(sorted([f"none/receipt-{EARLY}.md", f"none/receipt-{LATE}.md",
                                 f"none/cost-receipt-{LATE}.md", "none/transcript-job-n1.html"]),
                         self.sessions_files())

    def test_a_bare_id_folder_is_not_read_as_a_session_by_the_migration(self):
        # Nothing shipped that way, so it is neither renamed nor used as a destination.
        self.receipt("sessions/s-x", "20261001T100000Z", "s-x")
        self.receipt("receipts", EARLY, "s-x")
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.handoff / f"sessions/20261020_s-x/receipt-{EARLY}.md").is_file())
        self.assertEqual(["receipt-20261001T100000Z.md"],
                         sorted(p.name for p in (self.handoff / "sessions/s-x").iterdir()))


# --- command line -------------------------------------------------------------


class CliTests(MigrationCase):
    def test_repo_without_dot_handoff_exits_2_and_writes_nothing(self):
        bare = self.tmp / "bare"
        bare.mkdir()
        result = subprocess.run(
            [sys.executable, str(SCRIPT), "--repo", str(bare), "--projects-root", str(self.projects)],
            capture_output=True, text=True, env=self.env())
        self.assertEqual(2, result.returncode)
        self.assertIn(".handoff", result.stderr)
        self.assertEqual([], list(bare.iterdir()))

    def test_unknown_flag_exits_2(self):
        result = self.run_script("--nope")
        self.assertEqual(2, result.returncode)

    def test_symlinked_dot_handoff_exits_2(self):
        real = self.tmp / "real-handoff"
        self.handoff.rename(real)
        os.symlink(real, self.handoff)
        result = self.run_script(apply=True)
        self.assertEqual(2, result.returncode)

    def test_repo_defaults_to_the_current_directory(self):
        self.receipt("receipts", S1, "s-one")
        result = self.run_script(repo=False, cwd=self.repo)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual(1, len(by_action(result.stdout)["move"]))
        self.assertFalse((self.handoff / "sessions").exists())

    def test_a_missing_projects_root_means_no_recovery_not_an_error(self):
        self.receipt("receipts", S1, "none")
        shutil.rmtree(self.projects)
        result = self.run_script(apply=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertTrue((self.handoff / f"sessions/none/receipt-{S1}.md").is_file())


if __name__ == "__main__":
    unittest.main()
