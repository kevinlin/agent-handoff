from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts/delegate-codex.sh")


# The two --version strings that tell the identically named binaries apart.
GITHUB_COPILOT_VERSION = "GitHub Copilot CLI 1.0.83."
AWS_COPILOT_VERSION = "copilot version: v1.34.1"


def version_shim(version: str) -> str:
    """A fake CLI prologue answering --version and nothing else."""

    return f"if [ \"${{1:-}}\" = \"--version\" ]; then printf '{version}\\n'; exit 0; fi\n"


def write_fake(path: Path, body: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"#!/usr/bin/env bash\n{body}", encoding="utf-8")
    path.chmod(0o755)
    return path


def make_env(root: Path, codex_body: str, claude_body: str | None = None) -> dict[str, str]:
    """A clean environment pointing every HANDOFF_*_BIN at a fake worker CLI."""

    env = os.environ.copy()
    env.pop("HANDOFF_CLAUDE_PERMISSION_MODE", None)
    env.update({"HOME": str(root / "home"), "XDG_CONFIG_HOME": str(root / "xdg")})
    for name, body in (("codex", codex_body), ("claude", claude_body or codex_body)):
        env[f"HANDOFF_{name.upper()}_BIN"] = str(write_fake(root / name, body))
    # The copilot fake identifies itself the way GitHub Copilot CLI does, so
    # discovery has something real to match on rather than a name.
    env["HANDOFF_COPILOT_BIN"] = str(
        write_fake(root / "copilot", version_shim(GITHUB_COPILOT_VERSION) + codex_body)
    )
    return env


