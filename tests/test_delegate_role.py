from __future__ import annotations

import json
import os
import re
import shlex
import shutil
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


# The two flags the antigravity identity check looks for. The real CLI prints its
# help on stderr (HELP), so the fake does too: a check that read stdout alone
# would refuse the real thing.
ANTIGRAVITY_HELP = (
    "Usage of agy:\n"
    "  --conversation                  Resume a previous conversation by ID\n"
    "  --dangerously-skip-permissions  Auto-approve all tool permission requests without prompting\n"
)


def help_shim(help_text: str = ANTIGRAVITY_HELP, exit_code: int = 0) -> str:
    """A fake CLI prologue answering --help on stderr and nothing else."""

    return (
        'if [ "${1:-}" = "--help" ]; then cat >&2 <<\'HELP\'\n'
        f"{help_text}HELP\nexit {exit_code}\nfi\n"
    )


def antigravity_shim() -> str:
    """A fake agy prologue: a bare version number, and help on stderr (VERSION, HELP)."""

    return version_shim("1.3.5") + help_shim()


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
    env["HANDOFF_CURSOR_BIN"] = str(
        write_fake(root / "cursor-agent", version_shim("2026.09.28-64d2043") + codex_body)
    )
    write_fake(root / "ide" / "cursor", "echo 'Cursor IDE launcher' >&2\nexit 64\n")
    # Assigned, not setdefault: a HANDOFF_ANTIGRAVITY_BIN in the developer's shell
    # must never reach a real agy from a test.
    env["HANDOFF_ANTIGRAVITY_BIN"] = str(
        write_fake(root / "agy", antigravity_shim() + codex_body)
    )
    # The decoy is what an IDE launcher named agy prints: help without the two
    # flags, exit 0. It sits in its own directory so a test can put it first on PATH.
    write_fake(
        root / "ide" / "agy",
        "echo 'Usage: antigravity [options] [paths...]'\necho '  --new-window'\nexit 0\n",
    )
    # The real CLI's agy, in a directory a test can put later on PATH than the decoy.
    write_fake(root / "cli" / "agy", antigravity_shim() + codex_body)
    return env


