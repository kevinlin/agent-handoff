from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path("scripts/delegate-codex.sh")


def make_env(root: Path, codex_body: str) -> dict[str, str]:
    """A clean environment pointing HANDOFF_CODEX_BIN at a fake codex."""

    fake_codex = root / "codex"
    fake_codex.write_text(f"#!/usr/bin/env bash\n{codex_body}", encoding="utf-8")
    fake_codex.chmod(0o755)
    env = os.environ.copy()
    env.update(
        {
            "HOME": str(root / "home"),
            "XDG_CONFIG_HOME": str(root / "xdg"),
            "HANDOFF_CODEX_BIN": str(fake_codex),
        }
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
        include_arbiter: bool = False,
    ) -> str:
        deep_reasoner = ""
        if include_deep_reasoner:
            deep_reasoner = (
                "[hosts.claude_code.identities.deep_reasoner]\n"
                f'backend = "{deep_reasoner_backend}"\n'
                'model = "gpt-deep"\n'
                'effort = "xhigh"\n\n'
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

    def test_claude_backend_fails_with_spawn_guidance(self):
        result, _ = self.run_submit(
            self.config(deep_reasoner_backend="claude"),
            "--role",
            "deep_reasoner",
        )
        self.assertNotEqual(0, result.returncode)
        self.assertIn("backend=claude", result.stderr)
        self.assertIn("spawn the handoff-deep_reasoner subagent", result.stderr)

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


class WorktreeTests(unittest.TestCase):
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
            *arguments,
        )

    def submit(self, *arguments: str) -> str:
        result = self.submit_raw(*arguments)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def read_meta(self, job_id: str) -> dict[str, str]:
        meta = self.repo / ".handoff" / "jobs" / job_id / "meta"
        return parse_pairs(meta.read_text(encoding="utf-8"))

    def cleanup(self, job_id: str):
        return self.delegate("cleanup", job_id, "--repo", str(self.repo))

    def worktree(self, job_id: str) -> Path:
        return self.repo / ".handoff" / "worktrees" / job_id

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
        run_sh = (self.repo / ".handoff" / "jobs" / job_id / "run.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(str(self.worktree(job_id)), run_sh)
        self.assertIn('-C "$WORKDIR"', run_sh)

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

    def test_resume_lands_in_the_parent_worktree(self):
        job_id = self.submit("--worktree", "e2e/T7")
        job = self.repo / ".handoff" / "jobs" / job_id
        # Seed the cached session id and a terminal state so resume proceeds
        # without a real Codex log.
        (job / "session_id").write_text("sess-123", encoding="utf-8")
        (job / "exit_code").write_text("0\n", encoding="utf-8")
        result = self.delegate(
            "resume", job_id, "--repo", str(self.repo), "--prompt-file", str(self.prompt)
        )
        self.assertEqual(0, result.returncode, result.stderr)
        child = result.stdout.strip()
        self.assertEqual(str(self.worktree(job_id)), self.read_meta(child)["worktree"])
        run_sh = (self.repo / ".handoff" / "jobs" / child / "run.sh").read_text(
            encoding="utf-8"
        )
        self.assertIn(str(self.worktree(job_id)), run_sh)


if __name__ == "__main__":
    unittest.main()