def run_delegate(env: dict[str, str], *arguments: str):
    return subprocess.run(
        ["bash", str(SCRIPT), *arguments],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def parse_pairs(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


class DelegateRoleTests(unittest.TestCase):
    def run_submit(self, config: str | None, *arguments: str, init_git: bool = False):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        repo = root / "repo"
        repo.mkdir()
        if init_git:
            subprocess.run(
                ["git", "init", "--quiet", str(repo)],
                check=True,
                capture_output=True,
                text=True,
            )
        prompt = root / "prompt.md"
        prompt.write_text("test prompt\n", encoding="utf-8")
        if config is not None:
            config_path = repo / ".handoff" / "config.toml"
            config_path.parent.mkdir()
            config_path.write_text(config, encoding="utf-8")
        env = make_env(root, "printf 'codex-cli test-version\\n'\n")
        result = run_delegate(
            env,
            "submit",
            "--repo",
            str(repo),
            "--prompt-file",
            str(prompt),
            "--dry-run",
            *arguments,
        )
        return result, repo

    @staticmethod
    def config(
        *,
        include_deep_reasoner: bool = True,
        deep_reasoner_backend: str = "codex",
        deep_reasoner_effort: str = "xhigh",
        include_arbiter: bool = False,
    ) -> str:
        deep_reasoner = ""
        if include_deep_reasoner:
            deep_reasoner = (
                "[hosts.claude_code.identities.deep_reasoner]\n"
                f'backend = "{deep_reasoner_backend}"\n'
                'model = "gpt-deep"\n'
                f'effort = "{deep_reasoner_effort}"\n\n'
            )
        arbiter = ""
        if include_arbiter:
            arbiter = (
                "\n[hosts.claude_code.identities.arbiter]\n"
                'backend = "codex"\n'
                'model = "gpt-arbiter"\n'
                'effort = "high"\n'
            )
        return (
            "schema_version = 2\n"
            "revision = 0\n\n"
            f"{deep_reasoner}"
            "[hosts.claude_code.identities.fast_worker]\n"
            'backend = "codex"\n'
            'model = "gpt-fast"\n'
            'effort = "low"\n'
            f"{arbiter}"
        )

    @staticmethod
    def config_with(identity: str) -> str:
        return (
            "schema_version = 2\n"
            "revision = 0\n\n"
            f"[hosts.claude_code.identities.{identity}]\n"
            'backend = "codex"\n'
            'model = "gpt-e2e"\n'
            'effort = "high"\n'
        )

    parsed = staticmethod(parse_pairs)

    def test_deep_reasoner_uses_project_config(self):
        result, _ = self.run_submit(self.config(), "--role", "deep_reasoner")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        expected = {
            "role": "deep_reasoner",
            "backend": "codex",
            "model": "gpt-deep",
            "effort": "xhigh",
            "model_source": "config:project",
            "effort_source": "config:project",
        }
        self.assertEqual(expected, {key: parsed[key] for key in expected})

    def test_explicit_effort_overrides_fast_worker_config(self):
        result, _ = self.run_submit(
            self.config(), "--role", "fast_worker", "--effort", "xhigh"
        )
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertEqual("gpt-fast", parsed["model"])
        self.assertEqual("config:project", parsed["model_source"])
        self.assertEqual("xhigh", parsed["effort"])
        self.assertEqual("explicit", parsed["effort_source"])

    def test_missing_role_fails_with_setup_guidance(self):
        result, _ = self.run_submit(
            self.config(include_deep_reasoner=False), "--role", "deep_reasoner"
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("python3 scripts/handoff-config.py init", result.stderr)
        self.assertIn("set --role deep_reasoner", result.stderr)

    def test_without_role_uses_default_effort(self):
        result, _ = self.run_submit(None)
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertEqual("none", parsed["role"])
        self.assertEqual("codex", parsed["backend"])
        self.assertEqual("default", parsed["model"])
        self.assertEqual("high", parsed["effort"])
        self.assertEqual("default", parsed["effort_source"])
        # A codex worker is bounded by its own sandbox config, not by a mode.
        self.assertEqual("", parsed["permission_mode"])

    def test_gpt_5_6_efforts_are_accepted_and_unknown_ones_refused(self):
        for effort in ("minimal", "low", "medium", "high", "xhigh", "max", "ultra"):
            with self.subTest(effort=effort):
                result, _ = self.run_submit(None, "--effort", effort)
                self.assertEqual((0, ""), (result.returncode, result.stderr))
                self.assertEqual(effort, self.parsed(result.stdout)["effort"])
        result, _ = self.run_submit(None, "--effort", "supreme")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid --effort: supreme", result.stderr)

    def test_dry_run_does_not_create_jobs_directory(self):
        result, repo = self.run_submit(self.config(), "--role", "deep_reasoner")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertFalse((repo / ".handoff" / "jobs").exists())

    def test_dry_run_reports_selected_codex_binary(self):
        result, _ = self.run_submit(self.config(), "--role", "deep_reasoner")
        self.assertEqual(0, result.returncode, result.stderr)
        parsed = self.parsed(result.stdout)
        self.assertEqual("env", parsed["codex_bin_source"])
        self.assertEqual("codex-cli test-version", parsed["codex_version"])
        self.assertTrue(parsed["codex_bin"].endswith("/codex"))

    def test_arbiter_uses_codex_identity_config(self):
        result, _ = self.run_submit(
            self.config(include_arbiter=True), "--role", "arbiter"
        )
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertEqual("arbiter", parsed["role"])
        self.assertEqual("gpt-arbiter", parsed["model"])
        self.assertEqual("high", parsed["effort"])
        self.assertEqual("codex", parsed["backend"])

    def test_e2e_roles_are_accepted(self):
        for role in ("e2e_specifier", "e2e_verifier"):
            with self.subTest(role=role):
                result, _ = self.run_submit(self.config_with(role), "--role", role)
                self.assertEqual((0, ""), (result.returncode, result.stderr))
                self.assertEqual(role, self.parsed(result.stdout)["role"])

    def test_claude_backend_is_delegated_on_its_own_backend(self):
        result, _ = self.run_submit(
            self.config(deep_reasoner_backend="claude", deep_reasoner_effort="high"),
            "--role",
            "deep_reasoner",
        )
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertEqual("claude", parsed["backend"])
        self.assertEqual("config:project", parsed["backend_source"])
        self.assertTrue(parsed["codex_bin"].endswith("/claude"), parsed["codex_bin"])
        # --skip-git-repo-check is a codex flag; a claude job never carries it.
        self.assertEqual("", parsed["skip_git_repo_check"])
        self.assertEqual("bypassPermissions", parsed["permission_mode"])

    def test_explicit_backend_contradicting_a_role_is_refused(self):
        result, _ = self.run_submit(
            self.config(deep_reasoner_backend="claude", deep_reasoner_effort="high"),
            "--role",
            "deep_reasoner",
            "--backend",
            "codex",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("contradicts identity deep_reasoner", result.stderr)
        self.assertIn("handoff-config.py set --role deep_reasoner", result.stderr)

    def test_backend_without_a_role_selects_the_cli(self):
        result, _ = self.run_submit(None, "--backend", "claude")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertEqual("claude", parsed["backend"])
        self.assertEqual("explicit", parsed["backend_source"])
        result, _ = self.run_submit(None, "--backend", "gemini")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid --backend: gemini", result.stderr)

    def test_efforts_are_validated_per_cli(self):
        for effort in ("low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                result, _ = self.run_submit(None, "--backend", "claude", "--effort", effort)
                self.assertEqual((0, ""), (result.returncode, result.stderr))
        for effort in ("minimal", "ultra"):
            with self.subTest(effort=effort):
                result, _ = self.run_submit(None, "--backend", "claude", "--effort", effort)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(f"invalid --effort for claude: {effort}", result.stderr)

    def test_copilot_backend_is_selected_and_reports_its_binary(self):
        result, _ = self.run_submit(None, "--backend", "copilot")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertEqual("copilot", parsed["backend"])
        self.assertEqual("explicit", parsed["backend_source"])
        self.assertTrue(parsed["codex_bin"].endswith("/copilot"), parsed["codex_bin"])
        self.assertEqual("allow-all-tools", parsed["permission_mode"])
        # --skip-git-repo-check is a codex flag; a copilot job never carries it.
        self.assertEqual("", parsed["skip_git_repo_check"])

    def test_copilot_efforts_mirror_the_cli_enum(self):
        for effort in ("none", "minimal", "low", "medium", "high", "xhigh", "max"):
            with self.subTest(effort=effort):
                result, _ = self.run_submit(None, "--backend", "copilot", "--effort", effort)
                self.assertEqual((0, ""), (result.returncode, result.stderr))
                self.assertEqual(effort, self.parsed(result.stdout)["effort"])
        # `ultra` is codex's; the enums are per CLI, never one shared list.
        result, _ = self.run_submit(None, "--backend", "copilot", "--effort", "ultra")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid --effort for copilot: ultra", result.stderr)

    def test_copilot_refuses_the_auto_model(self):
        """An identity is a deliberate backend + model + effort choice.

        `auto` hands the model choice back to the vendor per request, so the
        job would record what Copilot picked rather than what was configured.
        """

        result, _ = self.run_submit(None, "--backend", "copilot", "--model", "auto")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("'auto' is refused on a copilot job", result.stderr)
        self.assertIn("--backend copilot --model <model>", result.stderr)
        # Any other model is fine.
        result, _ = self.run_submit(None, "--backend", "copilot", "--model", "gpt-5.6-luna")
        self.assertEqual((0, ""), (result.returncode, result.stderr))

    def test_a_copilot_identity_routes_to_the_copilot_cli(self):
        result, _ = self.run_submit(
            self.config(deep_reasoner_backend="copilot", deep_reasoner_effort="high"),
            "--role",
            "deep_reasoner",
        )
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertEqual("copilot", parsed["backend"])
        self.assertEqual("config:project", parsed["backend_source"])
        self.assertTrue(parsed["codex_bin"].endswith("/copilot"), parsed["codex_bin"])

    def test_non_git_repo_appends_skip_git_repo_check(self):
        result, repo = self.run_submit(None)
        self.assertFalse((repo / ".git").exists())
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertIn("--skip-git-repo-check", result.stdout)
        self.assertEqual("--skip-git-repo-check", parsed["skip_git_repo_check"])

    def test_git_repo_omits_skip_git_repo_check(self):
        result, repo = self.run_submit(None, init_git=True)
        self.assertTrue((repo / ".git").exists())
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        self.assertNotIn("--skip-git-repo-check", result.stdout)
        self.assertEqual("", parsed["skip_git_repo_check"])


class BackendLifecycle:
    """The whole job lifecycle, asserted identically against both backends.

    Only the exec-line shape differs (codex passes -C, claude cd's), so the
    subclasses below override BACKEND and WORKDIR_MARKER and nothing else.
    Parity is what this class exists to prove, so nothing here is skipped
    for one backend.
    """

    BACKEND = "codex"
    WORKDIR_MARKER = '-C "$WORKDIR"'
    # What this backend's worker runs under; codex is sandboxed by its own
    # config instead, so it records none.
    PERMISSION_MODE = ""
    DENIED_LINE = (
        '{"type":"system","subtype":"permission_denied","tool_name":"Bash",'
        '"decision_reason":"no approval surface in this session"}'
    )
    # What `result` should name, given DENIED_LINE twice around LOG_LINES.
    DENIED_TOOLS = ["Bash"]
    # A terminal event stream in this backend's own shape.
    LOG_LINES = (
        '{"type":"thread.started","thread_id":"sess-fixture"}',
        '{"type":"item.completed","item":{"type":"command_execution","command":"pytest -q"}}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"work done"}}',
        '{"type":"turn.completed","usage":{"input_tokens":11,"output_tokens":7}}',
    )

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--quiet", "--initial-branch", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.repo / ".gitignore").write_text(".handoff/\n", encoding="utf-8")
        (self.repo / "tracked.txt").write_text("tracked\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "--quiet", "-m", "init")
        self.prompt = self.root / "prompt.md"
        self.prompt.write_text("test prompt\n", encoding="utf-8")
        # Exits immediately, so the launched job finishes without doing work.
        self.env = make_env(self.root, "exit 0\n")
        # Registered after the temp dir, so it runs before it: a detached job
        # still writing into its own directory races the tree removal.
        self.addCleanup(self.await_jobs)

    def await_exit(self, job: Path):
        """Wait for a launched job to settle.

        exit_code is the last thing run.sh writes, but its shell stays alive for
        a moment after — long enough to race the temp-tree removal in teardown,
        which is how this surfaced. Wait for the process too, not just the file.
        """

        settled = False
        for _ in range(200):
            if (job / "exit_code").is_file():
                settled = True
                break
            time.sleep(0.05)
        pid_file = job / "pid"
        if settled and pid_file.is_file():
            pid = int(pid_file.read_text(encoding="utf-8").strip() or 0)
            for _ in range(200):
                try:
                    os.kill(pid, 0)
                except OSError:
                    break
                time.sleep(0.05)
        return settled

    def await_jobs(self):
        for job in (self.repo / ".handoff" / "jobs").glob("job-*"):
            self.await_exit(job)

    def git(self, *arguments: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.repo), *arguments],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def delegate(self, *arguments: str):
        return run_delegate(self.env, *arguments)

    def submit_raw(self, *arguments: str):
        return self.delegate(
            "submit",
            "--repo",
            str(self.repo),
            "--prompt-file",
            str(self.prompt),
            "--backend",
            self.BACKEND,
            *arguments,
        )

    def submit(self, *arguments: str) -> str:
        result = self.submit_raw(*arguments)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def job_dir(self, job_id: str) -> Path:
        return self.repo / ".handoff" / "jobs" / job_id

    def read_meta(self, job_id: str) -> dict[str, str]:
        return parse_pairs((self.job_dir(job_id) / "meta").read_text(encoding="utf-8"))

    def cleanup(self, job_id: str):
        return self.delegate("cleanup", job_id, "--repo", str(self.repo))

    def worktree(self, job_id: str) -> Path:
        return self.repo / ".handoff" / "worktrees" / job_id

    def finish(self, job_id: str) -> Path:
        """Give a job a terminal state and a readable log in its own format.

        The detached run.sh redirects into log.jsonl, so wait for the fake
        worker to exit before writing the fixture — otherwise it truncates it.
        """

        job = self.job_dir(job_id)
        self.assertTrue(self.await_exit(job), "fake worker never exited")
        (job / "log.jsonl").write_text("\n".join(self.LOG_LINES) + "\n", encoding="utf-8")
        (job / "session_id").unlink(missing_ok=True)
        return job

    def test_submit_records_the_backend_that_will_execute_it(self):
        meta = self.read_meta(self.submit())
        self.assertEqual(self.BACKEND, meta["backend"])
        self.assertEqual("explicit", meta["backend_source"])

    def test_worktree_is_created_and_recorded_with_a_base_sha(self):
        job_id = self.submit("--worktree", "e2e/T1")
        meta = self.read_meta(job_id)
        self.assertEqual(str(self.worktree(job_id)), meta["worktree"])
        self.assertEqual("e2e/T1", meta["branch"])
        self.assertRegex(meta["base_commit"], r"^[0-9a-f]{40}$")
        self.assertTrue((self.worktree(job_id) / "tracked.txt").exists())

    def test_base_is_resolved_to_an_immutable_sha_before_the_branch_moves(self):
        job_id = self.submit("--worktree", "e2e/T2", "--base", "main")
        pinned = self.read_meta(job_id)["base_commit"]
        (self.repo / "tracked.txt").write_text("advanced\n", encoding="utf-8")
        self.git("commit", "--quiet", "-am", "advance main")
        self.assertNotEqual(self.git("rev-parse", "main"), pinned)
        # The worktree branch still points at the pinned commit, not at main.
        self.assertEqual(pinned, self.git("rev-parse", "e2e/T2"))

    def test_invalid_base_fails_before_the_job_directory_is_created(self):
        result = self.submit_raw("--worktree", "e2e/T3", "--base", "no-such-ref")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not a valid commit", result.stderr)
        self.assertFalse(any((self.repo / ".handoff" / "jobs").glob("job-*")))

    def test_run_script_uses_the_worktree_as_working_directory(self):
        job_id = self.submit("--worktree", "e2e/T4")
        run_sh = (self.job_dir(job_id) / "run.sh").read_text(encoding="utf-8")
        self.assertIn(str(self.worktree(job_id)), run_sh)
        self.assertIn(self.WORKDIR_MARKER, run_sh)

    def test_cleanup_removes_a_clean_worktree_and_is_idempotent(self):
        job_id = self.submit("--worktree", "e2e/T5")
        first = self.cleanup(job_id)
        self.assertEqual(0, first.returncode, first.stderr)
        self.assertFalse(self.worktree(job_id).exists())
        second = self.cleanup(job_id)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertIn("no worktree", second.stdout)

    def test_cleanup_refuses_a_dirty_worktree(self):
        job_id = self.submit("--worktree", "e2e/T6")
        (self.worktree(job_id) / "tracked.txt").write_text("dirty\n", encoding="utf-8")
        result = self.cleanup(job_id)
        self.assertEqual(1, result.returncode)
        self.assertIn("uncommitted changes", result.stderr)
        self.assertTrue(self.worktree(job_id).exists())

    def test_submit_without_worktree_records_no_worktree_keys(self):
        meta = self.read_meta(self.submit())
        self.assertNotIn("worktree", meta)
        self.assertNotIn("base_commit", meta)

    def test_resume_lands_in_the_parent_worktree_on_the_parent_backend(self):
        job_id = self.submit("--worktree", "e2e/T7")
        self.finish(job_id)
        result = self.delegate(
            "resume", job_id, "--repo", str(self.repo), "--prompt-file", str(self.prompt)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        child = result.stdout.strip()
        meta = self.read_meta(child)
        self.assertEqual(str(self.worktree(job_id)), meta["worktree"])
        # The fix round never re-decides the vendor, and keeps its provenance.
        self.assertEqual(self.BACKEND, meta["backend"])
        self.assertEqual(self.read_meta(job_id)["role"], meta["role"])
        run_sh = (self.job_dir(child) / "run.sh").read_text(encoding="utf-8")
        self.assertIn(str(self.worktree(job_id)), run_sh)

    def test_status_and_result_report_the_same_shape(self):
        """What Phase 3's /loop reads is backend-agnostic — asserted, not assumed."""

        job_id = self.submit()
        self.finish(job_id)

        status = self.delegate("status", job_id, "--repo", str(self.repo))
        self.assertEqual(0, status.returncode, status.stderr)
        self.assertEqual(
            ["job", "state", "last_event", "log"],
            [line.split(":", 1)[0] for line in status.stdout.strip().splitlines()],
        )
        self.assertIn("state: DONE", status.stdout)

        result = self.delegate("result", job_id, "--repo", str(self.repo), "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(
            {
                "session_id",
                "agent_message",
                "commands",
                "permission_denied",
                "denied_tools",
                "usage",
                "errors",
            },
            set(payload),
        )
        self.assertEqual(0, payload["permission_denied"])
        self.assertEqual("sess-fixture", payload["session_id"])
        self.assertEqual("work done", payload["agent_message"])
        self.assertEqual(["pytest -q"], payload["commands"])
        self.assertTrue(payload["usage"])


    def test_meta_records_the_permission_mode_the_worker_runs_under(self):
        meta = self.read_meta(self.submit())
        self.assertEqual(self.PERMISSION_MODE, meta.get("permission_mode", ""))

    def test_result_reports_denials_that_the_exit_code_hides(self):
        """A blocked check still leaves exit 0, so the count is the only signal.

        This is the v3.5.1 defect in miniature: a worker that could not run its
        own acceptance checks reported success, and nothing downstream noticed.
        """

        job_id = self.submit()
        job = self.finish(job_id)
        job.joinpath("log.jsonl").write_text(
            "\n".join((self.DENIED_LINE, *self.LOG_LINES, self.DENIED_LINE)) + "\n",
            encoding="utf-8",
        )

        payload = json.loads(
            self.delegate("result", job_id, "--repo", str(self.repo), "--json").stdout
        )
        self.assertEqual(2, payload["permission_denied"])
        self.assertEqual(self.DENIED_TOOLS, payload["denied_tools"])
        # A denial is not worker output and must not be read as any.
        self.assertEqual("work done", payload["agent_message"])

        text = self.delegate("result", job_id, "--repo", str(self.repo))
        self.assertIn(f"permission_denied: 2 ({', '.join(self.DENIED_TOOLS)})", text.stdout)
        self.assertIn("verify its own work", text.stdout)

        status = self.delegate("status", job_id, "--repo", str(self.repo))
        self.assertIn("permission_denied: 2", status.stdout)


class CodexWorktreeTests(BackendLifecycle, unittest.TestCase):
    pass


class ClaudeWorktreeTests(BackendLifecycle, unittest.TestCase):
    BACKEND = "claude"
    # `claude` has no -C; the run script cd's into the worktree instead.
    WORKDIR_MARKER = 'cd "$WORKDIR"'
    PERMISSION_MODE = "bypassPermissions"
    LOG_LINES = (
        '{"type":"system","subtype":"init","session_id":"sess-fixture"}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"looking"},'
        '{"type":"tool_use","name":"Bash","input":{"command":"pytest -q"}}]},'
        '"session_id":"sess-fixture"}',
        '{"type":"result","subtype":"success","result":"work done",'
        '"usage":{"input_tokens":11,"output_tokens":7},"session_id":"sess-fixture"}',
    )

    def exec_line(self, job_id: str) -> str:
        return (self.job_dir(job_id) / "run.sh").read_text(encoding="utf-8")

    def test_worker_runs_with_permission_checks_bypassed_by_default(self):
        """The v3.5.1 fix, asserted.

        A background `--print` job has no approval surface, so every mode that
        prompts denies instead — which is what stopped a v3.5.0 worker from
        running the acceptance checks its own packet asked for.
        """

        run_sh = self.exec_line(self.submit())
        self.assertIn("--permission-mode bypassPermissions", run_sh)
        # Inert under bypass, but it keeps a dialled-down job from hanging on a
        # prompt nobody is there to answer.
        self.assertIn("--permission-prompts none", run_sh)

    def test_submit_warns_that_the_worker_is_unsupervised(self):
        result = self.submit_raw()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("permission checks bypassed", result.stderr)
        self.assertIn("HANDOFF_CLAUDE_PERMISSION_MODE=acceptEdits", result.stderr)
        # The warning goes to stderr; stdout stays exactly the jobId, which is
        # what the driver captures.
        self.assertRegex(result.stdout.strip(), r"^job-[\w.:-]+$")

    def test_the_override_dials_the_worker_back_down(self):
        self.env["HANDOFF_CLAUDE_PERMISSION_MODE"] = "acceptEdits"
        result = self.submit_raw()
        job_id = result.stdout.strip()
        self.assertIn("--permission-mode acceptEdits", self.exec_line(job_id))
        self.assertEqual("acceptEdits", self.read_meta(job_id)["permission_mode"])
        # Nothing is being bypassed, so there is nothing to warn about.
        self.assertNotIn("permission checks bypassed", result.stderr)

    def test_read_only_stays_plan_and_beats_the_override(self):
        self.env["HANDOFF_CLAUDE_PERMISSION_MODE"] = "bypassPermissions"
        job_id = self.submit("--read-only")
        self.assertIn("--permission-mode plan", self.exec_line(job_id))
        self.assertEqual("plan", self.read_meta(job_id)["permission_mode"])

    def test_an_unknown_override_is_refused_before_the_job_exists(self):
        """The value is spliced into the generated run.sh, so it is validated."""

        self.env["HANDOFF_CLAUDE_PERMISSION_MODE"] = "plan; touch pwned"
        result = self.submit_raw()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid HANDOFF_CLAUDE_PERMISSION_MODE", result.stderr)
        self.assertFalse(any((self.repo / ".handoff" / "jobs").glob("job-*")))
        self.assertFalse((self.repo / "pwned").exists())

    def test_a_read_only_job_still_validates_the_override(self):
        """--read-only wins, but a typo must not be swallowed on the way."""

        self.env["HANDOFF_CLAUDE_PERMISSION_MODE"] = "byPassPermissions"
        result = self.submit_raw("--read-only")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid HANDOFF_CLAUDE_PERMISSION_MODE", result.stderr)

    def test_a_fix_round_keeps_the_parent_permission_mode(self):
        job_id = self.submit()
        self.finish(job_id)
        result = self.delegate(
            "resume", job_id, "--repo", str(self.repo), "--prompt-file", str(self.prompt)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        child = result.stdout.strip()
        self.assertEqual("bypassPermissions", self.read_meta(child)["permission_mode"])
        self.assertIn("--permission-mode bypassPermissions", self.exec_line(child))


class CopilotWorktreeTests(BackendLifecycle, unittest.TestCase):
    BACKEND = "copilot"
    # `copilot` takes -C like codex does.
    WORKDIR_MARKER = '-C "$WORKDIR"'
    PERMISSION_MODE = "allow-all-tools"
    DENIED_LINE = (
        '{"type":"tool.execution_complete","data":{"toolCallId":"call_1","success":false,'
        '"error":{"message":"Permission to run this tool was denied due to the following '
        'rules: `shell(curl)`","code":"denied"}}}'
    )
    # The first denial precedes its start event, so its tool has no name yet:
    # the correlation is by toolCallId, not by position.
    DENIED_TOOLS = ["bash", "unknown"]
    LOG_LINES = (
        '{"type":"assistant.message_delta","data":{"content":"wor"},"ephemeral":true}',
        '{"type":"tool.execution_start","data":{"toolCallId":"call_1","toolName":"bash",'
        '"arguments":{"command":"pytest -q","description":"run the suite"}}}',
        '{"type":"tool.execution_complete","data":{"toolCallId":"call_1","success":true,'
        '"result":{"content":"ok"}}}',
        '{"type":"assistant.message","data":{"content":"","toolRequests":[],"turnId":"0"}}',
        '{"type":"assistant.message","data":{"content":"work done","toolRequests":[],'
        '"turnId":"1","phase":"final_answer"}}',
        '{"type":"result","sessionId":"sess-fixture","exitCode":0,'
        '"usage":{"premiumRequests":1,"sessionDurationMs":8717}}',
    )

    def exec_line(self, job_id: str) -> str:
        return (self.job_dir(job_id) / "run.sh").read_text(encoding="utf-8")

    def test_argv_carries_the_permission_and_egress_flags(self):
        """The capability assertions, not just the plumbing.

        v3.5.0 shipped a green suite over a broken permission default because
        no test named the flag that made the worker able to work. These are
        the flags a copilot job is useless or unsafe without.
        """

        run_sh = self.exec_line(self.submit())
        for flag in (
            "--output-format json",
            "--no-ask-user",
            "--no-remote",
            "--no-remote-export",
            "--no-auto-update",
            "--allow-all-tools",
            "--usage-output-file",
            "--log-dir",
        ):
            with self.subTest(flag=flag):
                self.assertIn(flag, run_sh)
        # Without this a long prompt can leave the job waiting on stdin forever.
        self.assertIn("</dev/null", run_sh)

    def test_argv_never_carries_the_export_or_blanket_permission_flags(self):
        """Session export to GitHub web and mobile is on by default.

        A delegated job carries the prompt and the repository contents, and
        Handoff owns the worktree protocol, so none of these are ours to pass.
        """

        run_sh = self.exec_line(self.submit())
        for flag in ("--share", "--share-gist", "--yolo", "--allow-all", "--worktree",
                     "--enable-memory", "--add-dir", "--max-ai-credits"):
            with self.subTest(flag=flag):
                self.assertNotRegex(run_sh, rf"{flag}(?![-\w])")

    def test_read_only_uses_plan_mode_and_never_allow_all_tools(self):
        """The exclusivity is a correctness requirement, not a preference.

        Probed together, plan mode still won on disk but the worker attempted
        only its read, emitted zero denial events, exited 0, and claimed two
        writes that never happened. Nothing in the event stream marks that, so
        there is nothing for the monitor to catch.
        """

        job_id = self.submit("--read-only")
        run_sh = self.exec_line(job_id)
        self.assertIn("--mode plan", run_sh)
        self.assertNotIn("--allow-all-tools", run_sh)
        self.assertEqual("plan", self.read_meta(job_id)["permission_mode"])

    def test_a_writing_job_uses_allow_all_tools_and_never_plan_mode(self):
        run_sh = self.exec_line(self.submit())
        self.assertIn("--allow-all-tools", run_sh)
        self.assertNotIn("--mode plan", run_sh)

    def test_submit_warns_that_the_worker_is_unsupervised(self):
        result = self.submit_raw()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("copilot worker running with permission checks bypassed", result.stderr)
        self.assertIn("--read-only", result.stderr)
        self.assertRegex(result.stdout.strip(), r"^job-[\w.:-]+$")

    def test_a_read_only_job_is_not_warned_about(self):
        result = self.submit_raw("--read-only")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("permission checks bypassed", result.stderr)

    def test_a_crashed_job_still_has_a_resumable_session_id(self):
        """Copilot emits sessionId only in its terminal event.

        So the id is assigned at submit and persisted before launch. This is
        the shape the decision exists for: a job that died before its terminal
        event, whose log therefore names no session at all.
        """

        job_id = self.submit()
        job = self.job_dir(job_id)
        self.assertTrue(self.await_exit(job), "fake worker never exited")
        assigned = (job / "session_id").read_text(encoding="utf-8").strip()
        self.assertRegex(assigned, r"^[0-9a-f]{8}-[0-9a-f-]{27}$")
        self.assertEqual("assigned", self.read_meta(job_id)["session_id_source"])
        self.assertIn(f"--session-id {assigned}", self.exec_line(job_id))

        # A log with no terminal event — nothing to extract an id from.
        job.joinpath("log.jsonl").write_text(
            "\n".join(self.LOG_LINES[:-1]) + "\n", encoding="utf-8"
        )
        payload = json.loads(
            self.delegate("result", job_id, "--repo", str(self.repo), "--json").stdout
        )
        self.assertEqual(assigned, payload["session_id"])

        result = self.delegate(
            "resume", job_id, "--repo", str(self.repo), "--prompt-file", str(self.prompt)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn(f"--resume {assigned}", self.exec_line(result.stdout.strip()))

    def test_a_fix_round_reuses_a_parent_binary_that_still_identifies(self):
        job_id = self.submit()
        self.finish(job_id)
        result = self.delegate(
            "resume", job_id, "--repo", str(self.repo), "--prompt-file", str(self.prompt)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("parent", self.read_meta(result.stdout.strip())["codex_bin_source"])

    def test_a_fix_round_re_verifies_the_parent_binary_identity(self):
        """The recorded path may point at a different tool by the fix round.

        `copilot` is also AWS Copilot CLI's binary name, so reusing a recorded
        path on the strength of it still being executable is not enough.
        """

        job_id = self.submit()
        self.finish(job_id)
        impostor = write_fake(
            self.root / "aws" / "copilot", version_shim(AWS_COPILOT_VERSION) + "exit 0\n"
        )
        meta = self.job_dir(job_id) / "meta"
        meta.write_text(
            meta.read_text(encoding="utf-8").replace(
                self.env["HANDOFF_COPILOT_BIN"], str(impostor)
            ),
            encoding="utf-8",
        )
        result = self.delegate(
            "resume", job_id, "--repo", str(self.repo), "--prompt-file", str(self.prompt)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        child = self.read_meta(result.stdout.strip())
        self.assertEqual(self.env["HANDOFF_COPILOT_BIN"], child["codex_bin"])
        self.assertEqual("env", child["codex_bin_source"])

    def test_result_reports_a_session_error_in_both_output_modes(self):
        """Copilot's API failures can arrive with an empty stderr."""

        job_id = self.submit()
        job = self.finish(job_id)
        error_line = (
            '{"type":"session.error","data":{"errorType":"query","statusCode":400,'
            '"message":"Execution failed: 400 Unsupported value"}}'
        )
        job.joinpath("log.jsonl").write_text(
            "\n".join((*self.LOG_LINES[:-1], error_line, self.LOG_LINES[-1])) + "\n",
            encoding="utf-8",
        )

        payload = json.loads(
            self.delegate("result", job_id, "--repo", str(self.repo), "--json").stdout
        )
        self.assertEqual(
            [{"error_type": "query", "message": "Execution failed: 400 Unsupported value",
              "status_code": 400}],
            payload["errors"],
        )
        text = self.delegate("result", job_id, "--repo", str(self.repo)).stdout
        self.assertIn("session_error: query (status 400)", text)
        self.assertIn("Unsupported value", text)

    def test_ephemeral_events_are_dropped(self):
        """82 streaming deltas in one small job; folding them in would repeat it."""

        job_id = self.submit()
        self.finish(job_id)
        payload = json.loads(
            self.delegate("result", job_id, "--repo", str(self.repo), "--json").stdout
        )
        self.assertEqual("work done", payload["agent_message"])


class CopilotBinaryDiscoveryTests(unittest.TestCase):
    """`copilot` is two unrelated CLIs, and PATH order decides which one wins."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.prompt = self.root / "prompt.md"
        self.prompt.write_text("test prompt\n", encoding="utf-8")
        self.env = make_env(self.root, "exit 0\n")
        self.github = self.install("github-bin", GITHUB_COPILOT_VERSION)
        self.aws = self.install("aws-bin", AWS_COPILOT_VERSION)

    def install(self, directory: str, version: str) -> Path:
        return write_fake(
            self.root / directory / "copilot", version_shim(version) + "exit 0\n"
        )

    def dry_run(self, env: dict[str, str]):
        return run_delegate(
            env, "submit", "--repo", str(self.repo), "--prompt-file", str(self.prompt),
            "--backend", "copilot", "--dry-run",
        )

    def on_path(self, *binaries: Path) -> dict[str, str]:
        env = dict(self.env)
        env.pop("HANDOFF_COPILOT_BIN")
        env["PATH"] = os.pathsep.join(
            [str(binary.parent) for binary in binaries] + ["/usr/bin", "/bin"]
        )
        return env

    def test_an_explicit_override_wins_over_discovery(self):
        result = self.dry_run(self.env)
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = parse_pairs(result.stdout)
        self.assertEqual(self.env["HANDOFF_COPILOT_BIN"], parsed["codex_bin"])
        self.assertEqual("env", parsed["codex_bin_source"])

    def test_an_env_set_impostor_fails_closed(self):
        """The override is checked too, and it is the likeliest stale path.

        Trusting it launches AWS Copilot CLI with GitHub Copilot flags: the job
        dies with a confusing error and a near-empty log, instead of failing
        legibly at submit.
        """

        env = dict(self.env)
        env["HANDOFF_COPILOT_BIN"] = str(self.aws)
        result = self.dry_run(env)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("HANDOFF_COPILOT_BIN does not point at GitHub Copilot CLI", result.stderr)
        self.assertIn(str(self.aws), result.stderr)
        self.assertIn(AWS_COPILOT_VERSION, result.stderr)
        # Distinguishable from the PATH-discovery refusal, which names PATH.
        self.assertNotIn("on PATH is not GitHub Copilot CLI", result.stderr)

    def test_a_codex_or_claude_override_is_not_identity_checked(self):
        """Neither has the name collision, so neither pays for a --version call."""

        for backend in ("codex", "claude"):
            with self.subTest(backend=backend):
                env = dict(self.env)
                env[f"HANDOFF_{backend.upper()}_BIN"] = str(self.aws)
                result = run_delegate(
                    env, "submit", "--repo", str(self.repo), "--prompt-file",
                    str(self.prompt), "--backend", backend, "--dry-run",
                )
                self.assertEqual((0, ""), (result.returncode, result.stderr))
                self.assertEqual(str(self.aws), parse_pairs(result.stdout)["codex_bin"])

    def test_the_github_cli_is_found_whichever_way_path_is_ordered(self):
        for order in ((self.github, self.aws), (self.aws, self.github)):
            with self.subTest(first=order[0].parent.name):
                result = self.dry_run(self.on_path(*order))
                self.assertEqual((0, ""), (result.returncode, result.stderr))
                parsed = parse_pairs(result.stdout)
                self.assertEqual(str(self.github), parsed["codex_bin"])
                self.assertEqual("path", parsed["codex_bin_source"])
                self.assertEqual(GITHUB_COPILOT_VERSION, parsed["codex_version"])

    def test_an_aws_only_install_fails_closed_and_says_what_it_found(self):
        result = self.dry_run(self.on_path(self.aws))
        self.assertNotEqual(0, result.returncode)
        self.assertIn("not GitHub Copilot CLI", result.stderr)
        self.assertIn(str(self.aws), result.stderr)
        self.assertIn(AWS_COPILOT_VERSION, result.stderr)
        self.assertIn("HANDOFF_COPILOT_BIN", result.stderr)

    def test_no_copilot_at_all_names_the_override(self):
        env = dict(self.env)
        env.pop("HANDOFF_COPILOT_BIN")
        env["PATH"] = "/usr/bin:/bin"
        result = self.dry_run(env)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("copilot CLI not found", result.stderr)
        self.assertIn("HANDOFF_COPILOT_BIN", result.stderr)

if __name__ == "__main__":
    unittest.main()