def settle(job: Path) -> bool:
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
                "[deep_reasoner]\n"
                f'backend = "{deep_reasoner_backend}"\n'
                'model = "gpt-deep"\n'
                f'effort = "{deep_reasoner_effort}"\n\n'
            )
        arbiter = ""
        if include_arbiter:
            arbiter = (
                "\n[arbiter]\n"
                'backend = "codex"\n'
                'model = "gpt-arbiter"\n'
                'effort = "high"\n'
            )
        return (
            "schema_version = 2\n"
            "revision = 0\n\n"
            f"{deep_reasoner}"
            "[fast_worker]\n"
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
            f"[{identity}]\n"
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

    def test_role_resolves_from_a_config_with_legacy_identity_headers(self):
        # Releases before 3.9.2 wrote identity headers with a host prefix. The
        # config is read as it stands; nothing rewrites it on a submit.
        legacy = (
            "schema_version = 2\n"
            "revision = 0\n\n"
            "[hosts.claude_code.identities.fast_worker]\n"
            'backend = "claude"\n'
            'model = "legacy-model"\n'
            'effort = "medium"\n'
        )
        result, repo = self.run_submit(legacy, "--role", "fast_worker")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = self.parsed(result.stdout)
        expected = {
            "role": "fast_worker",
            "backend": "claude",
            "model": "legacy-model",
            "effort": "medium",
            "backend_source": "config:project",
            "model_source": "config:project",
            "effort_source": "config:project",
        }
        self.assertEqual(expected, {key: parsed[key] for key in expected})
        self.assertEqual(legacy, (repo / ".handoff" / "config.toml").read_text(encoding="utf-8"))

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
        self.assertEqual("dontAsk", parsed["permission_mode"])

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
    EFFORT = "high"
    # When set, submit_raw passes it as --model. Antigravity refuses an empty
    # model (its resume passes the model again), so its shared submissions need one.
    MODEL = None
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
        return settle(job)

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
            "--effort",
            self.EFFORT,
            *(["--model", self.MODEL] if self.MODEL else []),
            *arguments,
        )

    def submit(self, *arguments: str) -> str:
        result = self.submit_raw(*arguments)
        self.assertEqual(0, result.returncode, result.stderr)
        return result.stdout.strip()

    def resume(self, job_id: str):
        return self.delegate(
            "resume", job_id, "--repo", str(self.repo), "--prompt-file", str(self.prompt)
        )

    def test_model_and_resume_id_are_literal_arguments(self):
        marker = self.root / "injected"
        values = ["ordinary-model", f'$(touch {marker})', f'`touch {marker}`', 'model"; false; #']
        worker = Path(self.env[f"HANDOFF_{self.BACKEND.upper()}_BIN"])
        # An antigravity worker must also answer --help with its two flags, or the
        # identity check would refuse the shim before quoting is ever tested.
        write_fake(worker, version_shim(GITHUB_COPILOT_VERSION if self.BACKEND == "copilot" else "fixture")
                   + (help_shim() if self.BACKEND == "antigravity" else "")
                   + "printf '%s\\n' \"$@\"\n")
        for value in values:
            with self.subTest(value=value):
                job_id = self.submit("--model", value)
                job = self.job_dir(job_id)
                self.assertTrue(self.await_exit(job))
                self.assertFalse(marker.exists())
                self.assertIn(value, (job / "log.jsonl").read_text().splitlines())
                (job / "session_id").write_text(value)
                result = self.resume(job_id)
                self.assertEqual(0, result.returncode, result.stderr)
                resumed = self.job_dir(result.stdout.strip())
                self.assertTrue(self.await_exit(resumed))
                self.assertFalse(marker.exists())
                self.assertIn(value, (resumed / "log.jsonl").read_text().splitlines())

    def test_multiline_model_and_invalid_resume_effort_are_refused(self):
        for value in ("model\npermission_posture=allow-all", "model\rname"):
            result = self.submit_raw("--model", value)
            self.assertNotEqual(0, result.returncode)
            self.assertIn("single line", result.stderr)
        job_id = self.submit()
        job = self.job_dir(job_id)
        self.assertTrue(self.await_exit(job))
        (job / "session_id").write_text("fixture-session")
        meta = job / "meta"
        meta.write_text(meta.read_text().replace(f"effort={self.EFFORT}", "effort=invalid"))
        result = self.resume(job_id)
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid --effort", result.stderr)
        self.assertFalse(self.job_dir(f"{job_id}-r2").exists())

    def configure_review(self, spec: int, implementation: int):
        path = self.repo / ".handoff" / "config.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            "schema_version = 2\nrevision = 0\n"
            f"[review]\nspec_max_rounds = {spec}\nimplementation_max_rounds = {implementation}\n",
            encoding="utf-8",
        )

    def test_implementation_chain_stops_at_the_default_cap(self):
        job_id = self.submit("--label", "T1")
        self.finish(job_id)
        second = self.resume(job_id)
        self.assertEqual(0, second.returncode, second.stderr)
        self.assertEqual(f"{job_id}-r2", second.stdout.strip())
        self.finish(f"{job_id}-r2")
        third = self.resume(f"{job_id}-r2")
        self.assertEqual(0, third.returncode, third.stderr)
        self.assertEqual(f"{job_id}-r3", third.stdout.strip())
        self.finish(f"{job_id}-r3")
        refused = self.resume(f"{job_id}-r3")
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("review cap reached", refused.stderr)
        self.assertIn("implementation gate", refused.stderr)
        self.assertIn("escalate to the arbiter", refused.stderr)
        self.assertFalse(self.job_dir(f"{job_id}-r4").exists())

    def test_spec_review_chain_uses_the_spec_cap(self):
        job_id = self.submit("--label", "spec-review", "--read-only")
        self.finish(job_id)
        refused = self.resume(job_id)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("spec gate", refused.stderr)
        self.assertFalse(self.job_dir(f"{job_id}-r2").exists())

    def test_configured_caps_are_honoured(self):
        self.configure_review(spec=2, implementation=1)
        spec = self.submit("--label", "spec-review", "--read-only")
        self.finish(spec)
        allowed = self.resume(spec)
        self.assertEqual(0, allowed.returncode, allowed.stderr)
        implementation = self.submit("--label", "T2")
        self.finish(implementation)
        self.assertIn("implementation gate", self.resume(implementation).stderr)

    def test_a_fresh_label_ending_in_a_round_suffix_is_round_one(self):
        job_id = self.submit("--label", "fix-r2")
        self.finish(job_id)
        result = self.resume(job_id)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual(f"{job_id}-r2", result.stdout.strip())

    def test_a_missing_ancestor_fails_closed(self):
        job_id = self.submit("--label", "T3")
        self.finish(job_id)
        child = self.resume(job_id).stdout.strip()
        self.finish(child)
        shutil.rmtree(self.job_dir(job_id))
        refused = self.resume(child)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("ancestor job", refused.stderr)

    def test_a_resume_with_no_parent_fails_closed(self):
        job_id = self.submit("--label", "T5")
        self.finish(job_id)
        child = self.resume(job_id).stdout.strip()
        self.finish(child)
        meta = self.job_dir(child) / "meta"
        meta.write_text(
            "".join(
                line for line in meta.read_text(encoding="utf-8").splitlines(True)
                if not line.startswith("parent=")
            ),
            encoding="utf-8",
        )
        refused = self.resume(child)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("no parent", refused.stderr)

    def test_a_parent_cycle_fails_closed(self):
        job_id = self.submit("--label", "T6")
        self.finish(job_id)
        child = self.resume(job_id).stdout.strip()
        self.finish(child)
        root_meta = self.job_dir(job_id) / "meta"
        root_meta.write_text(
            root_meta.read_text(encoding="utf-8").replace("mode=fresh", "mode=resume")
            + f"parent={child}\n",
            encoding="utf-8",
        )
        refused = self.resume(child)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("loops", refused.stderr)

    def test_an_invalid_config_blocks_a_fix_round(self):
        job_id = self.submit("--label", "T4")
        self.finish(job_id)
        self.configure_review(spec=0, implementation=3)
        refused = self.resume(job_id)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("review caps", refused.stderr)

    def configure_posture(self, posture):
        path = self.repo / ".handoff" / "config.toml"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            'schema_version = 2\nrevision = 0\n'
            '[fast_worker]\n'
            f'backend = "{self.BACKEND}"\nmodel = "fixture"\neffort = "{self.EFFORT}"\n'
            f'permission_mode = "{posture}"\n', encoding="utf-8"
        )

    def configured_submit(self, posture, *arguments):
        self.configure_posture(posture)
        return self.submit("--role", "fast_worker", *arguments)

    def assert_posture_argv(self, job_id, posture, read_only=False, resume=False):
        run_sh = (self.job_dir(job_id) / "run.sh").read_text()
        meta = self.read_meta(job_id)
        self.assertEqual(posture, meta["permission_posture"])
        self.assertEqual(str(read_only).lower(), meta["read_only"])
        if self.BACKEND == "codex":
            self.assertEqual(posture == "allow-all" and not read_only,
                             "--dangerously-bypass-approvals-and-sandbox" in run_sh)
            if read_only:
                self.assertIn('sandbox_mode=\"read-only\"' if resume else "-s read-only", run_sh)
            else:
                self.assertNotIn("sandbox_mode", run_sh)
                self.assertNotIn(" -s ", run_sh)
        elif self.BACKEND == "claude":
            mode = "plan" if read_only else ("dontAsk" if posture == "default" else "bypassPermissions")
            self.assertIn(f"--permission-mode {mode}", run_sh)
            self.assertEqual(not read_only and posture == "default", "--allowed-tools" in run_sh)
        elif self.BACKEND == "copilot":
            self.assertEqual(read_only, "--mode plan" in run_sh)
            self.assertEqual(not read_only, "--allow-all-tools" in run_sh)
            for flag in ("--allow-all-paths", "--allow-all-urls"):
                self.assertEqual(posture == "allow-all" and not read_only, flag in run_sh)
        elif self.BACKEND == "cursor":
            self.assertEqual(read_only, "--mode plan" in run_sh)
            self.assertEqual(not read_only, "--force" in run_sh)  # risk-ok: Cursor CLI flag name
            self.assertEqual("plan" if read_only else "force", meta["permission_mode"])
        elif self.BACKEND == "antigravity":
            # RO2: plan mode with the skip flag wrote files, so read-only never carries it.
            self.assertEqual(read_only, "--mode plan" in run_sh)
            self.assertEqual(not read_only, "--dangerously-skip-permissions" in run_sh)
            self.assertIn("--print-timeout 0s", run_sh)
            self.assertEqual("plan" if read_only else "skip-permissions", meta["permission_mode"])
        else:
            self.fail(f"no posture assertions for backend {self.BACKEND}")

    def test_configured_postures_and_resume_inherit_authority(self):
        for posture in ("default", "allow-all"):
            for read_only in (False, True):
                with self.subTest(posture=posture, read_only=read_only):
                    job_id = self.configured_submit(posture, *(["--read-only"] if read_only else []))
                    self.assert_posture_argv(job_id, posture, read_only)
                    self.assertEqual("read-only" if read_only else "config:project",
                                     self.read_meta(job_id)["permission_posture_source"])
                    self.finish(job_id)
                    # Config changes must not change the authority of a fix round.
                    self.configure_posture("default" if posture == "allow-all" else "allow-all")
                    result = self.delegate("resume", job_id, "--repo", str(self.repo),
                                           "--prompt-file", str(self.prompt))
                    self.assertEqual(0, result.returncode, result.stderr)
                    child = result.stdout.strip()
                    self.assert_posture_argv(child, posture, read_only, resume=True)
                    self.assertEqual("read-only" if read_only else "parent",
                                     self.read_meta(child)["permission_posture_source"])
                    if read_only:
                        self.assertNotIn("permission checks bypassed", result.stderr)

    def test_legacy_parent_missing_posture_resumes_default(self):
        job_id = self.configured_submit("allow-all")
        job = self.finish(job_id)
        meta = job / "meta"
        meta.write_text("".join(line for line in meta.read_text().splitlines(True)
                                if not line.startswith("permission_posture=")))
        result = self.delegate("resume", job_id, "--repo", str(self.repo),
                               "--prompt-file", str(self.prompt))
        self.assertEqual(0, result.returncode, result.stderr)
        self.assert_posture_argv(result.stdout.strip(), "default", resume=True)
        self.assertEqual("default", self.read_meta(result.stdout.strip())["permission_posture_source"])

    def test_effective_mode_controls_warnings(self):
        self.configure_posture("allow-all")
        result = self.submit_raw("--role", "fast_worker")
        self.assertIn("permission checks bypassed", result.stderr)
        result = self.submit_raw("--role", "fast_worker", "--read-only")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("permission checks bypassed", result.stderr)
        if self.BACKEND == "claude":
            self.env["HANDOFF_CLAUDE_PERMISSION_MODE"] = "dontAsk"
            result = self.submit_raw("--role", "fast_worker")
            self.assertNotIn("permission checks bypassed", result.stderr)
            self.configure_posture("default")
            self.env["HANDOFF_CLAUDE_PERMISSION_MODE"] = "bypassPermissions"
            result = self.submit_raw("--role", "fast_worker")
            self.assertIn("permission checks bypassed", result.stderr)
            self.assertEqual("default", self.read_meta(result.stdout.strip())["permission_posture"])
            self.assertEqual("env", self.read_meta(result.stdout.strip())["permission_posture_source"])

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
                "context_lost",
            },
            set(payload),
        )
        # Measured only on an antigravity resume; every fresh job reads null.
        self.assertIsNone(payload["context_lost"])
        self.assertEqual(0, payload["permission_denied"])
        self.assertEqual("sess-fixture", payload["session_id"])
        self.assertEqual("work done", payload["agent_message"])
        self.assertEqual(["pytest -q"], payload["commands"])
        self.assertTrue(payload["usage"])


    def test_meta_records_the_permission_mode_the_worker_runs_under(self):
        meta = self.read_meta(self.submit())
        self.assertEqual(self.PERMISSION_MODE, meta.get("permission_mode", ""))

    def denied_log_lines(self):
        """The stream the shared denial test writes, in this backend's own shape.

        The default is two standalone denial events around the clean stream, and
        the order matters: Copilot names its first denial `unknown` because that
        denial precedes the tool start (DENIED_TOOLS). A backend that reports
        denials another way overrides this.
        """

        return (self.DENIED_LINE, *self.LOG_LINES, self.DENIED_LINE)

    def test_result_reports_denials_that_the_exit_code_hides(self):
        """A blocked check still leaves exit 0, so the count is the only signal.

        This is the v3.5.1 defect in miniature: a worker that could not run its
        own acceptance checks reported success, and nothing downstream noticed.
        """

        job_id = self.submit()
        job = self.finish(job_id)
        job.joinpath("log.jsonl").write_text(
            "\n".join(self.denied_log_lines()) + "\n", encoding="utf-8"
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
    PERMISSION_MODE = "dontAsk"
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

    def test_worker_default_uses_dont_ask_and_worker_tools(self):
        run_sh = self.exec_line(self.submit())
        self.assertIn("--permission-mode dontAsk", run_sh)
        self.assertIn("--allowed-tools Read Glob Grep Edit Write Bash", run_sh)
        self.assertIn("--permission-prompts none", run_sh)

    def test_submit_warns_that_the_worker_is_unsupervised(self):
        self.env["HANDOFF_CLAUDE_PERMISSION_MODE"] = "bypassPermissions"
        result = self.submit_raw()
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("permission checks bypassed", result.stderr)
        self.assertIn("HANDOFF_CLAUDE_PERMISSION_MODE=dontAsk", result.stderr)
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
        self.assertEqual("dontAsk", self.read_meta(child)["permission_mode"])
        self.assertIn("--permission-mode dontAsk", self.exec_line(child))


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

    def test_export_flags_are_banned_and_blanket_permissions_require_allow_all(self):
        # The three allow-all flags are equivalent to --allow-all / --yolo.
        for posture in ("default", "allow-all"):
            for read_only in (False, True):
                job_id = self.configured_submit(posture, *(["--read-only"] if read_only else []))
                run_sh = self.exec_line(job_id)
                for flag in ("--share", "--share-gist", "--worktree", "--enable-memory",
                             "--add-dir", "--max-ai-credits"):
                    with self.subTest(flag=flag, posture=posture, read_only=read_only):
                        self.assertNotRegex(run_sh, rf"{flag}(?![-\w])")
                blanket = ("--allow-all-paths" in run_sh and "--allow-all-urls" in run_sh
                           and "--allow-all-tools" in run_sh)
                self.assertEqual(posture == "allow-all" and not read_only, blanket)
                if read_only or posture == "default":
                    for flag in ("--yolo", "--allow-all", "--allow-all-paths", "--allow-all-urls"):
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
        self.assertIn(f"SESSION_ID={assigned}\n", self.exec_line(result.stdout.strip()))
        self.assertIn('--resume "$SESSION_ID"', self.exec_line(result.stdout.strip()))

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


class CursorWorktreeTests(BackendLifecycle, unittest.TestCase):
    BACKEND = "cursor"
    EFFORT = "model"
    # cursor-agent takes cwd from the shell and the workspace from --workspace.
    WORKDIR_MARKER = '--workspace "$WORKDIR"'
    PERMISSION_MODE = "force"
    # Call ids embed a newline, escaped in the JSON.
    DENIED_LINE = (
        '{"type":"tool_call","subtype":"completed","call_id":"call_D\\nfc_9",'
        '"tool_call":{"shellToolCall":{"result":{"rejected":{"command":"curl https://example.com",'
        '"workingDirectory":"/r","reason":"","isReadonly":false}}}},"session_id":"sess-fixture"}'
    )
    # Named by tool kind.
    DENIED_TOOLS = ["shellToolCall"]
    LOG_LINES = (
        '{"type":"system","subtype":"init","apiKeySource":"login","cwd":"/r",'
        '"session_id":"sess-fixture","model":"GPT-5.4 Mini Low","permissionMode":"default"}',
        '{"type":"thinking","subtype":"delta","text":"planning","session_id":"sess-fixture"}',
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text",'
        '"text":"looking"}]},"session_id":"sess-fixture"}',
        '{"type":"tool_call","subtype":"started","call_id":"call_A\\nfc_1","tool_call":'
        '{"shellToolCall":{"args":{"command":"pytest -q"}}},"session_id":"sess-fixture"}',
        '{"type":"tool_call","subtype":"completed","call_id":"call_A\\nfc_1","tool_call":'
        '{"shellToolCall":{"result":{"success":{"command":"pytest -q","exitCode":0,'
        '"stdout":"ok","stderr":""}}}},"session_id":"sess-fixture"}',
        '{"type":"assistant","message":{"role":"assistant","content":[{"type":"text",'
        '"text":"work done"}]},"session_id":"sess-fixture"}',
        # result.result concatenates every assistant message.
        '{"type":"result","subtype":"success","is_error":false,"result":"lookingwork done",'
        '"session_id":"sess-fixture","usage":{"inputTokens":11,"outputTokens":7,'
        '"cacheReadTokens":0,"cacheWriteTokens":0}}',
    )

    def exec_line(self, job_id: str) -> str:
        return (self.job_dir(job_id) / "run.sh").read_text(encoding="utf-8")

    def bare_submit(self, *arguments: str):
        """A submit with neither the class's --backend nor its --effort."""

        return self.delegate(
            "submit", "--repo", str(self.repo), "--prompt-file", str(self.prompt), *arguments
        )

    def no_jobs(self) -> bool:
        return not any((self.repo / ".handoff" / "jobs").glob("job-*"))

    def test_fresh_and_resume_command_lines(self):
        job_id = self.submit("--model", "gpt-5.4-mini-low")
        run_sh = self.exec_line(job_id)
        self.assertIn('cd "$WORKDIR"\n', run_sh)
        self.assertIn(
            '"$CODEX_BIN" -p "$PROMPT" --output-format stream-json --trust '
            '--workspace "$WORKDIR" --model "$MODEL" --force '  # risk-ok: Cursor CLI flag name
            '>"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null',
            run_sh,
        )
        self.finish(job_id)
        run_sh = self.exec_line(self.resume(job_id).stdout.strip())
        self.assertIn('cd "$WORKDIR"\n', run_sh)
        self.assertIn(
            '"$CODEX_BIN" -p "$PROMPT" --output-format stream-json --trust '
            '--resume "$SESSION_ID" --force '  # risk-ok: Cursor CLI flag name
            '>"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null',
            run_sh,
        )
        self.assertNotIn("--model", run_sh)
        self.assertNotIn("--workspace", run_sh)
        # R3.5: Handoff writes nothing into Cursor's own configuration.
        for text in (self.exec_line(job_id), run_sh):
            self.assertNotIn(".cursor", text)
            self.assertNotIn("CURSOR_CONFIG_DIR", text)
        self.assertFalse((self.repo / ".cursor").exists())

    def test_a_fix_round_whose_parent_binary_is_gone_resolves_again(self):
        """R2.1: resume reuses the parent's binary only while it is executable."""

        job_id = self.submit()
        self.finish(job_id)
        meta = self.job_dir(job_id) / "meta"
        meta.write_text(meta.read_text(encoding="utf-8").replace(
            self.env["HANDOFF_CURSOR_BIN"], str(self.root / "gone" / "cursor-agent")),
            encoding="utf-8")
        result = self.resume(job_id)
        self.assertEqual(0, result.returncode, result.stderr)
        child = self.read_meta(result.stdout.strip())
        self.assertEqual((self.env["HANDOFF_CURSOR_BIN"], "env"),
                         (child["codex_bin"], child["codex_bin_source"]))

    def test_the_last_assistant_and_the_last_result_win_literally(self):
        job_id = self.submit()
        job = self.finish(job_id)
        empty = ('{"type":"assistant","message":{"role":"assistant","content":[]},'
                 '"session_id":"sess-fixture"}')
        later = ('{"type":"result","subtype":"success","result":"x","session_id":"sess-fixture",'
                 '"usage":{"inputTokens":99,"outputTokens":1,"cacheReadTokens":0,"cacheWriteTokens":0}}')
        job.joinpath("log.jsonl").write_text(
            "\n".join((*self.LOG_LINES, empty, later)) + "\n", encoding="utf-8")
        payload = json.loads(
            self.delegate("result", job_id, "--repo", str(self.repo), "--json").stdout
        )
        self.assertEqual(("", 99), (payload["agent_message"], payload["usage"]["inputTokens"]))

    def test_flags_handoff_owns_or_forbids_never_appear(self):
        for posture in ("default", "allow-all"):
            for read_only in (False, True):
                job_id = self.configured_submit(posture, *(["--read-only"] if read_only else []))
                self.finish(job_id)
                child = self.resume(job_id).stdout.strip()
                for run_sh in (self.exec_line(job_id), self.exec_line(child)):
                    for flag in ("--worktree", "--sandbox", "--approve-mcps", "--api-key",
                                 "--stream-partial-output", "--yolo"):
                        with self.subTest(flag=flag, posture=posture, read_only=read_only):
                            self.assertNotRegex(run_sh, rf"{flag}(?![-\w])")

    def test_read_only_is_plan_mode_and_never_force(self):
        job_id = self.submit("--read-only")
        run_sh = self.exec_line(job_id)
        self.assertIn("--mode plan", run_sh)
        self.assertNotIn("--force", run_sh)  # risk-ok: Cursor CLI flag name
        self.assertEqual("plan", self.read_meta(job_id)["permission_mode"])

    def test_both_postures_force_and_every_writing_job_warns(self):
        for posture in ("default", "allow-all"):
            with self.subTest(posture=posture):
                self.configure_posture(posture)
                result = self.submit_raw("--role", "fast_worker")
                self.assertEqual(0, result.returncode, result.stderr)
                job_id = result.stdout.strip()
                self.assertIn("--force", self.exec_line(job_id))  # risk-ok: Cursor CLI flag name
                meta = self.read_meta(job_id)
                self.assertEqual(("force", posture),
                                 (meta["permission_mode"], meta["permission_posture"]))
                self.assertIn("cursor worker running with permission checks bypassed", result.stderr)
                self.assertIn(".cursor/cli.json", result.stderr)
                self.assertIn("--read-only", result.stderr)
        result = self.submit_raw("--read-only")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("permission checks bypassed", result.stderr)

    def test_an_effort_other_than_model_is_refused(self):
        result = self.bare_submit("--backend", "cursor", "--effort", "high")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid --effort for cursor: high", result.stderr)
        self.assertIn("Cursor carries effort in the model id", result.stderr)
        self.configure_posture("default")
        config = self.repo / ".handoff" / "config.toml"
        config.write_text(config.read_text().replace('effort = "model"', 'effort = "high"'))
        result = self.bare_submit("--role", "fast_worker")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Cursor carries effort in the model id", result.stderr)
        self.assertTrue(self.no_jobs())

    def test_a_role_less_job_gets_the_only_effort(self):
        result = self.bare_submit("--backend", "cursor", "--dry-run")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        self.assertEqual("model", parse_pairs(result.stdout)["effort"])

    def test_auto_is_refused_at_submit(self):
        result = self.submit_raw("--model", "auto")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("'auto' is refused on a cursor job", result.stderr)
        self.assertIn("handoff-config.py set", result.stderr)
        self.configure_posture("default")
        config = self.repo / ".handoff" / "config.toml"
        config.write_text(config.read_text().replace('model = "fixture"', 'model = "auto"'))
        result = self.bare_submit("--role", "fast_worker")
        self.assertIn("'auto' is refused on a cursor job", result.stderr)
        self.assertTrue(self.no_jobs())

    def test_a_backend_contradicting_the_role_is_refused(self):
        self.configure_posture("default")  # fast_worker is cursor-backed
        result = self.bare_submit("--role", "fast_worker", "--backend", "codex")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("contradicts identity fast_worker, configured as backend=cursor", result.stderr)
        (self.repo / ".handoff" / "config.toml").write_text(
            'schema_version = 2\nrevision = 0\n'
            '[fast_worker]\n'
            'backend = "codex"\nmodel = "gpt-fast"\neffort = "high"\n',
            encoding="utf-8",
        )
        result = self.bare_submit("--role", "fast_worker", "--backend", "cursor")
        self.assertIn("contradicts identity fast_worker, configured as backend=codex", result.stderr)
        self.assertTrue(self.no_jobs())

    def test_a_model_cursor_rejects_at_launch_is_a_failed_job(self):
        """Cursor checks the slug when the worker starts, after the job dir exists."""

        refusal = "Cannot use this model: no-such-model. Available models: auto, gpt-5.4-mini-low"
        write_fake(Path(self.env["HANDOFF_CURSOR_BIN"]),
                   version_shim("fixture") + f"printf '%s\\n' '{refusal}' >&2\nexit 1\n")
        job_id = self.submit("--model", "no-such-model")
        job = self.job_dir(job_id)
        self.assertTrue(self.await_exit(job))
        status = self.delegate("status", job_id, "--repo", str(self.repo))
        self.assertIn("state: FAILED", status.stdout)
        self.assertEqual(refusal + "\n", (job / "stderr.log").read_text(encoding="utf-8"))
        self.assertEqual("", (job / "log.jsonl").read_text(encoding="utf-8"))
        payload = json.loads(
            self.delegate("result", job_id, "--repo", str(self.repo), "--json").stdout
        )
        self.assertEqual(("", {}), (payload["session_id"], payload["usage"]))
        refused = self.resume(job_id)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("no session id found", refused.stderr)

    def test_a_killed_job_resumes_on_the_session_its_log_names(self):
        """Wiring only: that Cursor restores the context is probe G's evidence."""

        job_id = self.submit()
        job = self.finish(job_id)
        # Killed while its first tool call ran: no completed event, no result.
        job.joinpath("log.jsonl").write_text("\n".join(self.LOG_LINES[:4]) + "\n", encoding="utf-8")
        job.joinpath("exit_code").write_text("137\n", encoding="utf-8")
        result = self.resume(job_id)
        self.assertEqual(0, result.returncode, result.stderr)
        run_sh = self.exec_line(result.stdout.strip())
        self.assertIn("SESSION_ID=sess-fixture\n", run_sh)
        self.assertIn('--resume "$SESSION_ID"', run_sh)

    def test_thinking_never_reaches_status_or_result(self):
        job_id = self.submit()
        job = self.finish(job_id)
        thinking = '{"type":"thinking","subtype":"delta","text":"more","session_id":"sess-fixture"}'
        job.joinpath("log.jsonl").write_text(
            "\n".join((*self.LOG_LINES, thinking)) + "\n", encoding="utf-8"
        )
        status = self.delegate("status", job_id, "--repo", str(self.repo))
        self.assertIn("last_event: result", status.stdout)
        payload = json.loads(
            self.delegate("result", job_id, "--repo", str(self.repo), "--json").stdout
        )
        # Never result.result, which is every assistant message run together.
        self.assertEqual("work done", payload["agent_message"])
        self.assertEqual([], payload["errors"])

    def test_an_unexpected_tool_call_shape_is_skipped_not_fatal(self):
        job_id = self.submit()
        job = self.finish(job_id)
        odd = (
            '{"type":"tool_call","subtype":"completed","call_id":"x","tool_call":'
            '{"shellToolCall":null},"session_id":"sess-fixture"}',
            '{"type":"tool_call","subtype":"completed","call_id":"y","tool_call":'
            '{"newToolCall":{"result":"rejected"}},"session_id":"sess-fixture"}',
            '{"type":"tool_call","subtype":"started","call_id":"z","tool_call":[],'
            '"session_id":"sess-fixture"}',
        )
        job.joinpath("log.jsonl").write_text(
            "\n".join((*self.LOG_LINES[:-1], *odd, self.LOG_LINES[-1])) + "\n", encoding="utf-8"
        )
        result = self.delegate("result", job_id, "--repo", str(self.repo), "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual((0, ["pytest -q"]), (payload["permission_denied"], payload["commands"]))
        status = self.delegate("status", job_id, "--repo", str(self.repo))
        self.assertNotIn("permission_denied", status.stdout)


class CursorBinaryDiscoveryTests(unittest.TestCase):
    """The backend is `cursor`, its agent is `cursor-agent`, and `cursor` is the IDE."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.prompt = self.root / "prompt.md"
        self.prompt.write_text("test prompt\n", encoding="utf-8")
        self.env = make_env(self.root, "exit 0\n")
        self.agent = Path(self.env.pop("HANDOFF_CURSOR_BIN"))
        self.ide = self.root / "ide" / "cursor"
        self.generic = write_fake(self.root / "generic" / "agent", "exit 0\n")

    def on_path(self, *directories: Path) -> dict[str, str]:
        env = dict(self.env)
        env["PATH"] = os.pathsep.join([*map(str, directories), "/usr/bin", "/bin"])
        return env

    def submit(self, env: dict[str, str], *extra: str):
        return run_delegate(
            env, "submit", "--repo", str(self.repo), "--prompt-file", str(self.prompt),
            "--backend", "cursor", "--effort", "model", *extra,
        )

    def test_the_ide_launcher_earlier_on_path_is_never_the_worker(self):
        result = self.submit(self.on_path(self.ide.parent, self.agent.parent))
        self.assertEqual(0, result.returncode, result.stderr)
        job = self.repo / ".handoff" / "jobs" / result.stdout.strip()
        self.addCleanup(settle, job)
        meta = parse_pairs((job / "meta").read_text(encoding="utf-8"))
        self.assertEqual((str(self.agent), "path"), (meta["codex_bin"], meta["codex_bin_source"]))

    def test_no_cursor_agent_fails_and_names_the_override(self):
        # `cursor` and `agent` are both on PATH; neither may be taken.
        result = self.submit(self.on_path(self.ide.parent, self.generic.parent), "--dry-run")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("cursor-agent", result.stderr)
        self.assertIn("HANDOFF_CURSOR_BIN", result.stderr)
        self.assertFalse((self.repo / ".handoff").exists())

    def test_the_override_wins_over_path(self):
        env = self.on_path(self.ide.parent)
        env["HANDOFF_CURSOR_BIN"] = str(self.agent)
        result = self.submit(env, "--dry-run")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        parsed = parse_pairs(result.stdout)
        self.assertEqual((str(self.agent), "env"), (parsed["codex_bin"], parsed["codex_bin_source"]))



AGY_SID = "sess-fixture"
ZERO_USAGE = {
    "input_tokens": 0,
    "output_tokens": 0,
    "thinking_tokens": 0,
    "cache_read_tokens": 0,
    "total_tokens": 0,
}
# What status and result print for a log with no result event (R5.1, R5.2).
UNKNOWN_DENIALS = "permission_denied: unknown (no result event; agy reports denials only when a run ends)"
LOST_CONTEXT = (
    "context_lost: true (round ran without its parent's context; "
    "resubmit with the full packet)"
)
# MUNKNOWN: a model agy rejects at launch is one result event with an empty id.
REJECTION_ERROR = (
    'invalid model selection (--model "no-such-model" --effort ""): model no-such-model '
    "is not recognized as a known model or custom model in settings\n"
    "Available models:\n  Gemini 3.8 Flash (High)\n  Gemini 3.8 Flash (Low)"
)
# TIMEOUTR: the only trace of a timed-out run that exits 0 with status SUCCESS.
TIMEOUT_LINE = "[agy] print timeout after 2s with turn in progress; returning partial output"


def agy_usage(input_tokens, output_tokens, thinking_tokens=0, cache_read_tokens=0):
    """The five snake_case counters agy reports; total is input plus output (P3)."""

    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "thinking_tokens": thinking_tokens,
        "cache_read_tokens": cache_read_tokens,
        "total_tokens": input_tokens + output_tokens,
    }


def agy_init(conversation_id=AGY_SID):
    return json.dumps({
        "event": "init",
        "conversation_id": conversation_id,
        "init": {"model": "gemini-3.8-flash-low", "cwd": "/r", "permission_mode": "always-proceed"},
    })


def agy_step(index, state, step_type, conversation_id=AGY_SID, **fields):
    return json.dumps({
        "event": "step_update",
        "step_update": {
            "conversation_id": conversation_id,
            "step_index": index,
            "state": state,
            "step_type": step_type,
            **fields,
        },
    })


def agy_tool(index, name, parameters, output=None, error=None, state="DONE",
             conversation_id=AGY_SID):
    """The ACTIVE and the closing line of one tool step, paired by step_index (P3)."""

    info = {"name": name, "parameters": parameters}
    closing = dict(info)
    if output is not None:
        closing["output"] = output
    if error is not None:
        closing["error"] = error
    return (
        agy_step(index, "ACTIVE", "tool", conversation_id, tool_name=name, tool_info=info),
        agy_step(index, state, "tool", conversation_id, tool_name=name,
                 duration_seconds=0.3, tool_info=closing),
    )


def agy_result(response="", usage=None, status="SUCCESS", error=None, denied=None,
               conversation_id=AGY_SID, turns=1):
    result = {
        "conversation_id": conversation_id,
        "status": status,
        "response": response,
        "duration_seconds": 1.0,
        "num_turns": turns,
        "usage": ZERO_USAGE if usage is None else usage,
    }
    if error is not None:
        result["error"] = error
    if denied is not None:
        result["denied_actions"] = denied
    return json.dumps({"event": "result", "result": result})


class AntigravityWorktreeTests(BackendLifecycle, unittest.TestCase):
    BACKEND = "antigravity"
    EFFORT = "model"
    MODEL = "gemini-3.8-flash-low"
    # agy takes its working directory from the shell and has no workspace flag.
    WORKDIR_MARKER = 'cd "$WORKDIR"'
    PERMISSION_MODE = "skip-permissions"
    DENIED_ACTIONS = [
        {"action": "write_file", "display_name": "WriteToFile"},
        {"action": "command", "display_name": "RunCommand"},
    ]
    # `result` names a denial by display_name, sorted.
    DENIED_TOOLS = ["RunCommand", "WriteToFile"]
    # P3's shapes: init, step_update agent and tool steps with usage, and a result.
    # The last step carries the final words on its DONE line, as P3's did.
    LOG_LINES = (
        agy_init(),
        agy_step(0, "DONE", "user_input"),
        agy_step(1, "ACTIVE", "agent_response", text_delta="looking"),
        agy_step(1, "DONE", "agent_response", text_delta="\n", duration_seconds=1.5,
                 usage=agy_usage(11, 3)),
        *agy_tool(2, "run_command", {"CommandLine": "pytest -q"}, output="ok\r\n"),
        agy_step(3, "ACTIVE", "agent_response", text_delta="work "),
        agy_step(3, "DONE", "agent_response", text_delta="done", duration_seconds=2.0,
                 usage=agy_usage(17, 4, 2, 5)),
        agy_result(response="looking\nwork done", usage=agy_usage(28, 7, 2, 5)),
    )

    def denied_log_lines(self):
        # Antigravity reports denials only as a list on its terminal result. P0's
        # shape is kept: the refused write ends as a DONE tool step with no error,
        # so only denied_actions can say it was refused.
        return (
            *self.LOG_LINES[:-1],
            *agy_tool(4, "write_to_file", {"TargetFile": "/r/tracked.txt"}),
            agy_result(response="looking\nwork done", usage=agy_usage(28, 7, 2, 5),
                       denied=self.DENIED_ACTIONS),
        )

    def exec_line(self, job_id: str) -> str:
        return (self.job_dir(job_id) / "run.sh").read_text(encoding="utf-8")

    def bare_submit(self, *arguments: str):
        """A submit with neither the class's --backend, --effort, nor --model."""

        return self.delegate(
            "submit", "--repo", str(self.repo), "--prompt-file", str(self.prompt), *arguments
        )

    def no_jobs(self) -> bool:
        return not any((self.repo / ".handoff" / "jobs").glob("job-*"))

    def on_path(self, *directories: Path) -> dict[str, str]:
        env = dict(self.env)
        env.pop("HANDOFF_ANTIGRAVITY_BIN")
        env["PATH"] = os.pathsep.join([*map(str, directories), "/usr/bin", "/bin"])
        return env

    def submit_with(self, env: dict[str, str], *extra: str):
        return run_delegate(
            env, "submit", "--repo", str(self.repo), "--prompt-file", str(self.prompt),
            "--backend", "antigravity", "--effort", "model", "--model", self.MODEL, *extra,
        )

    def install_worker(self, body: str):
        """Replace the fake agy that HANDOFF_ANTIGRAVITY_BIN names."""

        write_fake(Path(self.env["HANDOFF_ANTIGRAVITY_BIN"]), antigravity_shim() + body)

    def install_rejecting_worker(self):
        """MUNKNOWN: one result with an empty id on stdout, the message on stderr, exit 1."""

        stdout = self.root / "rejection.stdout"
        stderr = self.root / "rejection.stderr"
        stdout.write_text(agy_result(status="ERROR", error=REJECTION_ERROR, conversation_id="",
                                     turns=0) + "\n", encoding="utf-8")
        stderr.write_text(f"error: {REJECTION_ERROR}\n", encoding="utf-8")
        self.install_worker(f'cat "{stdout}"\ncat "{stderr}" >&2\nexit 1\n')

    def finish_with(self, job_id: str, lines, stderr: str | None = None,
                    exit_code: str | None = None) -> Path:
        """Like finish(), with a log the test chooses (an empty tuple leaves it empty)."""

        job = self.job_dir(job_id)
        self.assertTrue(self.await_exit(job), "fake worker never exited")
        (job / "log.jsonl").write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        (job / "session_id").unlink(missing_ok=True)
        if stderr is not None:
            (job / "stderr.log").write_text(stderr, encoding="utf-8")
        if exit_code is not None:
            (job / "exit_code").write_text(f"{exit_code}\n", encoding="utf-8")
        return job

    def status_text(self, job_id: str) -> str:
        return self.delegate("status", job_id, "--repo", str(self.repo)).stdout

    def result_text(self, job_id: str) -> str:
        return self.delegate("result", job_id, "--repo", str(self.repo)).stdout

    def result_json(self, job_id: str) -> dict:
        result = self.delegate("result", job_id, "--repo", str(self.repo), "--json")
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(result.stdout)

    def worker_tokens(self, job_id: str) -> list[str]:
        lines = [line for line in self.exec_line(job_id).splitlines()
                 if line.startswith('"$CODEX_BIN"')]
        self.assertEqual(1, len(lines))
        return shlex.split(lines[0])

    # --- submit -----------------------------------------------------------

    def test_a_role_less_antigravity_job_gets_the_only_effort(self):
        result = self.bare_submit("--backend", "antigravity", "--model", self.MODEL, "--dry-run")
        self.assertEqual((0, ""), (result.returncode, result.stderr))
        self.assertEqual("model", parse_pairs(result.stdout)["effort"])
        # Unlike cursor, no model means no job: its resume passes the model again.
        for extra in (("--dry-run",), ()):
            with self.subTest(extra=extra):
                result = self.bare_submit("--backend", "antigravity", *extra)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("handoff-config.py set", result.stderr)
        self.assertTrue(self.no_jobs())

    def test_submit_refuses_a_wrong_effort_and_an_empty_or_auto_model(self):
        result = self.bare_submit("--backend", "antigravity", "--model", self.MODEL,
                                  "--effort", "high")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("invalid --effort for antigravity: high", result.stderr)
        self.assertIn("Antigravity carries effort in the model id", result.stderr)
        self.assertIn("handoff-config.py set", result.stderr)
        for model, message in (("", "model is empty on an antigravity job"),
                               ("auto", "'auto' is refused on an antigravity job")):
            with self.subTest(model=model):
                result = self.bare_submit("--backend", "antigravity", "--model", model)
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertIn("handoff-config.py set", result.stderr)
        self.assertTrue(self.no_jobs())
        # The same refusals through an identity.
        self.configure_posture("default")
        config = self.repo / ".handoff" / "config.toml"
        original = config.read_text(encoding="utf-8")
        for old, new, message in (
            ('effort = "model"', 'effort = "high"', "invalid --effort for antigravity: high"),
            ('model = "fixture"', 'model = "auto"', "'auto' is refused on an antigravity job"),
        ):
            with self.subTest(config=new):
                config.write_text(original.replace(old, new), encoding="utf-8")
                result = self.bare_submit("--role", "fast_worker")
                self.assertNotEqual(0, result.returncode)
                self.assertIn(message, result.stderr)
                self.assertIn("handoff-config.py set", result.stderr)
        config.write_text(original, encoding="utf-8")
        result = self.bare_submit("--role", "fast_worker", "--model", "")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("model is empty on an antigravity job", result.stderr)
        self.assertIn("handoff-config.py set --role fast_worker", result.stderr)
        self.assertTrue(self.no_jobs())

    def test_a_backend_that_contradicts_the_role_is_refused_both_ways(self):
        self.configure_posture("default")  # fast_worker is antigravity-backed
        result = self.bare_submit("--role", "fast_worker", "--backend", "cursor")
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "contradicts identity fast_worker, configured as backend=antigravity", result.stderr)
        (self.repo / ".handoff" / "config.toml").write_text(
            'schema_version = 2\nrevision = 0\n'
            '[fast_worker]\n'
            'backend = "cursor"\nmodel = "gpt-5.4-mini-low"\neffort = "model"\n',
            encoding="utf-8",
        )
        result = self.bare_submit("--role", "fast_worker", "--backend", "antigravity")
        self.assertNotEqual(0, result.returncode)
        self.assertIn(
            "contradicts identity fast_worker, configured as backend=cursor", result.stderr)
        self.assertTrue(self.no_jobs())

    # --- binary resolution --------------------------------------------------

    def test_the_ide_launcher_named_agy_is_never_resolved(self):
        ide, cli = self.root / "ide" / "agy", self.root / "cli" / "agy"
        # Nothing but the decoy: it is refused, and the refusal names the override.
        result = self.submit_with(self.on_path(ide.parent), "--dry-run")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("HANDOFF_ANTIGRAVITY_BIN", result.stderr)
        self.assertIn(str(ide), result.stderr)
        # Nothing at all.
        result = self.submit_with(self.on_path(), "--dry-run")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("agy CLI not found", result.stderr)
        self.assertIn("HANDOFF_ANTIGRAVITY_BIN", result.stderr)
        self.assertFalse((self.repo / ".handoff").exists())
        # The decoy first, the real CLI later: discovery walks past the decoy.
        result = self.submit_with(self.on_path(ide.parent, cli.parent))
        self.assertEqual(0, result.returncode, result.stderr)
        meta = self.read_meta(result.stdout.strip())
        self.assertEqual((str(cli), "path"), (meta["codex_bin"], meta["codex_bin_source"]))

    def test_an_antigravity_override_that_fails_the_identity_check_is_refused(self):
        for name, prologue in (
            ("no-flags", help_shim("Usage: agy\n  --model  A model\n")),
            ("non-zero-exit", help_shim(ANTIGRAVITY_HELP, exit_code=3)),
        ):
            with self.subTest(candidate=name):
                candidate = write_fake(self.root / name / "agy",
                                       version_shim("1.3.5") + prologue + "exit 0\n")
                env = dict(self.env)
                env["HANDOFF_ANTIGRAVITY_BIN"] = str(candidate)
                result = self.submit_with(env, "--dry-run")
                self.assertNotEqual(0, result.returncode)
                self.assertIn("HANDOFF_ANTIGRAVITY_BIN does not point at Antigravity CLI", result.stderr)
                self.assertIn(str(candidate), result.stderr)
                self.assertIn("--conversation", result.stderr)
        self.assertFalse((self.repo / ".handoff").exists())

    def test_help_on_stdout_or_stderr_passes_the_identity_check(self):
        """R2.1 reads both streams together, because the real CLI prints on stderr (HELP)."""

        on_stdout = write_fake(
            self.root / "stdout-help" / "agy",
            version_shim("1.3.5")
            + 'if [ "${1:-}" = "--help" ]; then cat <<\'HELP\'\n' + ANTIGRAVITY_HELP + "HELP\nexit 0\nfi\n",
        )
        for candidate in (on_stdout, Path(self.env["HANDOFF_ANTIGRAVITY_BIN"])):
            with self.subTest(candidate=candidate.parent.name):
                env = dict(self.env)
                env["HANDOFF_ANTIGRAVITY_BIN"] = str(candidate)
                result = self.submit_with(env, "--dry-run")
                self.assertEqual((0, ""), (result.returncode, result.stderr))
                self.assertEqual(str(candidate), parse_pairs(result.stdout)["codex_bin"])

    def test_resume_rechecks_a_replaced_parent_binary(self):
        # A recorded path that still identifies is reused.
        kept = self.submit()
        self.finish(kept)
        reused = self.resume(kept)
        self.assertEqual(0, reused.returncode, reused.stderr)
        self.assertEqual("parent", self.read_meta(reused.stdout.strip())["codex_bin_source"])
        # The same path replaced by the IDE launcher is not: resume resolves afresh.
        job_id = self.submit()
        self.finish(job_id)
        decoy = self.root / "ide" / "agy"
        meta = self.job_dir(job_id) / "meta"
        meta.write_text(
            meta.read_text(encoding="utf-8").replace(self.env["HANDOFF_ANTIGRAVITY_BIN"], str(decoy)),
            encoding="utf-8",
        )
        result = self.resume(job_id)
        self.assertEqual(0, result.returncode, result.stderr)
        child = self.read_meta(result.stdout.strip())
        self.assertEqual((self.env["HANDOFF_ANTIGRAVITY_BIN"], "env"),
                         (child["codex_bin"], child["codex_bin_source"]))

    # --- command lines and posture -------------------------------------------

    def test_antigravity_command_lines_never_carry_refused_flags(self):
        refused = ("--effort", "--sandbox", "-c", "--continue", "-i", "--project",
                   "--new-project", "--add-dir", "--agent", "--remote-control",
                   "--input-format", "--json-schema", "--log-file")
        for posture in ("default", "allow-all"):
            for read_only in (False, True):
                job_id = self.configured_submit(posture, *(["--read-only"] if read_only else []))
                self.finish(job_id)
                child = self.resume(job_id).stdout.strip()
                for round_id in (job_id, child):
                    with self.subTest(posture=posture, read_only=read_only, job=round_id == child):
                        tokens = self.worker_tokens(round_id)
                        for flag in refused:
                            self.assertNotIn(flag, tokens)
                            self.assertFalse(any(token.startswith(f"{flag}=") for token in tokens))
                        modes = [tokens[i + 1] for i, token in enumerate(tokens) if token == "--mode"]
                        self.assertEqual(["plan"] if read_only else [], modes)
                        self.assertFalse("--mode" in tokens and "--dangerously-skip-permissions" in tokens)
                        timeouts = [tokens[i + 1] for i, token in enumerate(tokens)
                                    if token == "--print-timeout"]
                        self.assertEqual(["0s"], timeouts)

    def test_fresh_and_resume_command_lines(self):
        job_id = self.submit()
        self.assertIn(
            '"$CODEX_BIN" -p "$PROMPT" --output-format stream-json --model "$MODEL" '
            '--print-timeout 0s --dangerously-skip-permissions '
            '>"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null',
            self.exec_line(job_id),
        )
        self.assertNotIn("--conversation", self.exec_line(job_id))
        self.finish(job_id)
        resumed = self.exec_line(self.resume(job_id).stdout.strip())
        self.assertIn(
            '"$CODEX_BIN" -p "$PROMPT" --output-format stream-json --conversation "$SESSION_ID" '
            '--model "$MODEL" --print-timeout 0s --dangerously-skip-permissions '
            '>"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null',
            resumed,
        )
        self.assertIn(f"MODEL={self.MODEL}\n", resumed)
        self.assertIn(f"SESSION_ID={AGY_SID}\n", resumed)

    def test_the_bypass_warning_prints_under_default_and_allow_all(self):
        for posture in ("default", "allow-all"):
            with self.subTest(posture=posture):
                self.configure_posture(posture)
                result = self.submit_raw("--role", "fast_worker")
                self.assertEqual(0, result.returncode, result.stderr)
                self.assertIn("antigravity worker running with permission checks bypassed",
                              result.stderr)
                self.assertIn("Antigravity has no narrower posture Handoff can set", result.stderr)
                self.assertIn("use --read-only for a job that must not write", result.stderr)
                # stdout stays exactly the jobId, which is what the driver captures.
                self.assertRegex(result.stdout.strip(), r"^job-[\w.:-]+$")
                self.finish(result.stdout.strip())
                resumed = self.resume(result.stdout.strip())
                self.assertEqual(0, resumed.returncode, resumed.stderr)
                self.assertIn("antigravity worker running with permission checks bypassed",
                              resumed.stderr)
        result = self.submit_raw("--role", "fast_worker", "--read-only")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertNotIn("permission checks bypassed", result.stderr)
        self.assertNotIn("Antigravity has no narrower posture", result.stderr)

    def test_handoff_writes_nothing_under_gemini(self):
        """R3.5: agy keeps its settings and trust list under ~/.gemini; Handoff never touches them."""

        self.env["HOME"] = str(self.root)
        job_id = self.submit("--worktree", "e2e/G1")
        self.finish(job_id)
        child = self.resume(job_id).stdout.strip()
        self.finish(child)
        self.assertEqual([], list(self.root.rglob(".gemini")))
        for job in (job_id, child):
            for name in ("run.sh", "meta"):
                self.assertNotIn(".gemini", (self.job_dir(job) / name).read_text(encoding="utf-8"))

    # --- sessions and resume ------------------------------------------------

    def test_a_launch_rejection_has_no_session_and_one_error(self):
        """MUNKNOWN: one result with an empty id and no init, exit 1."""

        parent = self.submit()
        self.finish(parent)
        self.install_rejecting_worker()

        def assert_rejection(job_id: str):
            self.assertTrue(self.await_exit(self.job_dir(job_id)))
            status = self.status_text(job_id)
            self.assertIn("state: FAILED", status)
            self.assertIn("last_event: result", status)
            # Usage and denials are known and zero: the result event says so.
            self.assertNotIn("permission_denied", status)
            self.assertNotIn("context_lost", status)
            payload = self.result_json(job_id)
            self.assertEqual("", payload["session_id"])
            self.assertEqual(ZERO_USAGE, payload["usage"])
            self.assertEqual(0, payload["permission_denied"])
            self.assertEqual([], payload["denied_tools"])
            self.assertEqual("", payload["agent_message"])
            self.assertIsNone(payload["context_lost"])
            self.assertEqual(
                [{"status_code": None, "error_type": "result_error", "message": REJECTION_ERROR}],
                payload["errors"],
            )
            text = self.result_text(job_id)
            self.assertIn(f"session_error: result_error: {REJECTION_ERROR}", text)
            self.assertIn("permission_denied: 0", text)
            self.assertNotIn("context_lost", text)
            self.assertEqual(f"error: {REJECTION_ERROR}\n",
                             (self.job_dir(job_id) / "stderr.log").read_text(encoding="utf-8"))
            # No init, so no session to continue.
            refused = self.resume(job_id)
            self.assertNotEqual(0, refused.returncode)
            self.assertIn("no session id found", refused.stderr)

        fresh = self.submit("--model", "no-such-model")
        assert_rejection(fresh)
        # A resume round whose model has left the catalogue fails the same way,
        # and the parent's id it asked for is recorded but is not its session.
        result = self.resume(parent)
        self.assertEqual(0, result.returncode, result.stderr)
        round_id = result.stdout.strip()
        self.assertEqual(AGY_SID, self.read_meta(round_id)["resumed_session"])
        assert_rejection(round_id)
        self.assertFalse(self.job_dir(f"{parent}-r3").exists())

    def test_a_session_read_before_init_is_not_cached_empty(self):
        job_id = self.submit()
        job = self.job_dir(job_id)
        self.assertTrue(self.await_exit(job))
        self.assertEqual("", (job / "log.jsonl").read_text(encoding="utf-8"))
        self.assertIn(UNKNOWN_DENIALS, self.status_text(job_id))
        self.assertEqual("", self.result_json(job_id)["session_id"])
        # The read left only a newline in the cache, which must not stand as an id.
        self.assertTrue((job / "session_id").is_file())
        self.assertEqual("", (job / "session_id").read_text(encoding="utf-8").strip())
        (job / "log.jsonl").write_text(agy_init("sess-late") + "\n", encoding="utf-8")
        self.assertEqual("sess-late", self.result_json(job_id)["session_id"])
        resumed = self.resume(job_id)
        self.assertEqual(0, resumed.returncode, resumed.stderr)
        self.assertIn("SESSION_ID=sess-late\n", self.exec_line(resumed.stdout.strip()))

    def test_a_resume_of_a_resume_passes_the_recorded_model(self):
        first = self.submit()
        self.finish(first)
        second = self.resume(first).stdout.strip()
        self.finish_with(second, [line.replace(AGY_SID, "sess-round-2") for line in self.LOG_LINES])
        third = self.resume(second)
        self.assertEqual(0, third.returncode, third.stderr)
        third = third.stdout.strip()
        for job_id, asked in ((second, AGY_SID), (third, "sess-round-2")):
            with self.subTest(job=job_id):
                meta = self.read_meta(job_id)
                # The model that ran, never `inherit`, so a chain needs no walk back.
                self.assertEqual(self.MODEL, meta["model"])
                self.assertEqual(asked, meta["resumed_session"])
                run_sh = self.exec_line(job_id)
                self.assertIn(f"MODEL={self.MODEL}\n", run_sh)
                self.assertIn(f"SESSION_ID={asked}\n", run_sh)
                self.assertIn('--model "$MODEL"', run_sh)

    def test_resume_refuses_a_parent_with_no_recorded_model(self):
        job_id = self.submit()
        self.finish(job_id)
        meta = self.job_dir(job_id) / "meta"
        original = meta.read_text(encoding="utf-8")
        for broken in ("model=inherit", "model=default", "model="):
            with self.subTest(broken=broken):
                meta.write_text(re.sub(r"(?m)^model=.*$", broken, original), encoding="utf-8")
                result = self.resume(job_id)
                self.assertNotEqual(0, result.returncode)
                self.assertIn("recorded no model", result.stderr)
                self.assertFalse(self.job_dir(f"{job_id}-r2").exists())

    def test_an_antigravity_resume_does_not_prewrite_the_session_cache(self):
        job_id = self.submit()
        self.finish(job_id)
        result = self.resume(job_id)
        self.assertEqual(0, result.returncode, result.stderr)
        child = self.job_dir(result.stdout.strip())
        # The id it asked for is recorded; the id it ends up on is its own log's init.
        self.assertFalse((child / "session_id").exists())
        self.assertEqual(AGY_SID, self.read_meta(child.name)["resumed_session"])

    def test_a_lost_session_is_reported_in_status_text_and_json(self):
        # The id it asked for is the one it ran on: context intact.
        kept = self.submit()
        self.finish(kept)
        kept_round = self.resume(kept).stdout.strip()
        self.finish(kept_round)
        self.assertIs(False, self.result_json(kept_round)["context_lost"])
        self.assertNotIn("context_lost", self.status_text(kept_round))
        self.assertNotIn("context_lost", self.result_text(kept_round))
        # RUNKNOWN: an id agy cannot find is a warning, exit 0, and a new conversation.
        lost = self.submit()
        self.finish(lost)
        lost_round = self.resume(lost).stdout.strip()
        self.finish_with(
            lost_round,
            [line.replace(AGY_SID, "sess-new") for line in self.LOG_LINES],
            stderr=f'warning: conversation "{AGY_SID}" not found\n',
        )
        self.assertIn(LOST_CONTEXT, self.status_text(lost_round))
        self.assertIn(LOST_CONTEXT, self.result_text(lost_round))
        payload = self.result_json(lost_round)
        self.assertIs(True, payload["context_lost"])
        self.assertEqual("sess-new", payload["session_id"])
        # A further resume continues the round's own conversation, not the lost parent.
        further = self.resume(lost_round)
        self.assertEqual(0, further.returncode, further.stderr)
        self.assertIn("SESSION_ID=sess-new\n", self.exec_line(further.stdout.strip()))
        self.assertEqual("sess-new", self.read_meta(further.stdout.strip())["resumed_session"])

    def test_a_cut_off_antigravity_log_reports_unknown_denials(self):
        # Killed in its first tool call: init, an agent step, and a tool in flight.
        cut = self.LOG_LINES[:5]
        for label, lines in (("cut off", cut), ("empty log", ())):
            with self.subTest(log=label):
                job_id = self.submit()
                self.finish_with(job_id, lines, exit_code="137")
                self.assertIn(UNKNOWN_DENIALS, self.status_text(job_id))
                text = self.result_text(job_id)
                self.assertIn(UNKNOWN_DENIALS, text)
                self.assertNotIn("permission_denied: 0", text)
                self.assertNotIn("WARNING", text)
                payload = self.result_json(job_id)
                self.assertIsNone(payload["permission_denied"])
                self.assertEqual([], payload["denied_tools"])
                # Usage is known only once a result event exists.
                self.assertEqual({}, payload["usage"])
        # status is polled, so a running job prints no denial line at all.
        running = self.submit()
        job = self.finish_with(running, cut)
        pid, exit_code = (job / "pid").read_text(), (job / "exit_code").read_text()
        (job / "exit_code").unlink()
        (job / "pid").write_text(f"{os.getpid()}\n", encoding="utf-8")
        try:
            status = self.status_text(running)
        finally:
            (job / "pid").write_text(pid, encoding="utf-8")
            (job / "exit_code").write_text(exit_code, encoding="utf-8")
        self.assertIn("state: RUNNING", status)
        self.assertIn("last_event: step_update:tool", status)
        self.assertNotIn("permission_denied", status)

    def test_a_killed_job_resumes_after_init_and_is_refused_before_any_event(self):
        """Wiring only: that agy restores the context is KILL and RKILL's evidence."""

        killed = self.submit()
        self.finish_with(killed, self.LOG_LINES[:5], exit_code="137")
        result = self.resume(killed)
        self.assertEqual(0, result.returncode, result.stderr)
        run_sh = self.exec_line(result.stdout.strip())
        self.assertIn(f"SESSION_ID={AGY_SID}\n", run_sh)
        self.assertIn('--conversation "$SESSION_ID"', run_sh)
        # Killed before any event: nothing to resume, and the usual refusal says so.
        early = self.submit()
        self.finish_with(early, (), exit_code="137")
        refused = self.resume(early)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn("no session id found", refused.stderr)
        self.assertFalse(self.job_dir(f"{early}-r2").exists())

    # --- reading the log ----------------------------------------------------

    def test_the_agent_message_is_the_last_agent_response_step(self):
        """P3: result.response joins every agent message of the invocation."""

        steps = (
            agy_step(1, "ACTIVE", "agent_response", text_delta="Step 1: Attempting to edit."),
            agy_step(1, "DONE", "agent_response", text_delta="\n", usage=agy_usage(11945, 137)),
            *agy_tool(2, "write_to_file", {"TargetFile": "/r/tracked.txt"}),
            agy_step(3, "ACTIVE", "agent_response", text_delta="Step "),
            agy_step(3, "ACTIVE", "agent_response", text_delta="2: Attempting to run `ba"),
            agy_step(3, "ACTIVE", "agent_response", text_delta="sh check.sh`."),
            agy_step(3, "DONE", "agent_response", text_delta="\n", usage=agy_usage(12238, 115)),
            *agy_tool(4, "run_command", {"CommandLine": "bash check.sh"}, output="CHECK_OK\r\n"),
            agy_step(5, "ACTIVE", "agent_response", text_delta="### Execution"),
            agy_step(5, "ACTIVE", "agent_response", text_delta=" Report\n\n1. **Edit**"),
            agy_step(5, "DONE", "agent_response", text_delta="\n   - **Refusals:** None\n",
                     usage=agy_usage(13248, 355)),
        )
        final = "### Execution Report\n\n1. **Edit**\n   - **Refusals:** None\n"
        everything = ("Step 1: Attempting to edit.\n"
                      "Step 2: Attempting to run `bash check.sh`.\n" + final)
        job_id = self.submit()
        self.finish_with(job_id, (
            agy_init(), agy_step(0, "DONE", "user_input"), *steps,
            agy_result(response=everything, usage=agy_usage(36431, 607)),
        ))
        payload = self.result_json(job_id)
        self.assertEqual(final, payload["agent_message"])
        self.assertNotEqual(everything, payload["agent_message"])
        self.assertEqual(["bash check.sh"], payload["commands"])
        text = self.result_text(job_id)
        self.assertIn(f"--- agent message ---\n{final}", text)
        self.assertIn("commands_run: 1", text)

    def test_resume_usage_is_the_round_s_own_steps(self):
        """RSAME: result.usage is cumulative over the conversation, the steps are not."""

        parent = self.submit()
        self.finish(parent)
        round_id = self.resume(parent).stdout.strip()
        self.finish_with(round_id, (
            agy_init(),
            agy_step(2, "DONE", "user_input"),
            agy_step(3, "DONE", "system_message", duration_seconds=0.000285),
            agy_step(4, "ACTIVE", "agent_response", text_delta="RESUME-TOKEN-7281"),
            agy_step(4, "DONE", "agent_response", text_delta="\n", duration_seconds=3.19,
                     usage=agy_usage(12143, 99, 90)),
            agy_result(response="RESUME-TOKEN-7281\n", usage=agy_usage(23989, 100, 90), turns=2),
        ))
        payload = self.result_json(round_id)
        self.assertEqual(agy_usage(12143, 99, 90), payload["usage"])
        self.assertEqual("RESUME-TOKEN-7281\n", payload["agent_message"])
        self.assertEqual([], payload["commands"])
        self.assertIs(False, payload["context_lost"])

    def test_a_print_timeout_line_becomes_an_error(self):
        """TIMEOUTR: a timed-out run exits 0 with status SUCCESS and a stderr line."""

        job_id = self.submit()
        self.finish_with(
            job_id,
            (agy_init(), agy_step(0, "DONE", "user_input"), agy_result(turns=1)),
            stderr=f"warning: something else\n{TIMEOUT_LINE}\n",
        )
        payload = self.result_json(job_id)
        self.assertEqual(
            [{"status_code": None, "error_type": "print_timeout", "message": TIMEOUT_LINE}],
            payload["errors"],
        )
        self.assertIn(f"session_error: print_timeout: {TIMEOUT_LINE}", self.result_text(job_id))
        # Alongside a failed result, both are reported, the result's own first.
        both = self.submit()
        self.finish_with(
            both,
            (agy_init(), agy_result(status="ERROR", error="API error: capacity")),
            stderr=f"{TIMEOUT_LINE}\n",
        )
        self.assertEqual(["result_error", "print_timeout"],
                         [error["error_type"] for error in self.result_json(both)["errors"]])
        # A clean run with ordinary stderr reports no error.
        clean = self.submit()
        self.finish_with(clean, self.LOG_LINES, stderr="warning: something else\n")
        self.assertEqual([], self.result_json(clean)["errors"])

    def test_an_unexpected_antigravity_shape_is_skipped_not_fatal(self):
        odd = (
            '{"event":"step_update","step_update":null}',
            '{"event":"step_update","step_update":"agent_response"}',
            '{"event":"step_update","step_update":[]}',
            '{"event":"step_update","step_update":{"step_index":null,"step_type":"agent_response",'
            '"text_delta":"stray"}}',
            '{"event":"step_update","step_update":{"step_index":6,"state":"ACTIVE","step_type":"tool",'
            '"tool_name":"run_command","tool_info":null}}',
            '{"event":"step_update","step_update":{"step_index":7,"state":"ACTIVE","step_type":"tool",'
            '"tool_name":"run_command","tool_info":{"parameters":"ls"}}}',
            # A usage that is not an object, or holds no numbers, adds nothing.
            agy_step(1, "DONE", "agent_response", usage="lots"),
            agy_step(1, "DONE", "agent_response", usage=[1, 2]),
            agy_step(1, "DONE", "agent_response", usage=None),
            agy_step(1, "DONE", "agent_response",
                     usage={"input_tokens": "many", "output_tokens": True}),
            "[]", '"text"', "7", "null", '{"event":null}', '{"event":5}', "not json at all",
        )
        for label, extra in (
            ("no denied_actions key", {}),
            ("a string", {"denied_actions": "WriteToFile"}),
            ("an object", {"denied_actions": {"action": "write_file"}}),
            ("a number", {"denied_actions": 3}),
            ("null", {"denied_actions": None}),
            ("a usage that is not an object", {"usage": "none"}),
        ):
            with self.subTest(result=label):
                job_id = self.submit()
                result_line = json.dumps({"event": "result", "result": {
                    "conversation_id": AGY_SID, "status": "SUCCESS",
                    "response": "looking\nwork done", "num_turns": 1,
                    "usage": agy_usage(28, 7, 2, 5), **extra}})
                self.finish_with(job_id, (*self.LOG_LINES[:4], *odd, *self.LOG_LINES[4:-1], result_line))
                result = self.delegate("result", job_id, "--repo", str(self.repo), "--json")
                self.assertEqual(0, result.returncode, result.stderr)
                payload = json.loads(result.stdout)
                self.assertEqual(0, payload["permission_denied"])
                self.assertEqual([], payload["denied_tools"])
                self.assertEqual("work done", payload["agent_message"])
                self.assertEqual(["pytest -q"], payload["commands"])
                self.assertEqual(agy_usage(28, 7, 2, 5), payload["usage"])
                status = self.delegate("status", job_id, "--repo", str(self.repo))
                self.assertEqual(0, status.returncode, status.stderr)
                self.assertIn("last_event: result", status.stdout)
                self.assertNotIn("permission_denied", status.stdout)
        # A result whose payload is not an object is no result at all.
        job_id = self.submit()
        self.finish_with(job_id, (*self.LOG_LINES[:-1], '{"event":"result","result":null}'))
        payload = self.result_json(job_id)
        self.assertEqual((None, {}), (payload["permission_denied"], payload["usage"]))

    # --- no fall-through (R7.4) --------------------------------------------

    OTHER_BACKENDS_LINES = (
        '{"type":"system","subtype":"permission_denied","tool_name":"Bash"}',
        '{"type":"tool.execution_complete","data":{"toolCallId":"c","success":false,'
        '"error":{"code":"denied","message":"no"}}}',
        '{"type":"tool_call","subtype":"completed","call_id":"x","tool_call":'
        '{"shellToolCall":{"result":{"rejected":{"command":"curl"}}}}}',
        '{"type":"item.completed","item":{"type":"agent_message","text":"codex words"}}',
        '{"type":"assistant","message":{"content":[{"type":"text","text":"claude words"}]}}',
        '{"type":"turn.completed","usage":{"input_tokens":99,"output_tokens":9}}',
        '{"type":"result","subtype":"success","result":"claude result","usage":{"input_tokens":98}}',
    )

    def test_no_other_backends_event_shape_is_read_as_antigravity_and_none_the_other_way(self):
        job_id = self.submit()
        self.finish_with(job_id, (*self.OTHER_BACKENDS_LINES, *self.LOG_LINES))
        payload = self.result_json(job_id)
        self.assertEqual(0, payload["permission_denied"])
        self.assertEqual("work done", payload["agent_message"])
        self.assertEqual(["pytest -q"], payload["commands"])
        self.assertEqual(agy_usage(28, 7, 2, 5), payload["usage"])
        self.assertNotIn("permission_denied", self.status_text(job_id))
        # An antigravity denial list is not another backend's denial, and its
        # result event is not another backend's answer.
        for backend, effort in (("codex", "high"), ("claude", "high"),
                                ("copilot", "high"), ("cursor", "model")):
            with self.subTest(backend=backend):
                other = self.bare_submit("--backend", backend, "--effort", effort)
                self.assertRegex(other.stdout.strip(), r"^job-", other.stderr)
                self.finish_with(other.stdout.strip(), self.denied_log_lines())
                payload = self.result_json(other.stdout.strip())
                self.assertEqual(0, payload["permission_denied"])
                self.assertEqual("", payload["agent_message"])
                self.assertIsNone(payload["context_lost"])
                self.assertNotIn("permission_denied", self.status_text(other.stdout.strip()))

    def test_context_lost_is_null_on_other_backends(self):
        for backend, effort, lines in (
            ("codex", "high", CodexWorktreeTests.LOG_LINES),
            ("cursor", "model", CursorWorktreeTests.LOG_LINES),
        ):
            with self.subTest(backend=backend):
                submitted = self.bare_submit("--backend", backend, "--effort", effort)
                self.assertRegex(submitted.stdout.strip(), r"^job-", submitted.stderr)
                job_id = submitted.stdout.strip()
                self.finish_with(job_id, lines)
                payload = self.result_json(job_id)
                self.assertIn("context_lost", payload)
                self.assertIsNone(payload["context_lost"])
                self.assertNotIn("context_lost", self.status_text(job_id))
                self.assertNotIn("context_lost", self.result_text(job_id))


if __name__ == "__main__":
    unittest.main()
