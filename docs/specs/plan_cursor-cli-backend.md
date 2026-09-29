# Cursor CLI as the Fourth Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. In this repository the plan runs under `/agent-handoff`: each task is one delegated row.

**Goal:** A cursor-backed identity runs `cursor-agent -p --output-format stream-json` as the same durable Handoff job that codex, claude, and copilot get, with receipt schema v7 and version 3.9.0.

**Architecture:** One more explicit branch in each place that branches on backend (approach A of the design). The tables keyed by backend gain a row. Each of the five event parsers gains an explicit cursor branch, and the Claude and Copilot branches that act as catch-alls become explicit. No shared parsing module; that is a follow-up.

**Tech Stack:** Bash (`scripts/delegate-codex.sh`), Python 3 stdlib (`unittest`, no third-party packages), vanilla JavaScript in `assets/*.html`, `node --test` for the viewer.

**Spec:** [requirements_cursor-cli-backend.md](requirements_cursor-cli-backend.md) (R1.1 to R9.3) and [design_cursor-cli-backend.md](design_cursor-cli-backend.md) (decisions 1 to 13). Facts: [../research/cursor-cli-specification.md](../research/cursor-cli-specification.md). Executors read all three.

## Global Constraints

- Version `3.9.0` everywhere it appears. Receipt `receipt_schema_version` `7`. Config `schema_version` stays `2`.
- Backend value `cursor`. Binary `cursor-agent`, from `HANDOFF_CURSOR_BIN` first, then PATH. Never `cursor` (the IDE launcher), never `agent`.
- Cursor effort enum is exactly `("model",)`. `model = "auto"` is refused on cursor at setup and at submit, and each refusal names `handoff-config.py set`.
- Postures: `default` and `allow-all` both pass `--force`; read-only passes `--mode plan` and never `--force`. <!-- risk-ok: Cursor CLI flag name -->
- A generated cursor command line never contains `--worktree`, `--sandbox`, `--approve-mcps`, `--api-key`, `--stream-partial-output`, or `--yolo`.
- Every line in a doc, script, or test that spells Cursor's `--force` carries a `risk-ok` marker (`# risk-ok: Cursor CLI flag name` in code, `<!-- risk-ok: Cursor CLI flag name -->` in Markdown). In `test-prompts.json` the literal flag may appear only inside a `must_not` list. `bash scripts/check-skill-repo.sh .` must add no new warning lines. <!-- risk-ok: Cursor CLI flag name -->
- Handoff writes nothing to `.cursor/cli.json`, `~/.cursor/`, or `CURSOR_CONFIG_DIR`. No environment scrubbing for the cursor branch.
- No Cursor preset. No cost figure for a cursor job: the USD value is `None`.
- Codex and Claude parsing does not change. `tests/test_transcript_viewer.mjs` baseline test stays green.
- The skill surface (`SKILL.md`, `references/`, `scripts/`) never links into `docs/specs/`.
- Prose follows the repo style: plain words, no decorative emoji, no invented numbers.

## Review Focus

Inputs the spec implies but its acceptance checks do not exercise, most likely to bite first. Each line has a test in the named task.

1. **A role-less `submit --backend cursor` with no `--effort`.** A user expects it to run. Today's default effort `high` would be refused. Expected: the job gets effort `model`. Test in Task 1 (`test_a_role_less_job_gets_the_only_effort`). The preset path of `handoff-setup.py` gets the same fill (Task 2, `test_a_preset_override_onto_cursor_fills_the_effort`).
2. **A cursor log that ends in `thinking` events** (a job killed mid-reasoning, or still reasoning when polled). Expected: `status` shows the last non-`thinking` event, and `result` shows the last `assistant` message. Test in Task 1 (`test_thinking_never_reaches_status_or_result`).
3. **A `tool_call` event in a shape the probes did not show** (`null` body, a `result` that is a string, `tool_call` that is a list). Expected: nothing crashes, and nothing counts as a denial. Python's `"rejected" in "rejected"` is `True`, so every parser checks that `result` is an object. Tests in Task 1 (`test_an_unexpected_tool_call_shape_is_skipped_not_fatal`) and Task 4 (`test_an_unexpected_tool_call_shape_is_skipped`).
4. **The real `cursor-agent models` output**: a header line, blank lines, `auto - Auto (default)`, and a trailing `Tip:` line. Expected: only real slugs are offered, and `auto` is dropped. Test in Task 2 (`test_the_catalogue_reader_keeps_only_real_slugs`) with output copied from build `2026.09.28-64d2043`.
5. **`HANDOFF_CURSOR_BIN` set in the developer's shell.** The setup test suites copy `os.environ`, so a real `cursor-agent` would run during tests. Expected: the suites never reach the real CLI. Tasks 2 and 3 pop the variable in `setUp`; Task 1's `make_env` overwrites it.

---

## Execution order

Tasks touch disjoint files within a wave, so a wave's rows can run in parallel as `--worktree` jobs.

| Wave | Tasks | Why this order |
|---|---|---|
| 1 | 1, 4, 5 | Independent files. Task 1 carries the config engine's `BACKENDS` row because `submit --role` validates the config. |
| 2 | 2 | Smoke's first step runs `delegate-codex.sh submit --dry-run`, which needs Task 1. |
| 3 | 3 | The web wizard calls the catalogue reader Task 2 adds. |
| 4 | 6, 7 | Prose describes what shipped. |
| 5 | 8 | Live end-to-end run on the installed copy. Driver-run. |

---

### Task 1: The delegation primitive

Requirements: R1.1 (engine side), R1.2 and R1.3 (submit side), R1.4, R2.1 to R2.6, R3.1 to R3.4, R4.1 to R4.3, R5.1 to R5.3, R9.2 (delegate tests and shared helpers).

**Files:**
- Modify: `scripts/delegate-codex.sh` (header comment, `usage()`, `resolve_worker_bin`, `validate_effort`, new `resolve_cursor_permission_mode`, `warn_permission_bypass`, `cmd_submit`, `write_run_script`, new `write_cursor_exec_line`, `STATUS_SCAN_PY`, `cmd_result`)
- Modify: `scripts/handoff-config.py:62` (`BACKENDS`)
- Test: `tests/test_delegate_role.py`, `tests/test_handoff_config.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `handoff_config.BACKENDS == ("claude", "codex", "copilot", "cursor")`. `delegate-codex.sh submit --backend cursor` accepted; cursor effort `model`; `meta` lines `backend=cursor` and `permission_mode=force|plan`. Tasks 2 and 3 rely on the dry-run output keys (`backend`, `effort`, `codex_bin`, `permission_mode`) staying as they are.

- [ ] **Step 1: Update the shared test helpers**

In `tests/test_delegate_role.py`, move the body of `BackendLifecycle.await_exit` into a module function so a non-lifecycle test class can wait on a job too:

```python
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
```

and make the method delegate: `def await_exit(self, job: Path): return settle(job)`.

Extend `make_env` (after the copilot fake):

```python
    env["HANDOFF_CURSOR_BIN"] = str(
        write_fake(root / "cursor-agent", version_shim("2026.09.28-64d2043") + codex_body)
    )
    # `cursor` is the Cursor IDE launcher, and the backend's own name, so a
    # PATH search for the backend name finds the IDE. Nothing may resolve to it.
    write_fake(root / "ide" / "cursor", "echo 'Cursor IDE launcher' >&2\nexit 64\n")
```

In `BackendLifecycle`:

```python
    # The effort this backend accepts. Cursor accepts only `model`.
    EFFORT = "high"
```

`submit_raw` passes `"--effort", self.EFFORT` right after `"--backend", self.BACKEND`. In `test_multiline_model_and_invalid_resume_effort_are_refused` replace `"effort=high"` with `f"effort={self.EFFORT}"`. In `configure_posture` write `effort = "{self.EFFORT}"` in place of `effort = "high"`.

In `assert_posture_argv`, turn the final `else` into an explicit copilot branch and add a cursor branch:

```python
        elif self.BACKEND == "copilot":
            self.assertEqual(read_only, "--mode plan" in run_sh)
            self.assertEqual(not read_only, "--allow-all-tools" in run_sh)
            for flag in ("--allow-all-paths", "--allow-all-urls"):
                self.assertEqual(posture == "allow-all" and not read_only, flag in run_sh)
        elif self.BACKEND == "cursor":
            # Both postures are the force flag; read-only never carries it.
            self.assertEqual(read_only, "--mode plan" in run_sh)
            self.assertEqual(not read_only, "--force" in run_sh)  # risk-ok: Cursor CLI flag name
            self.assertEqual("plan" if read_only else "force", meta["permission_mode"])
        else:
            self.fail(f"no posture assertions for backend {self.BACKEND}")
```

- [ ] **Step 2: Write the cursor lifecycle and discovery tests**

Append to `tests/test_delegate_role.py`, before `if __name__ == "__main__":`:

```python
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
            '[hosts.claude_code.identities.fast_worker]\n'
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
```

In `tests/test_handoff_config.py`: the tuple in `test_every_supported_backend_validates` becomes `("claude", "codex", "copilot", "cursor")`; `test_the_error_names_every_supported_backend` asserts `"backend must be one of claude, codex, copilot, cursor"`. Add to `CliTests`:

```python
    def test_a_cursor_identity_is_written_and_validates(self):
        with tempfile.TemporaryDirectory() as directory:
            base = ("--repo", directory)
            self.assertEqual(0, self.run_cli(*base, "init")[0])
            status, _, error = self.run_cli(
                *base, "set", "--role", "fast_worker", "--backend", "cursor",
                "--model", "claude-opus-5-5-high", "--effort", "model",
            )
            self.assertEqual((0, ""), (status, error))
            self.assertEqual(0, self.run_cli(*base, "validate")[0])
```

- [ ] **Step 3: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_delegate_role tests.test_handoff_config`
Expected: FAIL. `CursorWorktreeTests` fail with `invalid --backend: cursor` or a config validation error; the config tests fail on the `BACKENDS` tuple.

- [ ] **Step 4: Implement**

`scripts/handoff-config.py:62`:

```python
BACKENDS = ("claude", "codex", "copilot", "cursor")
```

`scripts/delegate-codex.sh`, in order:

1. Header comment (lines 7 to 8): add `cursor-agent -p --output-format stream-json` to the wrapped commands.

2. `usage()`: `--backend codex|claude|copilot|cursor`; "(the name is historical; it drives the codex, claude, copilot, and cursor backends)"; read-only "maps to ... and `--mode plan` on copilot and cursor". Add these paragraphs (no literal force flag in the text, so no marker is needed):

```text
Cursor takes one effort, `model`: Cursor carries effort inside the model slug
(claude-opus-5-5-high), so the slug is the whole choice. A role-less cursor job
that names no --effort gets `model`. `--model auto` is refused on a copilot or
cursor job.

On cursor the permission posture changes nothing: default and allow-all both
run in Cursor's force mode (-f), because no narrower Cursor mode let a probed
worker run its own checks. Deny rules in .cursor/cli.json are the only
narrowing, and every writing cursor job prints the bypass warning. A cursor job
never carries --worktree, --sandbox, --approve-mcps, --api-key,
--stream-partial-output, or --yolo.

The cursor backend's binary is `cursor-agent`, set with HANDOFF_CURSOR_BIN.
`cursor` on PATH is the Cursor IDE launcher and `agent` is too generic a name,
so neither is ever resolved.
```

3. `resolve_worker_bin`: the `local` line becomes `local backend="$1" env_var configured candidate="" bin_name="$1"`, the `case` gains `cursor) env_var="HANDOFF_CURSOR_BIN" ;;`, and the final PATH fallback becomes:

```bash
  # The cursor backend's agent is `cursor-agent`; `cursor` on PATH is the IDE.
  [ "$backend" = "cursor" ] && bin_name="cursor-agent"
  if [ -z "$candidate" ]; then
    candidate="$(command -v "$bin_name" 2>/dev/null || true)"
    CODEX_BIN_SOURCE="path"
  fi
  [ -n "$candidate" ] && [ -x "$candidate" ] || die "$bin_name CLI not found; install it or set $env_var"
```

Codex, claude, and copilot keep their message, because their `bin_name` is the backend name.

4. `validate_effort`, a new case before `*)`:

```bash
    cursor) [ "$2" = "model" ] || die "invalid --effort for cursor: $2 (Cursor carries effort in the model id; name the variant in --model and use --effort model)" ;;
```

5. After `resolve_copilot_permission_mode`:

```bash
resolve_cursor_permission_mode() {
  # Both postures map to Cursor's force mode: probed, no narrower mode let a
  # worker run its checks. Read-only is plan mode and never force.
  PERMISSION_MODE="force"
  [ "$1" = "true" ] && PERMISSION_MODE="plan"
  return 0
}
```

6. `warn_permission_bypass`: add `cursor:force` to the whitelist `case`, and make the cursor remedy the first branch:

```bash
  if [ "$BACKEND" = "cursor" ]; then
    echo "     Cursor has no narrower posture: add deny rules in .cursor/cli.json, or use --read-only for a job that must not write." >&2
  elif [ "$BACKEND" = "claude" ] && [ "${HANDOFF_CLAUDE_PERMISSION_MODE:-}" = "bypassPermissions" ]; then
```

7. `cmd_submit`: the backend guard becomes `codex|claude|copilot|cursor`. After the `EFFORT_SOURCE="explicit"` line:

```bash
  # Cursor has one effort value. A role-less job that named none gets it rather
  # than the codex-shaped default.
  if [ "$BACKEND" = "cursor" ] && [ "$EFFORT_EXPLICIT" = "false" ] && [ -z "$ROLE" ]; then EFFORT="model"; fi
```

Replace the copilot-only `auto` refusal:

```bash
  if [ "$MODEL" = "auto" ] && { [ "$BACKEND" = "copilot" ] || [ "$BACKEND" = "cursor" ]; }; then
    die "model 'auto' is refused on a $BACKEND job: name a concrete model instead, with 'handoff-config.py set --role ${ROLE:-<identity>} --backend $BACKEND --model <model>'"
  fi
```

8. `write_run_script`: a branch after copilot, `elif [ "$BACKEND" = "cursor" ]; then write_cursor_exec_line "$model" "$read_only" "$session_id"`. New function after `write_copilot_exec_line`:

```bash
write_cursor_exec_line() {
  local model="$1" read_only="$2" session_id="$3"
  # --trust always: a fresh worktree is a workspace Cursor has never seen.
  # Handoff owns the worktree protocol, so Cursor's own worktree flag is never
  # passed, and neither are its sandbox, MCP-approval, API-key, or
  # partial-output flags. No env scrubbing: Cursor reads none of the
  # ANTHROPIC_* or CLAUDE_CODE_* variables.
  local args="--output-format stream-json --trust"
  if [ -n "$session_id" ]; then
    # A resumed session carries its model and workspace; cwd comes from the cd.
    args="$args --resume \"\$SESSION_ID\""
  else
    args="$args --workspace \"\$WORKDIR\""
    [ -n "$model" ] && args="$args --model \"\$MODEL\""
  fi
  if [ "$read_only" = "true" ]; then
    args="$args --mode plan"
  else
    args="$args --force"  # risk-ok: Cursor CLI flag name
  fi
  echo 'cd "$WORKDIR"'
  printf '"$CODEX_BIN" -p "$PROMPT" %s >"$JOB/log.jsonl" 2>"$JOB/stderr.log" </dev/null\n' "$args"
}
```

9. `STATUS_SCAN_PY`: skip `thinking` before `last` is set, and add a cursor branch before the claude `elif`:

```python
        etype = event.get("type", "")
        # Cursor's reasoning stream, hundreds of events a job, would bury the
        # event that says where the worker is.
        if backend == "cursor" and etype == "thinking":
            continue
        if etype:
            last = etype
        if backend == "copilot":
            ...  # unchanged
        elif backend == "cursor":
            # Typed: a refused call completes with a `rejected` result object.
            call = event.get("tool_call")
            if etype == "tool_call" and event.get("subtype") == "completed" and isinstance(call, dict):
                if any(isinstance(body, dict) and isinstance(body.get("result"), dict)
                       and "rejected" in body["result"] for body in call.values()):
                    denied += 1
        elif event.get("subtype") == "permission_denied":
            denied += 1
```

10. `cmd_result`: a cursor branch right after the copilot branch's `continue`, before the claude `permission_denied` check:

```python
            if backend == "cursor":
                if etype == "thinking":
                    continue
                if etype == "assistant":
                    # One event per message. Cursor's result.result runs every
                    # message together, so the answer is the last event's text.
                    blocks = (event.get("message") or {}).get("content") or []
                    text = "".join(block.get("text") or "" for block in blocks
                                   if isinstance(block, dict) and block.get("type") == "text")
                    if text:
                        messages.append(text)
                elif etype == "tool_call":
                    # The tool kind is the one key under tool_call holding an object.
                    call = event.get("tool_call")
                    kind, body = next(((k, v) for k, v in (call.items() if isinstance(call, dict) else ())
                                       if isinstance(v, dict)), ("", {}))
                    result = body.get("result")
                    if event.get("subtype") == "started" and kind == "shellToolCall":
                        command = (body.get("args") or {}).get("command")
                        if command:
                            commands.append(command)
                    elif event.get("subtype") == "completed" and isinstance(result, dict) \
                            and "rejected" in result:
                        denied.append(kind)
                elif etype == "result":
                    usage = event.get("usage") or usage
                continue
```

Update the parser's leading comment to say the backend is an input because Cursor's `system`, `assistant`, `user`, and `result` type names also collide with Claude's.

`extract_session_id` needs no change: its key tuple already reads `session_id`.

- [ ] **Step 5: Run the tests to see them pass**

Run: `bash -n scripts/delegate-codex.sh && python3 -m unittest tests.test_delegate_role tests.test_handoff_config`
Expected: PASS, including every `BackendLifecycle` test on `CodexWorktreeTests`, `ClaudeWorktreeTests`, `CopilotWorktreeTests`, and `CursorWorktreeTests`.

Run: `bash scripts/check-skill-repo.sh . | grep -v skillgantry-workspace | grep -E '^\./'`
Expected: no listed line comes from a file this task changed.

- [ ] **Step 6: Commit**

```bash
git add scripts/delegate-codex.sh scripts/handoff-config.py tests/test_delegate_role.py tests/test_handoff_config.py
git commit -m "feat: Cursor CLI as a fourth delegate-codex.sh backend"
```

---

### Task 2: Setup engine, terminal wizard, and smoke

Requirements: R1.2 and R1.3 (setup side), R1.5, R6.1, R6.3 (terminal), R6.5, R6.6, R9.2 (setup tests).

**Files:**
- Modify: `scripts/handoff-setup.py` (effort table, `validate_backend_efforts`, `validate_backend_models`, new `cursor_bin`, `cursor_catalogue`, `validate_cursor_model`, `_cursor_last_assistant_text`, `cli_available`, `choose_identities`, `smoke`, `interactive`)
- Test: `tests/test_handoff_setup.py`

**Interfaces:**
- Consumes: Task 1's `BACKENDS` row, and `delegate-codex.sh submit --role <cursor identity> --read-only --dry-run` succeeding.
- Produces, used by Task 3:
  - `CURSOR_EFFORTS: Tuple[str, ...] = ("model",)`, and `BACKEND_EFFORTS["cursor"]`
  - `CURSOR_LOGIN_FIX: str`
  - `cursor_bin(env: Mapping[str, str]) -> Optional[str]`
  - `cursor_catalogue(binary: str, env: Mapping[str, str]) -> Tuple[List[Tuple[str, str]], str]`: `(slug, display name)` pairs in catalogue order, `auto` dropped, and `""` or an error message that ends with `CURSOR_LOGIN_FIX`
  - `validate_cursor_model(binary: str, model: str, env: Mapping[str, str], *, cwd: Path) -> Tuple[bool, str]`

- [ ] **Step 1: Write the failing tests**

In `SetupTests.setUp`, after `self.env = os.environ.copy()`, add `self.env.pop("HANDOFF_CURSOR_BIN", None)`: a variable set in the developer's shell must never send a test to the real CLI.

Append before `if __name__ == "__main__":`:

```python
# `cursor-agent models` on build 2026.09.28-64d2043, trimmed to four entries.
CURSOR_MODELS_OUTPUT = (
    "Available models\n\n"
    "auto - Auto (default)\n"
    "gpt-5.4-mini-low - GPT-5.4 Mini Low\n"
    "claude-opus-5-5-high - Claude Opus 5.5 1M High\n"
    "composer-2.5 - Composer 2.5\n\n"
    "Tip: use --model <id> (or /model <id> in interactive mode) to switch. "
    "Parameterized models also accept quoted overrides, e.g. --model "
    "'claude-opus-4-8[context=1m,effort=high,fast=false]'.\n"
)

CURSOR_FAKE = """#!__PYTHON__
import json, os, sys
log = os.environ.get("HANDOFF_TEST_CURSOR_ARGS")
if log:
    with open(log, "a", encoding="utf-8") as handle:
        handle.write(" ".join(sys.argv[1:]) + "\\n")
if sys.argv[1:2] == ["--version"]:
    print("2026.09.28-64d2043")
    sys.exit(0)
if sys.argv[1:2] == ["models"]:
    if os.environ.get("HANDOFF_TEST_CURSOR_MODELS_ERROR"):
        sys.stderr.write(os.environ["HANDOFF_TEST_CURSOR_MODELS_ERROR"] + "\\n")
        sys.exit(1)
    sys.stdout.write(__MODELS__)
    sys.exit(0)
if os.environ.get("HANDOFF_TEST_CURSOR_STDERR"):
    sys.stderr.write(os.environ["HANDOFF_TEST_CURSOR_STDERR"] + "\\n")
    sys.exit(1)
reply = os.environ.get("HANDOFF_TEST_CURSOR_REPLY", "HANDOFF_SMOKE_OK")
for event in (
    {"type": "system", "subtype": "init", "session_id": "s", "model": "GPT-5.4 Mini Low"},
    {"type": "assistant", "message": {"content": [{"type": "text", "text": reply}]}},
    {"type": "result", "subtype": "success", "result": reply, "usage": {"inputTokens": 1}},
):
    print(json.dumps(event))
""".replace("__PYTHON__", sys.executable).replace("__MODELS__", repr(CURSOR_MODELS_OUTPUT))


class CursorSetupTests(SetupTests):
    """The cursor backend in the setup engine, the terminal wizard, and smoke."""

    def setUp(self):
        super().setUp()
        self.cursor_args_log = self.root / "cursor-args.txt"
        cursor = self.bin / "cursor-agent"
        cursor.write_text(CURSOR_FAKE, encoding="utf-8")
        cursor.chmod(0o755)
        self.env["HANDOFF_TEST_CURSOR_ARGS"] = str(self.cursor_args_log)

    def cursor_choices(self, model="gpt-5.4-mini-low", effort="model"):
        return {
            "deep_reasoner": ("claude", "opus", "high"),
            "fast_worker": ("cursor", model, effort),
            "arbiter": ("codex", "gpt-detected", "xhigh"),
        }

    def cursor_calls(self):
        """Invocations other than delegate-codex.sh's --version probe."""

        if not self.cursor_args_log.exists():
            return []
        return [line for line in self.cursor_args_log.read_text(encoding="utf-8").splitlines()
                if line != "--version"]

    def smoke(self):
        return self.run_cli("--smoke", "--repo", str(self.repo), "--timestamp", "2026-09-30T01:02:03Z")

    def test_effort_is_the_model_id_and_nothing_else(self):
        self.assertEqual(("model",), handoff_setup.BACKEND_EFFORTS["cursor"])
        status, _, error = self.run_cli(*self.custom_args(self.cursor_choices(), action="--preview"))
        self.assertEqual((0, ""), (status, error))
        status, _, error = self.run_cli(
            *self.custom_args(self.cursor_choices(effort="high"), action="--preview"))
        self.assertEqual(2, status)
        self.assertIn("with backend=cursor must be one of model", error)
        self.assertIn("Cursor carries effort in the model id", error)

    def test_a_preset_override_onto_cursor_fills_the_effort(self):
        status, _, error = self.run_cli(*self.claude_args(
            "--apply", "--role-backend", "fast_worker=cursor",
            "--role-model", "fast_worker=gpt-5.4-mini-low"))
        self.assertEqual((0, ""), (status, error))
        self.assertEqual("model", self.configured()["fast_worker"]["effort"])

    def test_auto_is_refused_and_the_fix_is_named(self):
        status, _, error = self.run_cli(
            *self.custom_args(self.cursor_choices(model="auto"), action="--preview"))
        self.assertEqual(2, status)
        self.assertIn("'auto', which is refused on cursor", error)
        self.assertIn("handoff-config.py set", error)
        self.assertEqual([], self.cursor_calls())

    def test_no_preset_names_cursor(self):
        self.assertNotIn("cursor", {
            backend for preset in handoff_setup.PRESETS.values()
            for backend, _, _ in preset.values()})

    def test_availability_is_cursor_agent_never_the_ide_or_agent(self):
        self.assertTrue(handoff_setup.cli_available("cursor", self.env))
        elsewhere = self.root / "ide-only"
        elsewhere.mkdir()
        for name in ("cursor", "agent"):
            (elsewhere / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
            (elsewhere / name).chmod(0o755)
        self.env["PATH"] = f"{elsewhere}:/usr/bin:/bin"
        self.assertIsNone(handoff_setup.cursor_bin(self.env))
        self.assertFalse(handoff_setup.cli_available("cursor", self.env))
        self.env["HANDOFF_CURSOR_BIN"] = str(self.bin / "cursor-agent")
        self.assertEqual(str(self.bin / "cursor-agent"), handoff_setup.cursor_bin(self.env))

    def test_the_catalogue_reader_keeps_only_real_slugs(self):
        models, error = handoff_setup.cursor_catalogue(str(self.bin / "cursor-agent"), self.env)
        self.assertEqual("", error)
        self.assertEqual(
            [("gpt-5.4-mini-low", "GPT-5.4 Mini Low"),
             ("claude-opus-5-5-high", "Claude Opus 5.5 1M High"),
             ("composer-2.5", "Composer 2.5")],
            models,
        )

    def test_an_unreadable_catalogue_names_the_login_fix(self):
        self.env["HANDOFF_TEST_CURSOR_MODELS_ERROR"] = (
            "Error: Authentication required. Run 'agent login', pass --api-key/--auth-token, "
            "or set CURSOR_API_KEY/CURSOR_AUTH_TOKEN.")
        models, error = handoff_setup.cursor_catalogue(str(self.bin / "cursor-agent"), self.env)
        self.assertEqual([], models)
        self.assertIn("Authentication required", error)
        self.assertIn("cursor-agent login", error)

    def test_smoke_checks_membership_then_runs_one_read_only_request(self):
        self.assertEqual(0, self.run_cli(*self.custom_args(self.cursor_choices()))[0])
        status, output, error = self.smoke()
        self.assertEqual((0, ""), (status, error))
        self.assertIn("fast_worker: PASS", output)
        calls = self.cursor_calls()
        self.assertEqual("models", calls[0])
        self.assertEqual(2, len(calls))
        self.assertIn("--output-format stream-json --trust --model gpt-5.4-mini-low --mode ask", calls[1])
        self.assertNotIn("--force", calls[1])  # risk-ok: Cursor CLI flag name
        self.assertTrue(self.configured()["fast_worker"]["verified"])

    def test_smoke_refuses_a_base_name_before_the_paid_run(self):
        # Cursor would run `gpt-5.4-mini` as a variant it picks itself.
        self.assertEqual(0, self.run_cli(*self.custom_args(self.cursor_choices(model="gpt-5.4-mini")))[0])
        status, _, error = self.smoke()
        self.assertEqual(1, status)
        self.assertIn("gpt-5.4-mini is not in `cursor-agent models`", error)
        self.assertEqual(["models"], self.cursor_calls())
        self.assertFalse(self.configured()["fast_worker"]["verified"])

    def test_smoke_quotes_cursors_refusal_verbatim(self):
        self.assertEqual(0, self.run_cli(*self.custom_args(self.cursor_choices()))[0])
        refusal = "Cannot use this model: gpt-5.4-mini-low. Available models: auto, composer-2.5"
        self.env["HANDOFF_TEST_CURSOR_STDERR"] = refusal
        status, _, error = self.smoke()
        self.assertEqual(1, status)
        self.assertIn(refusal, error)
        self.assertFalse(self.configured()["fast_worker"]["verified"])

    def test_smoke_needs_the_sentinel_in_the_last_assistant_message(self):
        self.assertEqual(0, self.run_cli(*self.custom_args(self.cursor_choices()))[0])
        self.env["HANDOFF_TEST_CURSOR_REPLY"] = "Sure, happy to help."
        status, _, error = self.smoke()
        self.assertEqual(1, status)
        self.assertIn("unexpected response", error)

    def test_smoke_names_the_login_when_the_catalogue_is_unreadable(self):
        self.assertEqual(0, self.run_cli(*self.custom_args(self.cursor_choices()))[0])
        self.env["HANDOFF_TEST_CURSOR_MODELS_ERROR"] = "Error: Authentication required."
        status, _, error = self.smoke()
        self.assertEqual(1, status)
        self.assertIn("cursor-agent login", error)
        self.assertEqual(["models"], self.cursor_calls())

    def test_terminal_wizard_names_four_backends_and_fills_cursor_effort(self):
        from unittest.mock import call, patch
        answers = ["4", "1", "n", "n", "", "", "n"]
        for identity in handoff_setup.CORE_IDENTITIES:
            if identity == "fast_worker":
                answers.extend(["cursor", "gpt-5.4-mini-low", ""])  # no effort question
            else:
                answers.extend(["claude", "opus", "high", ""])
        answers.append("n")
        with patch("sys.stdin.isatty", return_value=True), \
                patch("builtins.input", side_effect=answers) as prompt:
            status, output, error = self.run_cli("--interactive", "--repo", str(self.repo))
        self.assertEqual(0, status, error)
        self.assertIn(call("fast_worker backend [claude/codex/copilot/cursor]: "),
                      prompt.call_args_list)
        self.assertNotIn(call("fast_worker effort: "), prompt.call_args_list)
        self.assertIn("fast_worker: backend=cursor [custom], model=gpt-5.4-mini-low [custom], "
                      "effort=model [custom]", output)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_handoff_setup`
Expected: FAIL. `BACKEND_EFFORTS["cursor"]` raises `KeyError`, and `cursor_bin` and `cursor_catalogue` do not exist.

- [ ] **Step 3: Implement**

In `scripts/handoff-setup.py`:

```python
# Cursor carries effort inside the model slug (claude-opus-5-5-high), so the
# one value means "set by the model id" and never reaches the command line.
CURSOR_EFFORTS = ("model",)
BACKEND_EFFORTS = {
    "claude": CLAUDE_EFFORTS,
    "codex": CODEX_EFFORTS,
    "copilot": COPILOT_EFFORTS,
    "cursor": CURSOR_EFFORTS,
}
CURSOR_LOGIN_FIX = (
    "run `cursor-agent login`, or set HANDOFF_CURSOR_BIN to the Cursor CLI, then try again"
)
```

`validate_backend_efforts` appends the reason on cursor:

```python
            raise SetupError(
                f"--role-effort for {identity} with backend={backend} must be one of "
                f"{', '.join(supported)}"
                + ("; Cursor carries effort in the model id" if backend == "cursor" else "")
            )
```

`validate_backend_models` covers both catalogue backends. Update its docstring to name copilot and cursor:

```python
    for identity, values in identities.items():
        backend = values["backend"]
        if backend not in ("copilot", "cursor"):
            continue
        model = str(values.get("model") or "").strip()
        if model == "auto":
            raise SetupError(
                f"--role-model for {identity} is 'auto', which is refused on {backend}: "
                "an identity is a deliberate backend+model+effort choice and 'auto' "
                "resolves per request. Name a concrete model, for example with "
                f"'handoff-config.py set --role {identity} --backend {backend} --model <model>'."
            )
        if not model:
            raise SetupError(
                f"--role-model for {identity} with backend={backend} must name a model; "
                "no model name is guessed. (A role-less ad-hoc job passes no --model "
                "and runs on the CLI's own default; a configured identity does not get "
                "that latitude.)"
            )
```

After `copilot_bin`:

```python
def cursor_bin(env: Mapping[str, str]) -> Optional[str]:
    """Resolve Cursor's agent CLI with delegate-codex.sh's precedence.

    ``HANDOFF_CURSOR_BIN`` first, then ``cursor-agent`` on PATH. Never
    ``cursor``, which is the Cursor IDE launcher, and never ``agent``.
    """

    configured = env.get("HANDOFF_CURSOR_BIN", "")
    if configured:
        candidate = (
            configured if "/" in configured
            else shutil.which(configured, path=env.get("PATH"))
        )
        return candidate if candidate and os.access(candidate, os.X_OK) else None
    return shutil.which("cursor-agent", path=env.get("PATH"))


def cursor_catalogue(binary: str, env: Mapping[str, str]) -> Tuple[List[Tuple[str, str]], str]:
    """Read ``cursor-agent models``: (slug, display name) pairs and an error.

    Free, and text only: a header, one ``slug - Display name`` line per model,
    then a tip line. ``auto`` is dropped because an identity never names it.
    The error is empty on success and otherwise ends with the fix.
    """

    try:
        result = subprocess.run(
            [binary, "models"], env=dict(env), text=True,
            capture_output=True, check=False, timeout=15,
        )
    except subprocess.TimeoutExpired:
        return [], f"cursor-agent models timed out after 15 seconds; {CURSOR_LOGIN_FIX}"
    except OSError as error:
        return [], f"cursor-agent models could not start: {error}; {CURSOR_LOGIN_FIX}"
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip() or f"cursor-agent models exited {result.returncode}"
        return [], f"{detail}; {CURSOR_LOGIN_FIX}"
    models = []
    for line in result.stdout.splitlines():
        slug, separator, name = line.strip().partition(" - ")
        if separator and slug != "auto" and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", slug):
            models.append((slug, name.strip()))
    if not models:
        return [], f"cursor-agent models listed no models; {CURSOR_LOGIN_FIX}"
    return models, ""
```

`cli_available` gains, before the `shutil.which` fallback:

```python
    if backend == "cursor":
        return cursor_bin(env) is not None
```

and its docstring names cursor's reason (the backend name is the IDE launcher's name).

After `_copilot_stream_error`:

```python
def validate_cursor_model(
    binary: str,
    model: str,
    env: Mapping[str, str],
    *,
    cwd: Path,
) -> Tuple[bool, str]:
    """Smoke one cursor slug: catalogue membership, then one real run.

    Membership is free and comes first: Cursor accepts a base name outside the
    catalogue and quietly runs a variant it picks. The run costs one small
    request when the slug is usable; Cursor refuses an unusable one before any
    session, at no cost, and its stderr is returned verbatim because it lists
    the models the account can use. ``--mode ask`` makes the run read-only; it
    does not make it tool-free. The pass rule is Claude smoke's: exit 0 and the
    sentinel in the last assistant message.
    """

    catalogue, error = cursor_catalogue(binary, env)
    if error:
        return False, error
    if model not in {slug for slug, _ in catalogue}:
        return False, (
            f"{model} is not in `cursor-agent models`; name a slug the catalogue "
            "lists (Cursor resolves a base name to a variant without saying so)"
        )
    command = [
        binary, "-p",
        "This is a configuration smoke test. Reply with exactly "
        "HANDOFF_SMOKE_OK and nothing else. Do not use tools.",
        "--output-format", "stream-json", "--trust", "--model", model, "--mode", "ask",
    ]
    try:
        result = subprocess.run(
            command, cwd=cwd, env=dict(env), text=True,
            capture_output=True, check=False, timeout=120,
        )
    except subprocess.TimeoutExpired:
        return False, "cursor smoke run timed out after 120 seconds"
    except OSError as error:
        return False, f"cursor smoke run could not start: {error}"
    if result.returncode != 0:
        return False, (result.stderr or "").strip() or f"cursor-agent exited {result.returncode}"
    if "HANDOFF_SMOKE_OK" not in _cursor_last_assistant_text(result.stdout):
        return False, "cursor smoke run returned an unexpected response"
    return True, ""


def _cursor_last_assistant_text(stdout: str) -> str:
    """The text of the last ``assistant`` event. Never ``result.result``,
    which runs every assistant message together."""

    text = ""
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict) and event.get("type") == "assistant":
            blocks = (event.get("message") or {}).get("content") or []
            text = "".join(block.get("text") or "" for block in blocks if isinstance(block, dict))
    return text
```

`choose_identities`, preset path: `"effort": efforts.get(identity, "model" if backend == "cursor" else preset_effort),`.

`smoke`, after the copilot block and before the generic `PASS`:

```python
            if configured[identity]["backend"] == "cursor":
                binary = cursor_bin(env)
                if not binary:
                    failures = True
                    print(f"{identity}: FAIL\ncursor-agent not found; install Cursor CLI "
                          "or set HANDOFF_CURSOR_BIN", file=sys.stderr)
                    continue
                passed, detail = validate_cursor_model(
                    binary, str(configured[identity]["model"]), env, cwd=args.repo)
                if not passed:
                    failures = True
                    print(f"{identity}: FAIL\n{detail}", file=sys.stderr)
                    continue
                successes.append(identity)
                print(f"{identity}: PASS (slug in cursor-agent models, run accepted by Cursor)")
                continue
```

`interactive`, the per-identity loop:

```python
        for identity in identities_for(with_e2e):
            backend = input(f"{identity} backend [claude/codex/copilot/cursor]: ").strip()
            identity_backends.append(f"{identity}={backend}")
            identity_models.append(f"{identity}={input(f'{identity} model: ').strip()}")
            if backend == "cursor":
                # Cursor carries effort in the model slug; there is nothing to ask.
                print(f"{identity} effort: model (set by the Cursor model id)")
                effort = "model"
            else:
                effort = input(f"{identity} effort: ").strip()
            identity_efforts.append(f"{identity}={effort}")
            permission = input(f"{identity} permission [default/allow-all] (default): ").strip() or "default"
            identity_permissions.append(f"{identity}={permission}")
```

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest tests.test_handoff_setup tests.test_delegate_role`
Expected: PASS. `CopilotSetupTests` still passes: its `auto` assertion reads `'auto', which is refused on copilot`.

- [ ] **Step 5: Commit**

```bash
git add scripts/handoff-setup.py tests/test_handoff_setup.py
git commit -m "feat: Cursor in the setup engine, terminal wizard, and smoke"
```

---

### Task 3: Web setup wizard

Requirements: R6.2, R6.3 (web), R6.4, R9.2 (UI tests).

**Files:**
- Modify: `scripts/handoff-setup-ui.py` (new `_cursor_model_options`, `_ensure_model_option`, `build_state`, `normalize_payload`, the matrix subtitle, and the page script's `BACKEND_LABELS`, `BACKEND_LABELS_ORDER`, `EFFORT_LABELS`, `sourceLabel`, `renderCards`)
- Test: `tests/test_handoff_setup_ui.py`

**Interfaces:**
- Consumes: `engine.CURSOR_EFFORTS`, `engine.cursor_bin(env)`, `engine.cursor_catalogue(binary, env)` from Task 2.
- Produces: `build_state()` keys `model_options["cursor"]`, `model_discovery["cursor"]`, `efforts_by_backend["cursor"] == ["model"]`, and a new `model_discovery_failed: Dict[str, bool]` for every backend.

- [ ] **Step 1: Write the failing tests**

In `SetupUITests.setUp`, add `self.env.pop("HANDOFF_CURSOR_BIN", None)` after the `os.environ.copy()`. Update the existing assertions that enumerate backends:
- `test_state_shows_exact_detected_models_and_full_presets`: `efforts_by_backend` gains `"cursor": ["model"]`.
- `CopilotSetupUITests.test_opening_the_wizard_makes_no_copilot_subprocess_call`: `{"claude", "codex", "copilot", "cursor"}`.
- `CopilotSetupUITests.test_one_malformed_entry_costs_that_entry_and_not_the_page`: `["claude", "codex", "copilot", "cursor"]`.

Append before `if __name__ == "__main__":`:

```python
CURSOR_UI_FAKE = """#!/bin/sh
printf '%s\\n' "$*" >> "$HANDOFF_TEST_CURSOR_ARGS"
if [ "$1" = "models" ]; then
  if [ -n "${HANDOFF_TEST_CURSOR_MODELS_ERROR:-}" ]; then
    printf '%s\\n' "$HANDOFF_TEST_CURSOR_MODELS_ERROR" >&2
    exit 1
  fi
  printf 'Available models\\n\\nauto - Auto (default)\\ngpt-5.4-mini-low - GPT-5.4 Mini Low\\ncomposer-2.5 - Composer 2.5\\n\\nTip: use --model <id> to switch.\\n'
  exit 0
fi
exit 0
"""

RETAINED_CURSOR_CONFIG = (
    "schema_version = 2\nrevision = 0\n\n"
    "[hosts.claude_code.identities.deep_reasoner]\n"
    'backend = "claude"\nmodel = "opus"\neffort = "high"\n\n'
    "[hosts.claude_code.identities.fast_worker]\n"
    'backend = "cursor"\nmodel = "gpt-5.2-retired"\neffort = "model"\n\n'
    "[hosts.claude_code.identities.arbiter]\n"
    'backend = "codex"\nmodel = "gpt-detected"\neffort = "xhigh"\n'
)


class CursorSetupUITests(SetupUITests):
    """The Cursor model is picked from `cursor-agent models`, read at page load."""

    def setUp(self):
        super().setUp()
        self.cursor_log = self.root / "cursor-ui-args.txt"
        fake = self.bin / "cursor-agent"
        fake.write_text(CURSOR_UI_FAKE, encoding="utf-8")
        fake.chmod(0o755)
        self.env["HANDOFF_TEST_CURSOR_ARGS"] = str(self.cursor_log)

    def cursor_calls(self):
        return self.cursor_log.read_text(encoding="utf-8").splitlines() if self.cursor_log.exists() else []

    def state(self):
        return handoff_setup_ui.build_state(self.repo, self.env)

    def cursor_payload(self, model="gpt-5.4-mini-low", effort="model"):
        return {
            "mode": "custom",
            "identities": {
                "deep_reasoner": {"backend": "claude", "model": "opus", "effort": "high"},
                "fast_worker": {"backend": "cursor", "model": model, "effort": effort},
                "arbiter": {"backend": "codex", "model": "gpt-detected", "effort": "xhigh"},
            },
            "review": {"spec_max_rounds": 1, "implementation_max_rounds": 3},
            "scope": "project",
            "exclude_choice": "track",
            "routing_action": "none",
            "write_agents": False,
            "smoke": False,
        }

    def test_opening_the_wizard_runs_cursor_agent_models_and_nothing_else(self):
        state = self.state()
        self.assertEqual(["models"], self.cursor_calls())
        options = state["model_options"]["cursor"]
        self.assertEqual(["gpt-5.4-mini-low", "composer-2.5"], [o["value"] for o in options])
        self.assertEqual("GPT-5.4 Mini Low", options[0]["label"])
        self.assertEqual({"cursor-agent models"}, {o["source"] for o in options})
        self.assertEqual(["model"], state["efforts_by_backend"]["cursor"])
        self.assertEqual("Read from cursor-agent models", state["model_discovery"]["cursor"])
        self.assertFalse(state["model_discovery_failed"]["cursor"])
        self.assertNotIn("cursor", state["clis"])

    def test_the_only_effort_is_model(self):
        options = self.state()["model_options"]
        payload = handoff_setup_ui.normalize_payload(
            self.cursor_payload(), repo=self.repo, env=self.env, model_options=options)
        self.assertEqual("model", payload["identities"]["fast_worker"]["effort"])
        with self.assertRaises(handoff_setup_ui.UIError) as refusal:
            handoff_setup_ui.normalize_payload(
                self.cursor_payload(effort="high"), repo=self.repo, env=self.env,
                model_options=options)
        self.assertIn("allowed values: model", str(refusal.exception))

    def test_a_model_the_catalogue_does_not_offer_is_refused(self):
        controller = handoff_setup_ui.SetupController(self.repo, self.env)
        with self.assertRaises(handoff_setup_ui.UIError) as refusal:
            controller.preview(self.cursor_payload(model="gpt-5.4-mini"))
        self.assertIn("not in `cursor-agent models`", str(refusal.exception))
        self.assertFalse((self.repo / ".handoff" / "config.toml").exists())

    def test_a_missing_cli_offers_no_model_and_names_the_fix(self):
        (self.bin / "cursor-agent").unlink()
        state = self.state()
        self.assertEqual([], state["model_options"]["cursor"])
        self.assertIn("HANDOFF_CURSOR_BIN", state["model_discovery"]["cursor"])
        self.assertTrue(state["model_discovery_failed"]["cursor"])

    def test_a_logged_out_cli_names_cursor_agent_login(self):
        self.env["HANDOFF_TEST_CURSOR_MODELS_ERROR"] = "Error: Authentication required."
        state = self.state()
        self.assertEqual([], state["model_options"]["cursor"])
        self.assertIn("cursor-agent login", state["model_discovery"]["cursor"])

    def test_a_retained_slug_is_kept_marked_and_a_failure_still_shows(self):
        config = self.repo / ".handoff" / "config.toml"
        config.parent.mkdir()
        config.write_text(RETAINED_CURSOR_CONFIG, encoding="utf-8")
        kept = {o["value"]: o for o in self.state()["model_options"]["cursor"]}
        self.assertEqual("Not in the current catalogue", kept["gpt-5.2-retired"]["label"])
        self.assertEqual(["model"], kept["gpt-5.2-retired"]["efforts"])
        self.env["HANDOFF_TEST_CURSOR_MODELS_ERROR"] = "Error: Authentication required."
        state = self.state()
        self.assertEqual(["gpt-5.2-retired"], [o["value"] for o in state["model_options"]["cursor"]])
        self.assertTrue(state["model_discovery_failed"]["cursor"])
        self.assertIn("cursor-agent login", state["model_discovery"]["cursor"])

    def test_the_page_wires_the_cursor_backend(self):
        source = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("cursor:'Cursor'", source)
        self.assertIn("'cursor-agent models':'Read from cursor-agent models',", source)
        self.assertIn("model:'Set by the model id'", source)
        # The discovery failure shows beside a retained slug, not only on an empty list.
        self.assertIn("state.model_discovery_failed[values.backend]", source)
        # The effort field is locked on cursor.
        self.assertIn("values.backend !== 'cursor'", source)
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_handoff_setup_ui`
Expected: FAIL with `KeyError: 'cursor'` from `state["model_options"]` and `model_discovery_failed`.

- [ ] **Step 3: Implement**

After `_copilot_model_options`:

```python
def _cursor_model_options(env: Mapping[str, str]) -> Tuple[List[Dict[str, Any]], str]:
    """Offer the slugs ``cursor-agent models`` lists, read at page load.

    The call is free and takes about a second, the same cost profile as
    codex's ``model/list``. Nothing is guessed when it fails.
    """

    binary = engine.cursor_bin(env)
    if not binary:
        return [], ("Cursor CLI not found. Install it, or set HANDOFF_CURSOR_BIN, "
                    "then start the wizard again")
    catalogue, error = engine.cursor_catalogue(binary, env)
    if error:
        return [], error
    return [
        {
            "value": slug,
            "label": name or slug,
            "description": "",
            "source": "cursor-agent models",
            "efforts": list(engine.CURSOR_EFFORTS),
            "is_default": False,
        }
        for slug, name in catalogue
    ], "Read from cursor-agent models"
```

`_ensure_model_option`: before `options.append`, mark a retained cursor slug, and use `label` in the appended dict:

```python
    label = value
    if backend == "cursor":
        # Slugs leave the catalogue between Cursor releases. Keep the configured
        # one and say so, rather than replace it.
        label = "Not in the current catalogue"
```

`build_state`:

```python
    cursor_options, cursor_discovery = _cursor_model_options(env)
    option_sets = {
        "claude": claude_options,
        "codex": codex_options,
        "copilot": copilot_options,
        "cursor": cursor_options,
    }
    # Taken before configured values are added back: a retained model fills
    # the list, and the page must still say that discovery failed.
    discovery_failed = {backend: not options for backend, options in option_sets.items()}
```

`model_discovery` gains `"cursor": cursor_discovery`, and the returned state gains `"model_discovery_failed": discovery_failed`.

`normalize_payload`, the membership guard:

```python
            if option is None and backend in ("copilot", "cursor") and model != "auto":
                listing = ("your Copilot model list" if backend == "copilot"
                           else "`cursor-agent models`")
                raise UIError(
                    f"model {model} for {identity} is not in {listing}; "
                    "pick one the list offers, or fix what the page names and start "
                    "the wizard again"
                )
```

Keep the copilot wording byte-for-byte ("is not in your Copilot model list; pick one the list offers, or fix the login it names and start the wizard again") if an existing test asserts more than `not in your Copilot model list`. Check with `grep -n "not in your Copilot" tests/`.

Page subtitle (the `<p>` under `The three Agent Handoff roles`):

```html
<p>Codex models are read from your local account, Claude models use the official CLI aliases, Copilot models come from your entitlement, and Cursor models from <code>cursor-agent models</code>. A Cursor model carries its own effort.</p>
```

Page script:

```js
    const BACKEND_LABELS = {claude:'Claude Code', codex:'Codex', copilot:'GitHub Copilot', cursor:'Cursor'};
    const BACKEND_LABELS_ORDER = ['claude','codex','copilot','cursor'];
```

`EFFORT_LABELS` gains `model:'Set by the model id',`. `sourceLabel` gains `'cursor-agent models':'Read from cursor-agent models',`. In `renderCards`:

```js
        const discovery = esc(state.model_discovery[values.backend] || 'No models available');
        const sourceLine = models.length
          ? `Source: ${esc(sourceLabel(source))}`
            + (state.model_discovery_failed[values.backend] ? ` · ${discovery}` : '')
          : discovery;
```

and the effort `<select>` is locked on cursor: `${efforts.length && values.backend !== 'cursor' ? '' : 'disabled'}`. `syncEffort` already sets `model`, the only value, so a disabled field still submits it.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest tests.test_handoff_setup_ui tests.test_handoff_setup`
Expected: PASS, including the unchanged `test_opening_the_wizard_makes_no_copilot_subprocess_call`.

Manual check: `python3 scripts/handoff-setup-ui.py --repo <scratch repo>`, pick Cursor for one role, confirm the dropdown shows display names, the effort field reads "Set by the model id" and is disabled, and a preview writes nothing.

- [ ] **Step 5: Commit**

```bash
git add scripts/handoff-setup-ui.py tests/test_handoff_setup_ui.py
git commit -m "feat: the setup wizard reads Cursor models from cursor-agent models"
```

---

### Task 4: Evidence: receipt v7, cost receipt, session view

Requirements: R7.1 to R7.5, R9.2 (receipt and cost fixtures).

**Files:**
- Modify: `docs/receipt-schema.json`, `scripts/make-receipt.py`, `scripts/validate-receipt.py`, `scripts/render-cost-receipt.py`, `assets/cost-receipt.html`, `scripts/handoff-session-ui.py`, `examples/session-receipt.md`, `scripts/check-skill-repo.sh:178`
- Modify fixture: `tests/fixtures/session-view/receipt-20260910T003000Z.md`
- Test: `tests/test_receipt.py`, `tests/test_cost_receipt.py`, `tests/test_session_ui.py`

**Interfaces:**
- Consumes: nothing from other tasks (the tests write `meta` files by hand).
- Produces: receipt fields `cursor_jobs` and `cursor_job_durations`; `make-receipt.py --cursor-jobs`; `render_cost_receipt.cursor_rejections(events: list[dict]) -> list[tuple[str, dict]]`; `summarize(rows)["cursor_meter"] == {"jobs": int}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_receipt.py`:
- `fields()`: add `"cursor_jobs": "1"`, `"cursor_job_durations": "none"` after the copilot pair; `"receipt_schema_version": "7"`.
- `test_old_schema_version_fails`: `assert_one_failure("receipt_schema_version must be 7", receipt_schema_version="6")`.
- `test_non_integer_job_counts_fail`: add `self.assert_one_failure("cursor_jobs must be an integer", cursor_jobs="two")`.
- `test_job_durations_accept_measured_entries_and_reject_junk`: add `"cursor_job_durations"` to the tuple.
- `test_roles_used_rejects_an_unknown_host`: the placeholder host `"cursor"` becomes `"gemini"`.
- `RECEIPT_ARGS`: add `"--cursor-jobs", "0",` after the copilot pair.
- `test_an_unknown_backend_line_buckets_as_codex`: `backend="cursor"` becomes `backend="gemini"`, and assert `self.assertEqual("none", emitted["cursor_job_durations"])`.
- New tests:

```python
    def test_a_v6_receipt_no_longer_validates(self):
        v6 = fields(receipt_schema_version="6")
        del v6["cursor_jobs"]
        del v6["cursor_job_durations"]
        failures = " ".join(validate_receipt.validate(v6))
        self.assertIn("missing field: cursor_jobs", failures)
        self.assertIn("missing field: cursor_job_durations", failures)

    def test_roles_used_accepts_a_cursor_host(self):
        self.assertEqual([], validate_receipt.validate(fields(
            roles_used='[{"role": "fast_worker", "host": "cursor", '
            '"model": "claude-opus-5-5-high", "effort": "model", "verified": true}]')))
```

- `test_mixed_backend_jobs_are_partitioned_by_their_meta`: add

```python
        self.write_job(repo, "job-cu", started + timedelta(minutes=9),
                       started + timedelta(minutes=13), backend="cursor")
```

pass `"--cursor-jobs", "1"`, and assert `self.assertEqual("job-cu=4min 00sec", emitted["cursor_job_durations"])`.

`tests/fixtures/session-view/receipt-20260910T003000Z.md`: add `cursor_jobs: 0` and `cursor_job_durations: none` after the copilot pair; `receipt_schema_version: 7`.

`tests/test_cost_receipt.py`. Add `import shutil`, `import subprocess`, and `from datetime import datetime, timedelta, timezone` if not present.
- `receipt_text()`: add `"cursor_jobs": "1"`, `"cursor_job_durations": "job-d=4min 00sec"`; version `"7"`.
- Any other `receipt_text()` caller whose repo has no `job-d` passes `cursor_jobs="0", cursor_job_durations="none"`.
- `LoadReceiptTests.setUp`: `make_repo(..., {"job-a": "codex", "job-b": "claude", "job-c": "copilot", "job-d": "cursor"})`; `test_valid_receipt_loads_jobs_in_order` expects `["codex", "claude", "copilot", "cursor"]`; `test_schema_version_five_is_refused` also refuses `"6"`; `test_none_contributes_no_jobs` adds `cursor_jobs="0", cursor_job_durations="none"`. Add `test_a_cursor_count_disagreeing_with_entries_is_refused` with `receipt_text(cursor_jobs="2")` and the text `"cursor_jobs"`.
- Replace `test_denials_are_attributed_to_both_reporting_backends` (Markdown) and `test_template_attributes_denials_to_both_reporting_backends` (template) with the three-backend wording below.
- `CliTests.setUp`: add a cursor job with two rejections, and the pair in `self.text`:

```python
        cursor = jobs / "job-d"
        cursor.mkdir(parents=True)
        (cursor / "meta").write_text("backend=cursor\nmodel=gpt-5.4-mini\nrole=fast_worker\nlabel=l\n")
        (cursor / "exit_code").write_text("0\n")
        (cursor / "log.jsonl").write_text(
            "\n".join(json.dumps(e) for e in (CURSOR_INIT, CURSOR_REJECTED, CURSOR_REJECTED,
                                               CURSOR_RESULT)) + "\n")
```

and `cursor_jobs="1", cursor_job_durations="job-d=4min 00sec"` in the `receipt_text(...)` call. The class docstring says four-way.

New fixtures and tests:

```python
CURSOR_INIT = {"type": "system", "subtype": "init", "session_id": "s1",
               "model": "GPT-5.4 Mini Low", "permissionMode": "default"}
CURSOR_THINKING = {"type": "thinking", "subtype": "delta", "text": "hm", "session_id": "s1"}
CURSOR_REJECTED = {"type": "tool_call", "subtype": "completed", "call_id": "call_X\nfc_1",
                   "tool_call": {"shellToolCall": {"result": {"rejected": {
                       "command": "curl https://example.com", "reason": ""}}}},
                   "session_id": "s1"}
CURSOR_RESULT = {"type": "result", "subtype": "success", "is_error": False,
                 "result": "first message second message", "session_id": "s1",
                 "usage": {"inputTokens": 130, "outputTokens": 17,
                           "cacheReadTokens": 17408, "cacheWriteTokens": 0}}


class CursorFoldTests(unittest.TestCase):
    def test_tokens_come_from_the_last_result_and_reasoning_is_unknown(self):
        folded = rcr.fold_usage([CURSOR_INIT, CURSOR_THINKING, CURSOR_RESULT], "cursor")
        self.assertEqual({"input": 130, "cache_read": 17408, "cache_write": 0,
                          "output": 17, "reasoning": None}, folded["usage"])
        self.assertIsNone(folded["cost_usd"])
        self.assertEqual(["GPT-5.4 Mini Low"], folded["models"])

    def test_denials_count_typed_rejections(self):
        folded = rcr.fold_usage([CURSOR_REJECTED, CURSOR_RESULT, CURSOR_REJECTED], "cursor")
        self.assertEqual(2, folded["denials"])

    def test_no_result_is_unknown_never_zero(self):
        folded = rcr.fold_usage([CURSOR_INIT], "cursor")
        self.assertEqual({c: None for c in rcr.COUNTERS}, folded["usage"])

    def test_a_cursor_log_never_reaches_the_claude_parser(self):
        # Claude's fold reads snake_case and permission_denials, so on a cursor
        # log it would report unknown tokens and a measured zero denials.
        claude = rcr.fold_usage([CURSOR_REJECTED, CURSOR_RESULT], "claude")
        cursor = rcr.fold_usage([CURSOR_REJECTED, CURSOR_RESULT], "cursor")
        self.assertEqual((None, 0), (claude["usage"]["input"], claude["denials"]))
        self.assertEqual((130, 1), (cursor["usage"]["input"], cursor["denials"]))

    def test_an_unknown_backend_reads_nothing(self):
        folded = rcr.fold_usage([CURSOR_RESULT], "gemini")
        self.assertEqual({c: None for c in rcr.COUNTERS}, folded["usage"])
        self.assertIsNone(folded["denials"])

    def test_an_unexpected_tool_call_shape_is_skipped(self):
        odd = [{"type": "tool_call", "subtype": "completed", "tool_call": {"shellToolCall": None}},
               {"type": "tool_call", "subtype": "completed",
                "tool_call": {"newToolCall": {"result": "rejected"}}},
               {"type": "tool_call", "subtype": "completed", "tool_call": []}]
        self.assertEqual(0, rcr.fold_usage(odd, "cursor")["denials"])


class CursorJobRowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)

    def write_job(self, job_id, meta, events=()):
        job = self.tmp / ".handoff" / "jobs" / job_id
        job.mkdir(parents=True)
        (job / "meta").write_text(meta)
        (job / "exit_code").write_text("0\n")
        (job / "log.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))

    def row(self, job_id):
        return rcr.job_row(self.tmp, {"job_id": job_id, "backend": "cursor", "running": False})

    def test_the_model_column_shows_what_ran_then_falls_back_to_the_slug(self):
        # Requested as a base name; init names the variant Cursor ran.
        self.write_job("job-k", "backend=cursor\nmodel=gpt-5.4-mini\n", [CURSOR_INIT, CURSOR_RESULT])
        self.write_job("job-k-r2", "backend=cursor\nmodel=inherit\nparent=job-k\n",
                       [CURSOR_INIT, CURSOR_RESULT])
        # Killed before init: no display name, so the slug, through parent=.
        self.write_job("job-k-r3", "backend=cursor\nmodel=inherit\nparent=job-k-r2\n")
        # Rejected at launch: an empty log.
        self.write_job("job-e", "backend=cursor\nmodel=no-such-model\n")
        self.assertEqual("GPT-5.4 Mini Low", self.row("job-k")["model"])
        self.assertEqual("GPT-5.4 Mini Low", self.row("job-k-r2")["model"])
        self.assertEqual("gpt-5.4-mini", self.row("job-k-r3")["model"])
        self.assertEqual("no-such-model", self.row("job-e")["model"])
        self.assertEqual({c: None for c in rcr.COUNTERS}, self.row("job-e")["usage"])

    def test_a_parent_and_its_fix_round_sum_and_carry_no_cost(self):
        self.write_job("job-k", "backend=cursor\nmodel=gpt-5.4-mini\n", [CURSOR_INIT, CURSOR_RESULT])
        self.write_job("job-k-r2", "backend=cursor\nmodel=inherit\nparent=job-k\n",
                       [CURSOR_INIT, CURSOR_RESULT])
        summary = rcr.summarize([self.row("job-k"), self.row("job-k-r2")])
        self.assertEqual(260, summary["outside_driver"]["usage"]["input"]["value"])
        self.assertEqual({"jobs": 2}, summary["cursor_meter"])
        self.assertFalse(summary["outside_driver"]["cost_applicable"])


class FourBackendRoundTripTests(unittest.TestCase):
    def test_one_job_per_backend_makes_a_receipt_both_readers_accept(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp)
        started = datetime.now(timezone.utc) - timedelta(minutes=30)
        backends = ("codex", "claude", "copilot", "cursor")
        for offset, backend in enumerate(backends):
            job = tmp / ".handoff" / "jobs" / f"job-{backend}"
            job.mkdir(parents=True)
            submitted = (started + timedelta(minutes=offset + 1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            (job / "meta").write_text(
                f"backend={backend}\nmodel=m\nrole=fast_worker\nlabel=l\nsubmitted_at={submitted}\n")
            (job / "exit_code").write_text("0\n")
        made = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "make-receipt.py"), "--repo", str(tmp),
             "--phase", "delegated implementation", "--claude-session", "none",
             "--checks", "unittest", "--codex-jobs", "1", "--cc-jobs", "1",
             "--copilot-jobs", "1", "--cursor-jobs", "1",
             "--started-at", started.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "--roles-used", '[{"role":"fast_worker","host":"cursor","model":"m",'
                             '"effort":"model","verified":false}]'],
            capture_output=True, text=True, check=False)
        self.assertEqual(0, made.returncode, made.stderr)
        receipt = next((tmp / ".handoff" / "receipts").glob("receipt-*.md"))
        checked = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "validate-receipt.py"), str(receipt)],
            capture_output=True, text=True, check=False)
        self.assertEqual(0, checked.returncode, checked.stdout)
        loaded = rcr.load_receipt(receipt.read_text(encoding="utf-8"), tmp)
        self.assertEqual(list(backends), [job["backend"] for job in loaded["jobs"]])
        for field in ("codex_jobs", "cc_jobs", "copilot_jobs", "cursor_jobs"):
            self.assertEqual("1", loaded["fields"][field])
```

Add to `SummaryTests`:

```python
    def test_denials_sum_across_claude_copilot_and_cursor_jobs(self):
        summary = rcr.summarize([row("a", "claude", 1, denials=1),
                                 row("b", "copilot", 1, denials=2),
                                 row("c", "cursor", 1, denials=3)])
        self.assertEqual(6, summary["denials"])

    def test_a_run_without_cursor_has_no_cursor_jobs(self):
        self.assertEqual({"jobs": 0}, rcr.summarize([row("a", "codex", 1)])["cursor_meter"])
```

Add to `MarkdownTests`:

```python
    def test_a_cursor_job_reads_no_cost_figure_rather_than_unknown(self):
        out = rcr.render_markdown(self.payload([row("job-cu", "cursor", 130)]))
        line = next(l for l in out.splitlines() if l.startswith("| `job-cu`"))
        self.assertTrue(line.endswith("| n/a - no cost figure |"), line)

    def test_the_summary_names_the_cursor_meter_only_when_it_ran(self):
        out = rcr.render_markdown(self.payload([row("job-cu", "cursor", 130)]))
        self.assertIn("**Ran on the Cursor meter** (1 job). Cursor reports tokens but no "
                      "cost figure", out)
        self.assertNotIn("Cursor meter", rcr.render_markdown(self.payload()))

    def test_denials_name_every_reporting_backend(self):
        out = rcr.render_markdown(self.payload([row("c", "cursor", 1, denials=2)]))
        self.assertIn("claude-backed, copilot-backed, and cursor-backed jobs: **2**", out)
```

Add to `TemplateTests`:

```python
    def test_template_renders_the_cursor_meter_and_cost_cell(self):
        for needle in ("cursor_meter", "Ran on the Cursor meter", "n/a - no cost figure",
                       "and cursor-backed jobs"):
            self.assertIn(needle, self.html)
```

Add to `CliTests`:

```python
    def test_a_cursor_job_with_two_rejections_reports_two(self):
        row = rcr.job_row(self.tmp, {"job_id": "job-d", "backend": "cursor", "running": False})
        self.assertEqual((2, "GPT-5.4 Mini Low"), (row["denials"], row["model"]))
        self.write("receipt-20260908T155001Z.md")
        self.assertEqual(0, self.run_cli()[0])
        md = (self.tmp / ".handoff" / "cost-receipts" / "cost-receipt-20260908T155001Z.md").read_text()
        line = next(l for l in md.splitlines() if l.startswith("| `job-d`"))
        self.assertIn("GPT-5.4 Mini Low", line)
        self.assertIn("n/a - no cost figure", line)
```

`tests/test_session_ui.py`, a new test in `SessionTests` (add `import json` if missing):

```python
    def test_cursor_denials_are_reported_and_an_unknown_backend_is_not_read(self):
        job = self.repo / '.handoff/jobs/job-a'
        rejected = {'type': 'tool_call', 'subtype': 'completed', 'call_id': 'c\nf',
                    'tool_call': {'shellToolCall': {'result': {'rejected': {
                        'command': 'curl https://example.com', 'reason': ''}}}}}
        (job / 'log.jsonl').write_text(json.dumps(rejected) + '\n' + json.dumps(rejected) + '\n')
        found = ui.denials(job, 'cursor')
        self.assertEqual('reported', found['state'])
        self.assertEqual(2, len(found['items']))
        self.assertEqual({'tool': 'shellToolCall', 'command': 'curl https://example.com'},
                         found['items'][0])
        # Not the copilot branch by default.
        self.assertEqual({'state': 'not reported (gemini)', 'items': []}, ui.denials(job, 'gemini'))
```

- [ ] **Step 2: Run the tests to see them fail**

Run: `python3 -m unittest tests.test_receipt tests.test_cost_receipt tests.test_session_ui`
Expected: FAIL, mostly `unknown fields: cursor_job_durations, cursor_jobs`, `receipt_schema_version must be 6`, and `KeyError: 'cursor_meter'`.

- [ ] **Step 3: Implement**

`docs/receipt-schema.json`: `$id` becomes `handoff.receipt.v7`; the top description says version 7 and adds "cursor_jobs/cursor_job_durations for cursor-backed ones" and "All eight"; add both names to `required` after the copilot pair; add the two properties after `copilot_job_durations`, copying the copilot pair's type, minimum, and pattern, with descriptions that say "cursor-backed"; `roles_used[].host` enum gains `"cursor"`; the `receipt_schema_version` `const` becomes `7`.

`scripts/make-receipt.py`: `BACKENDS = ("codex", "claude", "copilot", "cursor")`;

```python
    parser.add_argument("--cursor-jobs", default="0", help="Number of cursor-backed delegate-codex.sh jobs including fix rounds.")
```

and in `fields`, after the copilot pair:

```python
        "cursor_jobs": args.cursor_jobs,
        "cursor_job_durations": durations["cursor"],
```

with `"receipt_schema_version": "7"`. The module docstring's usage and tips name `--cursor-jobs`.

`scripts/validate-receipt.py`: `ROLE_HOSTS = {"claude_code", "codex", "copilot", "cursor"}`; `REQUIRED_FIELDS` gains `"cursor_jobs"` and `"cursor_job_durations"` after the copilot pair; the count loop reads `("codex_jobs", "cc_jobs", "copilot_jobs", "cursor_jobs")`; the durations loop adds `"cursor_job_durations"`; the version gate reads `"7"` in the test and the message.

`scripts/render-cost-receipt.py`:

```python
SCHEMA_VERSION = "7"

BACKEND_FIELDS = {
    "codex": ("codex_jobs", "codex_job_durations"),
    "claude": ("cc_jobs", "cc_job_durations"),
    "copilot": ("copilot_jobs", "copilot_job_durations"),
    "cursor": ("cursor_jobs", "cursor_job_durations"),
}
```

`USAGE_FIELDS` gains:

```python
    # Cursor reports no reasoning count.
    "cursor": {
        "input": "inputTokens",
        "cache_read": "cacheReadTokens",
        "cache_write": "cacheWriteTokens",
        "output": "outputTokens",
    },
```

After `_fold_copilot`:

```python
def cursor_rejections(events: list[dict]) -> list[tuple[str, dict]]:
    """(tool kind, rejected payload) for each tool call Cursor refused.

    The kind is the one key under `tool_call`. The refusal is typed: a
    `completed` event whose result object holds a `rejected` key.
    """
    found = []
    for event in events:
        call = event.get("tool_call")
        if (event.get("type") != "tool_call" or event.get("subtype") != "completed"
                or not isinstance(call, dict)):
            continue
        for kind, body in call.items():
            result = body.get("result") if isinstance(body, dict) else None
            if isinstance(result, dict) and "rejected" in result:
                payload = result["rejected"]
                found.append((kind, payload if isinstance(payload, dict) else {}))
    return found


def _fold_cursor(events: list[dict]) -> dict:
    """Tokens from the last `result`, denials from typed rejections, and the
    model from `system/init`, which names the display name of what ran.

    Cursor reports four token counters, no reasoning count, and no cost. Usage
    is per invocation, so a parent and its fix round are two rows that sum. A
    job with no `result` event has unknown usage, never zero.
    """
    usage = _blank_usage()
    models: list[str] = []
    seen_terminal = 0
    for event in events:
        if event.get("type") == "system" and event.get("subtype") == "init" and event.get("model"):
            models = [event["model"]]
        elif event.get("type") == "result":
            seen_terminal += 1
            usage = _blank_usage()
            raw = event.get("usage") or {}
            for counter, field in USAGE_FIELDS["cursor"].items():
                _add(usage, counter, raw.get(field))
    return {"usage": usage, "cost_usd": None, "denials": len(cursor_rejections(events)),
            "models": models, "repeated": seen_terminal > 1,
            "credits": {key: None for key in CREDITS}}
```

`fold_usage`: after the copilot early return add `if backend == "cursor": return _fold_cursor(events)`, and change the claude `else:` to `elif backend == "claude":`. Add a comment after that block: any other backend falls through with every counter unknown and denials unreported, so no log is read with another backend's parser.

`job_row`:

```python
    model = resolve_model(repo, job["job_id"])
    if job["backend"] == "cursor" and folded["models"]:
        # init names the model that ran. A job rejected at launch, or killed
        # before init, has none and keeps the slug from meta.
        model = folded["models"][-1]
```

and the returned dict uses `"model": model`.

`summarize` returns `"cursor_meter": {"jobs": sum(1 for r in rows if r["backend"] == "cursor")}` as well. Its docstring names the Cursor meter: tokens only, in the outside-driver figure, and no cost.

`render_markdown`, after the credit sentence:

```python
    if summary["cursor_meter"]["jobs"]:
        out += [f"**Ran on the Cursor meter** ({_job_count(summary['cursor_meter']['jobs'])}). "
                "Cursor reports tokens but no cost figure, so none is shown.", ""]
```

the denials sentence reads `"Permission denials across claude-backed, copilot-backed, and cursor-backed jobs: "`, and the cost cell table gains `"cursor": "n/a - no cost figure"`. The module docstring adds "cursor jobs yield token counters and no cost figure".

`assets/cost-receipt.html`: after the copilot credit card,

```js
  const cursorJobs = data.summary.cursor_meter.jobs;
  if (cursorJobs) {
    const cursorCard = section("card lead");
    const cursorHead = el("h2", "Ran on the Cursor meter ");
    cursorHead.appendChild(el("span", jobCount(cursorJobs), "count"));
    cursorCard.appendChild(cursorHead);
    cursorCard.appendChild(el("p", "Cursor reports tokens but no cost figure, so none is shown.", "note"));
  }
```

the denials line reads `"Permission denials across claude-backed, copilot-backed, "` + `` `and cursor-backed jobs: ${data.summary.denials}. A denied tool call does ` ``, and the cost cell becomes

```js
        j.backend === "codex" ? absent("n/a - subscription")
          : j.backend === "copilot" ? absent("n/a - AI credits")
          : j.backend === "cursor" ? absent("n/a - no cost figure")
          : (j.cost_usd === null ? absent("unknown") : measured(j.cost_usd))];
```

`scripts/handoff-session-ui.py` `denials()`, after the claude block:

```python
    if backend == 'cursor':
        return {'state': 'reported', 'items': [
            {'tool': kind, 'command': rejected.get('command', 'details unavailable')}
            for kind, rejected in cost.cursor_rejections(events)]}
    if backend != 'copilot':
        return {'state': f'not reported ({backend})', 'items': []}
```

The copilot code that follows is unchanged.

`examples/session-receipt.md`: add `cursor_jobs: 0` and `cursor_job_durations: none` after the copilot pair, `receipt_schema_version: 7`, and the notes line about the partition says "`cursor_jobs` for `backend=cursor`; the four are partitioned". `scripts/check-skill-repo.sh:178`: "v6 schema" becomes "v7 schema".

Run `grep -n '"6"\|v6\|schema_version' scripts/showcase-cost-ledger.py` and confirm the ledger does not read the receipt version; if it does not, leave it.

- [ ] **Step 4: Run the tests to see them pass**

Run: `python3 -m unittest tests.test_receipt tests.test_cost_receipt tests.test_session_ui && python3 scripts/validate-receipt.py examples/session-receipt.md`
Expected: PASS and `PASS Handoff session receipt`.

Run the CLAUDE.md roundtrip with `--cursor-jobs 0` added, piped through `validate-receipt.py -`. Expected: `PASS`.

- [ ] **Step 5: Commit**

```bash
git add docs/receipt-schema.json scripts/make-receipt.py scripts/validate-receipt.py scripts/render-cost-receipt.py assets/cost-receipt.html scripts/handoff-session-ui.py examples/session-receipt.md scripts/check-skill-repo.sh tests/fixtures/session-view/receipt-20260910T003000Z.md tests/test_receipt.py tests/test_cost_receipt.py tests/test_session_ui.py
git commit -m "feat: receipt v7 and cost evidence for cursor jobs"
```

---

### Task 5: Transcript viewer

Requirements: R7.6, R7.4 (the viewer's side), R9.2 (viewer fixture).

**Files:**
- Modify: `assets/transcript-viewer.html` (the `viewer-normalize` script block: `HV.inferBackend`, `HV.normalize`, two new helpers)
- Create: `tests/fixtures/transcript/cursor-basic.jsonl`
- Test: `tests/test_transcript_viewer.mjs`

**Interfaces:**
- Consumes: nothing.
- Produces: `HV.normalize(logText, 'cursor')` rows; `HV.inferBackend` returns `'cursor'`.

- [ ] **Step 1: Write the fixture**

`tests/fixtures/transcript/cursor-basic.jsonl`, one JSON object per line. The `\n` inside each `call_id` is a JSON escape, so each id holds a real newline once parsed.

```jsonl
{"type":"system","subtype":"init","apiKeySource":"login","cwd":"/repo","session_id":"cur-1","model":"GPT-5.4 Mini Low","permissionMode":"default"}
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Fix the typo and run the checks."}]},"session_id":"cur-1"}
{"type":"thinking","subtype":"delta","text":"**Reading the file**","session_id":"cur-1","timestamp_ms":1790694467824}
{"type":"thinking","subtype":"completed","session_id":"cur-1","timestamp_ms":1790694469198}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"I'll fix the typo first."}]},"session_id":"cur-1"}
{"type":"tool_call","subtype":"started","call_id":"call_E\nfc_1","tool_call":{"editToolCall":{"args":{"path":"tracked.txt"}}},"session_id":"cur-1"}
{"type":"tool_call","subtype":"completed","call_id":"call_E\nfc_1","tool_call":{"editToolCall":{"args":{"path":"tracked.txt"},"result":{"success":{}}}},"session_id":"cur-1"}
{"type":"tool_call","subtype":"started","call_id":"call_S\nfc_2","tool_call":{"shellToolCall":{"args":{"command":"bash check.sh"}}},"session_id":"cur-1"}
{"type":"tool_call","subtype":"completed","call_id":"call_S\nfc_2","tool_call":{"shellToolCall":{"args":{"command":"bash check.sh"},"result":{"success":{"command":"bash check.sh","exitCode":0,"stdout":"checks ok","stderr":""}}}},"session_id":"cur-1"}
{"type":"tool_call","subtype":"started","call_id":"call_C\nfc_3","tool_call":{"shellToolCall":{"args":{"command":"curl https://example.com"}}},"session_id":"cur-1"}
{"type":"tool_call","subtype":"completed","call_id":"call_C\nfc_3","tool_call":{"shellToolCall":{"result":{"rejected":{"command":"curl https://example.com","workingDirectory":"/repo","reason":"","isReadonly":false}}}},"session_id":"cur-1"}
{"type":"tool_call","subtype":"started","call_id":"call_R\nfc_4","tool_call":{"shellToolCall":{"args":{"command":"cat ../outside.txt"}}},"session_id":"cur-1"}
{"type":"tool_call","subtype":"completed","call_id":"call_R\nfc_4","tool_call":{"shellToolCall":{"result":{"spawnError":{"command":"","workingDirectory":"","error":"spawn /bin/zsh ENOENT"}}}},"session_id":"cur-1"}
{"type":"assistant","message":{"role":"assistant","content":[{"type":"text","text":"Fixed the typo; checks pass. curl was refused."}]},"session_id":"cur-1"}
{"type":"result","subtype":"success","duration_ms":3317,"is_error":false,"result":"I'll fix the typo first.Fixed the typo; checks pass. curl was refused.","session_id":"cur-1","usage":{"inputTokens":130,"outputTokens":17,"cacheReadTokens":17408,"cacheWriteTokens":0}}
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_transcript_viewer.mjs`:

```js
test('cursor drops thinking and folds each tool call into one row', () => {
  const { rows } = normalize(fixture('cursor-basic.jsonl'), 'cursor');
  assert.ok(rows.every(r => (r.raw || {}).type !== 'thinking'));
  const shells = rows.filter(r => r.kind === 'command');
  assert.equal(shells.length, 3);
  const check = shells.find(r => r.command === 'bash check.sh');
  assert.equal(check.outcome, 'success');
  assert.equal(check.exit_code, 0);
  assert.equal(check.output, 'checks ok');
});

test('a cursor call id with an embedded newline still pairs its halves', () => {
  const { rows } = normalize(fixture('cursor-basic.jsonl'), 'cursor');
  assert.equal(rows.filter(r => r.kind === 'error').length, 0);
  const edit = rows.find(r => r.tool === 'editToolCall');
  assert.equal(edit.kind, 'file_change');
  assert.deepEqual(edit.changes, [{ path: 'tracked.txt', kind: 'update' }]);
  assert.equal(edit.raw.call_id, 'call_E\nfc_1');
});

test('a rejected cursor call is a denial and a spawnError is a failure', () => {
  const { rows } = normalize(fixture('cursor-basic.jsonl'), 'cursor');
  const curl = rows.find(r => r.command === 'curl https://example.com');
  assert.equal(curl.outcome, 'rejected');
  assert.equal(curl.denied, true);
  assert.equal(curl.is_error, true);
  const spawn = rows.find(r => r.command === 'cat ../outside.txt');
  assert.equal(spawn.outcome, 'spawnError');
  assert.equal(spawn.denied, undefined);
  assert.match(spawn.output, /ENOENT/);
});

test('cursor messages are rows and init names the model that ran', () => {
  const { rows } = normalize(fixture('cursor-basic.jsonl'), 'cursor');
  assert.deepEqual(rows.filter(r => r.kind === 'agent').map(r => r.text),
    ["I'll fix the typo first.", 'Fixed the typo; checks pass. curl was refused.']);
  assert.equal(rows.find(r => r.kind === 'user').text, 'Fix the typo and run the checks.');
  const life = rows.filter(r => r.kind === 'lifecycle');
  assert.deepEqual(life.map(r => r.label), ['session start', 'result']);
  assert.equal(life[0].detail, 'GPT-5.4 Mini Low');
  assert.equal(life[1].usage.inputTokens, 130);
});

test('a dropped cursor log is inferred from tool_call or thinking, never from shared types', () => {
  const HVn = globalThis.window.HandoffViewer;
  const text = fixture('cursor-basic.jsonl');
  assert.equal(HVn.inferBackend(text), 'cursor');
  assert.deepEqual(normalize(text), normalize(text, 'cursor'));
  assert.equal(HVn.inferBackend('{"type":"thinking","subtype":"delta","text":"x"}'), 'cursor');
  // The stated limit: a log that crashed after init holds only shared types.
  const sharedOnly = text.split('\n')
    .filter(line => /^\{"type":"(system|user|assistant|result)"/.test(line)).join('\n');
  assert.equal(HVn.inferBackend(sharedOnly), null);
});
```

The existing baseline test (`codex and claude fixtures parse exactly as they did before the copilot branch`) is the R7.4 regression guard. Do not edit it or `baseline-rows.json`.

- [ ] **Step 3: Run the tests to see them fail**

Run: `node --test tests/test_transcript_viewer.mjs`
Expected: the five new tests FAIL (no `cursor` branch; `inferBackend` returns `null`); the 34 existing tests pass.

- [ ] **Step 4: Implement**

In the `viewer-normalize` block, after `mapCopilotTool`:

```js
  // The tool kind is the one key under tool_call holding an object.
  function cursorCall(event) {
    const call = event.tool_call && typeof event.tool_call === 'object' ? event.tool_call : {};
    const kind = Object.keys(call).find(k => call[k] && typeof call[k] === 'object') || '';
    return { kind, body: kind ? call[kind] : {} };
  }

  function mapCursorTool(kind, args) {
    const base = { tool: kind, args };
    if (kind === 'shellToolCall')
      return { ...base, kind: 'command', command: args.command || '', output: '' };
    if (kind === 'editToolCall')
      // Provisional, as in the other branches: a refused edit is not a change.
      return { ...base, kind: 'file_change', pending_edit: true,
               changes: args.path ? [{ path: args.path, kind: 'update' }] : [] };
    return { ...base, kind: 'tool' };
  }
```

`HV.inferBackend`, inside the loop after the codex check:

```js
      if (type === 'tool_call' || type === 'thinking') return 'cursor';
```

Update the comment above `inferBackend`: Cursor's `system`, `user`, `assistant`, and `result` collide with claude's, so only `tool_call` and `thinking` identify a cursor log.

`HV.normalize`, a branch right after the copilot branch's closing `}`:

```js
      // --- Cursor stream-json ---------------------------------------------
      // system, user, assistant, and result share claude's type names, so the
      // branch is chosen by the declared or inferred backend, never by them.
      if (format === 'cursor') {
        if (type === 'thinking') return;   // a reasoning stream, hundreds per job
        if (type === 'tool_call') {
          const { kind, body } = cursorCall(event);
          if (event.subtype === 'started') {
            const row = add({ pos: index, raw: event,
                              ...mapCursorTool(kind, (body && body.args) || {}) });
            // The id embeds a newline; as a map key that is harmless, and it is
            // only ever displayed through JSON.stringify and textContent.
            if (event.call_id) byToolCallId.set(event.call_id, row);
            return;
          }
          const target = byToolCallId.get(event.call_id);
          if (!target) {
            add({ kind: 'error', pos: index, raw: event,
                  text: 'tool result with no matching tool call' });
            return;
          }
          const result = (body && body.result) || {};
          const outcome = Object.keys(result)[0] || '';
          const detail = (outcome && result[outcome]) || {};
          target.outcome = outcome;
          target.is_error = outcome !== 'success';
          if (outcome === 'rejected') target.denied = true;   // typed, never pattern-matched
          if (typeof detail.exitCode === 'number') target.exit_code = detail.exitCode;
          target.output = outcome === 'success'
            ? [detail.stdout, detail.stderr].filter(Boolean).join('\n')
            : (detail.error || detail.reason || outcome);
          if (target.pending_edit && target.is_error) {
            target.kind = 'error';
            target.text = target.output;
          }
          delete target.pending_edit;
          return;
        }
        if (type === 'assistant' || type === 'user') {
          const blocks = (event.message && event.message.content) || [];
          const text = (Array.isArray(blocks) ? blocks : [])
            .filter(b => b && b.type === 'text').map(b => b.text || '').join('');
          return add({ kind: type === 'user' ? 'user' : 'agent', pos: index, text, raw: event });
        }
        if (type === 'system' && event.subtype === 'init')
          return add({ kind: 'lifecycle', pos: index, label: 'session start',
                       detail: event.model, raw: event });
        if (type === 'result')
          return add({ kind: 'lifecycle', pos: index, label: 'result',
                       usage: event.usage, raw: event });
        add({ kind: 'unknown', pos: index, type, raw: event });
        return;
      }
```

- [ ] **Step 5: Run the tests to see them pass**

Run: `node --test tests/test_transcript_viewer.mjs && python3 -m unittest tests.test_render_transcript`
Expected: 39 pass, 0 fail; the render tests pass unchanged.

- [ ] **Step 6: Commit**

```bash
git add assets/transcript-viewer.html tests/fixtures/transcript/cursor-basic.jsonl tests/test_transcript_viewer.mjs
git commit -m "feat: the transcript viewer renders cursor logs"
```

---

### Task 6: Skill prose, version, prompts, and evals

Requirements: R8.1, R8.2, R8.4, R8.5.

**Files:**
- Modify: `SKILL.md`, `README.md`, `CLAUDE.md`, `CHANGELOG.md`, `references/claude-driven.md`, `references/fable5-principles.md`, `references/setup.md`, `references/handoff-template.md`, `references/goal-template.md`, `references/e2e-gauntlet.md`, `references/tryout.md`, `docs/user-guide/agent-handoff.html`, `docs/user-guide/diagrams/evidence-flow.svg`, `test-prompts.json`, `evals/eval.yaml`
- Create: `docs/releases/v3.9.0.md`
- Rename: `evals/cases/receipt-contract-splits-three-backends.yaml` to `evals/cases/receipt-contract-splits-four-backends.yaml`

**Interfaces:**
- Consumes: the shipped behaviour of Tasks 1 to 5. Read their diffs first.
- Produces: nothing code consumes.

Rules for every edit in this task:
- A line that enumerates backends gains cursor. A line about one backend's mechanics (AWS Copilot's name collision, `--allow-all-tools`) stays as it is.
- Never spell Cursor's force flag with its two dashes in prose; say "Cursor's force mode". If a line must spell it, it carries `<!-- risk-ok: Cursor CLI flag name -->`. In `test-prompts.json`, only inside `must_not`.
- No link from `SKILL.md` or `references/` into `docs/specs/`.
- Plain words. Run the `declawed` scan on `CHANGELOG.md`'s new entry and on `docs/releases/v3.9.0.md` before committing.

- [ ] **Step 1: Version to 3.9.0**

`SKILL.md` frontmatter `version: 3.9.0`; `README.md:8` badge `3.9.0` (both the label and the shield URL); `docs/user-guide/agent-handoff.html:186` reads `@ v3.9.0 · updated 2026-09-30`.

Verify: `grep -rn "3\.8\.[12]" SKILL.md README.md docs/user-guide/agent-handoff.html` prints nothing.

- [ ] **Step 2: SKILL.md**

- Description: `(Codex, a second Claude Code, GitHub Copilot, or Cursor)`.
- Configuration paragraph (line 38): `claude, codex, copilot, or cursor`. Add one sentence: "A cursor identity's model is a whole Cursor catalogue slug, which carries the effort, so its effort is always `model`."
- Output Contract block: after the `copilot_job_durations:` line add `cursor_jobs: <0 | count>` and a `cursor_job_durations:` line in the copilot line's exact placeholder style; `receipt_schema_version: 7`.
- Line 203: partition "four ways"; "A copilot or cursor job is never folded into any other count."
- Line 209: "`receipt_schema_version` is always `7`; a receipt with no `cursor_jobs` and `cursor_job_durations`, or one carrying `direction` or `monitoring_level`, predates this contract".
- `grep -n -i copilot SKILL.md` and treat each remaining enumeration the same way.

- [ ] **Step 3: references/**

- `claude-driven.md`:
  - Line 5: "A claude-backed, copilot-backed, or cursor-backed identity".
  - Line 12, preflight: add "`cursor-agent --version` and `cursor-agent status` for any cursor-backed one. Never check `cursor`: that name belongs to the Cursor IDE launcher."
  - Line 34: "`--mode plan` on copilot and cursor", "any of the four backends".
  - Line 92, the posture paragraph: add "On cursor the posture changes nothing: default and allow-all both run in Cursor's force mode, and deny rules in the user's or project's `.cursor/cli.json` are the only narrowing. Handoff writes nothing there. Every writing cursor job prints the bypass warning. Read-only is `--mode plan`."
  - After that paragraph, add R8.4's two remaining limits: "Cursor's attribution setting adds a `Co-authored-by: Cursor <cursoragent@cursor.com>` trailer to worker commits, e2e worker commits included. It is the user's setting: report it, do not strip it." and "`(NO ZDR)` in a Cursor model's display name means that model has no zero data retention, and a delegated job sends repository contents to it."
  - Line 116, monitoring: "any of the four backends". Add: "On cursor, a model Cursor refuses fails at launch: `status` reads FAILED, `log.jsonl` is empty, and `stderr.log` holds Cursor's message and the models it accepts."
- `fable5-principles.md` lines 66, 74, 76: "four backends", "the Codex subscription, the Claude meter, Copilot's AI credits, or the Cursor meter", "identical on all four", "the same across all four backends", "on Codex, Copilot, or Cursor".
- `setup.md` line 16, add: "Cursor models come from `cursor-agent models`, read when the page opens, which is free: one option per `slug - Display name` line, `auto` dropped. When the CLI is missing or logged out the page offers no Cursor model and names `cursor-agent login` or `HANDOFF_CURSOR_BIN`. A cursor identity's effort is fixed at `model`. A configured slug that has left the catalogue stays selectable, marked. Smoke checks the slug against the catalogue before its one paid `--mode ask` run. `(NO ZDR)` in a display name means no zero data retention." The closing rule becomes "Never render one shared effort enum across the four CLIs."
- `handoff-template.md` line 45: "; on a cursor-backed one it becomes `--mode plan`, which never carries Cursor's force mode. The job, the jobId, and the receipt entry are the same on all four."
- `goal-template.md` lines 50, 62, 66: "four CLIs"; "`cursor` on the Cursor meter"; "any of the four vendors"; "any of the four backends".
- `e2e-gauntlet.md` lines 25, 52: "all four backends"; "codex, claude, copilot, and cursor"; "a claude-backed, copilot-backed, or cursor-backed row".
- `tryout.md` line 7: "codex, claude, copilot, or cursor".

- [ ] **Step 4: README.md, CLAUDE.md, user guide, diagram**

- `README.md`: line 109 alt text "on codex, claude, copilot, or cursor"; line 138 adds `cursor_jobs`; the receipt example near line 159 gains `cursor_jobs: 0` and `cursor_job_durations: none` after the copilot pair and reads `receipt_schema_version: 7`; line 235 "schema v7" and four counts; line 237 "any of the four backends' event formats"; line 261 "The four CLIs". Then `grep -n -i copilot README.md` for other enumerations (identity table, backend list).
- `CLAUDE.md`: "One flow" names `cursor`; line 52 "schema v7", four counts with `cursor_jobs`, "a copilot or cursor job is never folded into another count"; line 54 "four vendors (`claude`, `codex`, `copilot`, `cursor`)". In Runtime primitives, `delegate-codex.sh` wraps `cursor-agent -p --output-format stream-json` as well, and add: "On cursor, the binary is `cursor-agent` (never `cursor`, the IDE launcher), effort is always `model` because the slug carries it, both postures run in Cursor's force mode, and `--read-only` means `--mode plan`." `handoff-setup-ui.py`: "Cursor's models come from `cursor-agent models` at page load." `handoff_runtime.py`: "The copilot and cursor branches deliberately do not".
- `docs/user-guide/agent-handoff.html`: line 194 alt text; line 237 "four vendors"; line 258 adds "Cursor runs in its force mode under both, because no narrower Cursor mode let a probed worker run its checks; deny rules in `.cursor/cli.json` are the only narrowing."; line 514 alt text "the four job counts"; the receipt example near line 552 gains the cursor pair and version 7; line 561 "The four job counts". Then `grep -n -i copilot` in the file for the job primitive table and the cost-receipt section.
- `docs/user-guide/diagrams/evidence-flow.svg:75`: `codex_jobs · cc_jobs · copilot_jobs · cursor_jobs`, only if it fits inside its card at the same font size. Check the card's `rect` width against the label; if it does not fit, leave the label and list it under the release notes' known gaps. The diagrams that draw one chip per backend (`delegate-paths.svg`, `identities.svg`, `handoff-lifecycle.svg`) need new shapes, not text edits, so they stay out of scope and go in the same list.

Verify: `python3 -c "import xml.dom.minidom,glob;[xml.dom.minidom.parse(f) for f in glob.glob('docs/user-guide/diagrams/*.svg')]"`.

- [ ] **Step 5: CHANGELOG and release notes**

`CHANGELOG.md`, a new top entry:

```markdown
## v3.9.0 (2026-09-30)

### Cursor CLI is the fourth backend

- feat: `cursor` is a fourth `backend` value. A cursor-backed identity runs `cursor-agent -p --output-format stream-json` as the same durable job the other three get: jobId, job directory, monitor loop, bounded `resume`, worktree lifecycle, and receipt evidence.
- feat: a cursor identity's model is a whole catalogue slug (`claude-opus-5-5-high`, `composer-2.5`), and its effort is always `model`, because Cursor carries effort in the slug. Any other effort, and `model = "auto"`, are refused at setup and at submit.
- feat: both wizards read Cursor's model list from `cursor-agent models` when they open, which is free. Smoke checks the slug against that list before its one paid read-only run, because Cursor quietly runs a variant of a base name it does not list.
- **breaking**: receipt schema v7 adds `cursor_jobs` and `cursor_job_durations`. A v6 receipt no longer validates; regenerate it with `make-receipt.py`.
- feat: the cost receipt shows a cursor job's tokens, and its cost cell reads "n/a - no cost figure": Cursor reports no cost, so none is made up. The model column shows the model that ran, read from the job's `init` event.
- feat: the transcript viewer renders cursor logs. `thinking` events are dropped, each tool call is one row with its outcome, and a dropped log is recognised as cursor from its first `tool_call` or `thinking` event.
- note: on Cursor the permission posture changes nothing. `default` and `allow-all` both run in Cursor's force mode, because in probing no narrower mode let a worker run its own checks. Deny rules in `.cursor/cli.json` are the only narrowing, and every writing cursor job prints the bypass warning. `--read-only` runs `--mode plan`.
```

`docs/releases/v3.9.0.md`: follow the shape of `docs/releases/v3.8.1.md`. Cover what changed, the breaking receipt change and how to regenerate, the posture note, the three Cursor limits from R8.4, and a "Known gaps" list: the chip diagrams that still draw three backends, and the open questions this release did not close (mid-run API failure shape; running without `--trust`; the logged-out `-p` message if Task 8 could not provoke it).

- [ ] **Step 6: test-prompts.json and evals**

`test-prompts.json`:
- `receipt-splits-three-backend-counts` becomes `receipt-splits-four-backend-counts`. Prompt: "This run had jobs on Codex, on a second Claude Code, on Copilot, and on Cursor. Give me the receipt." `expected_behavior` passes `--cursor-jobs` too and emits `receipt_schema_version 7` with `cursor_jobs` and `cursor_job_durations` beside the three existing pairs. `must_not` adds "Fold cursor jobs into any other count.", "Emit a v6 receipt that omits cursor_jobs and cursor_job_durations.", and "Report a cost figure for a cursor job; Cursor reports none."
- `no-identity-substitution-across-three-backends` becomes `no-identity-substitution-across-four-backends`, and its expected text says "all four backends".
- New case `cursor-identity-delegates-on-its-own-backend`. Prompt: "fast_worker is cursor-backed. Hand task T2 off." `expected_behavior`: "Submit with delegate-codex.sh submit --role fast_worker and report the jobId.", "Check that meta records backend=cursor.", "Tell the user that on Cursor the permission posture changes nothing: the job auto-approves every tool call except what the user's own deny rules forbid.", "Monitor it with the same status and result loop as any other backend." `must_not`: "Pass --force to a --read-only cursor job.", "Resolve the worker from the `cursor` or `agent` binary.", "Move the task to another identity to avoid Cursor's meter." <!-- risk-ok: Cursor CLI flag name -->
- New case `cursor-effort-lives-in-the-slug`. Prompt: "Set deep_reasoner to Cursor with Claude Opus 5.5 at high effort." `expected_behavior`: "Name the catalogue slug that carries the effort, such as claude-opus-5-5-high, as the model.", "Set effort to model.", "Check the slug against cursor-agent models, or run smoke, rather than typing a base name." `must_not`: "Set effort = high on a cursor identity.", "Set model = auto."

Rename the eval case with `git mv evals/cases/receipt-contract-splits-three-backends.yaml evals/cases/receipt-contract-splits-four-backends.yaml`. In it: `id` matches the new file name; the title says schema v7 and four job counts; the description says four ways and "always 7"; the prompt adds "and on Cursor"; `must_contain` and the success `all` list add `"cursor_jobs"`; the failure `any` list adds `'(?m)^\s*receipt_schema_version\s*:\s*6\b'` and keeps the `5` pattern (explanatory prose about obsolete fields stays allowed because the patterns anchor on a field assignment at line start); the success version pattern becomes `'(?i)schema (version )?7'`. `evals/eval.yaml:33` points at the new path. `grep -rn "three" evals/` for any other stale count.

- [ ] **Step 7: Verify**

Run:

```bash
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
bash scripts/check-skill-repo.sh . | grep -v skillgantry-workspace | grep -E '^\./'
grep -rn -i -E "three backends|three CLIs|three vendors|three job counts|schema v6|receipt_schema_version: 6" SKILL.md README.md CLAUDE.md references/ docs/user-guide/agent-handoff.html
```

Expected: `SUMMARY fail=0 warn=1`; no listed warning line comes from a file this task changed; `PASS static checks`; the last grep prints nothing.

- [ ] **Step 8: Commit**

```bash
git add -A SKILL.md README.md CLAUDE.md CHANGELOG.md docs/releases/v3.9.0.md references docs/user-guide test-prompts.json evals
git commit -m "docs: Agent Handoff 3.9.0 -- Cursor CLI as the fourth backend"
```

---

### Task 7: Design-of-record docs

Requirements: R8.3.

**Files:**
- Modify: `docs/specs/design_agent-handoff.md`, `docs/specs/design_agent-identities-and-config.md`, `docs/specs/design_agent-handoff-evidence.md`, `docs/specs/design_session-visualisation.md`, `docs/specs/index.md`, `docs/specs/requirements_cursor-cli-backend.md` (status only)

**Interfaces:**
- Consumes: the shipped diffs of Tasks 1 to 5, and `design_cursor-cli-backend.md`.
- Produces: nothing code consumes.

- [ ] **Step 1: Update each design doc to what shipped**

- `design_agent-handoff.md`: add a cursor row to the permission table (default and allow-all both `--force`, read-only `--mode plan`, and the note that the posture is a no-op); add the five rows from the cursor design's "Where it fails closed" table to the fail-closed table; add the cursor risks (a `default` as loose as `allow-all`, the one-machine sandbox finding, a slug rejected after the job directory exists, commit attribution, `(NO ZDR)`). Every line that spells the flag carries `<!-- risk-ok: Cursor CLI flag name -->`. <!-- risk-ok: Cursor CLI flag name -->
- `design_agent-identities-and-config.md`: the backend enum gains `cursor`; the effort table gains the cursor row `model`, meaning "set by the model id"; `auto` is refused on copilot and cursor; `schema_version` stays 2.
- `design_agent-handoff-evidence.md`: the backend telemetry table and the normalization table gain cursor rows (session id from line one, last `assistant` event as the message, `shellToolCall` commands, typed `rejected` denials, the last `result.usage` with four camelCase counters, no reasoning count, no cost, the `init` display name); the receipt contract moves to v7 with four pairs; the missing-data rules say a cursor job with no `result` has unknown usage; the meter descriptions name the Cursor meter as tokens without a cost figure.
- `design_session-visualisation.md`: denial extraction gains the cursor branch (`cursor_rejections` from the cost module) and an unknown backend reads "not reported (<backend>)"; the verification inventory lists the new session-view test.

- [ ] **Step 2: Index and status**

`docs/specs/index.md`: the cursor design row's coverage reads "1 requirements doc, 1 plan"; the requirements row's status reads `implemented`; a plan row `[plan_cursor-cli-backend.md](plan_cursor-cli-backend.md) | Cursor CLI as fourth backend | design_cursor-cli-backend | v3.9.0 | implemented`. In `requirements_cursor-cli-backend.md` change nothing but a status line, if the file carries one.

- [ ] **Step 3: Verify and commit**

Run: `bash scripts/check-skill-repo.sh . | grep -v skillgantry-workspace | grep -E '^\./'`
Expected: no listed line comes from a file this task changed.

```bash
git add docs/specs
git commit -m "specs: Update the design-of-record docs for the Cursor backend"
```

---

### Task 8: Live end-to-end verification

Requirements: R9.1, R9.3, and the open questions. Driver-run: it spends requests on the user's Cursor meter, and the evidence is what the driver reviews.

**Files:**
- Modify: this plan (fill in *Verification record* below), `docs/research/cursor-cli-specification.md` (probe results, as `[probed]` or `[open]` lines)

- [ ] **Step 1: CI parity**

Run everything `.github/workflows/checks.yml` runs:

```bash
bash scripts/check-skill-repo.sh .
bash -n install.sh && bash -n scripts/check-skill-repo.sh && bash -n scripts/delegate-codex.sh
python3 -m py_compile scripts/*.py
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
python3 scripts/run-test-prompts.py
python3 scripts/make-receipt.py --start --repo .
python3 scripts/make-receipt.py --repo . --phase review --claude-session ci-test \
  --checks "ci roundtrip" --codex-jobs 0 \
  --scope project --config-source project --roles-used '[]' --no-save \
  | python3 scripts/validate-receipt.py -
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
bash install.sh --dry-run
```

Expected: every command exits 0.

- [ ] **Step 2: Install and record the revision**

```bash
bash install.sh
bash install.sh --status      # installed revision must equal `git rev-parse HEAD`
H="$HOME/.claude/skills/agent-handoff/scripts"
```

- [ ] **Step 3: Scratch repo and identity**

```bash
S="$(mktemp -d)/repo"; mkdir -p "$S"; cd "$S"
git init -q -b main
printf 'helo world\n' > greeting.txt
printf '#!/usr/bin/env bash\ngrep -qx "hello world" greeting.txt && echo CHECK_OK || { echo CHECK_FAILED; exit 1; }\n' > check.sh
printf '.handoff/\n' > .gitignore
git add -A && git commit -qm init
python3 "$H/handoff-config.py" --repo "$S" init
python3 "$H/handoff-config.py" --repo "$S" set --role fast_worker --backend cursor --model gpt-5.4-mini-low --effort model
python3 "$H/handoff-setup.py" --status --repo "$S"
```

Pick a slug that `cursor-agent models` lists; `gpt-5.4-mini-low` is the one the probes used. Before running smoke, read `--status`: smoke checks every configured identity, including ones merged in from `~/.config/handoff/config.toml`, and a claude-backed one spends a Claude request. Set the other core identities in the scratch config to codex if that is not wanted. Then:

```bash
python3 "$H/handoff-setup.py" --smoke --repo "$S"      # expect fast_worker: PASS
python3 "$H/make-receipt.py" --start --repo "$S"
```

- [ ] **Step 4: Jobs**

Write three packets outside the repo:
- `edit.md`: "Fix the typo in greeting.txt so the file reads exactly `hello world`. Then run `bash check.sh` and quote its output. Do not commit."
- `question.md`: "What exact text does greeting.txt contain now? Answer in one line."
- `notes.md`: "Create NOTES.md containing the single line `worktree ok`, then commit it with the message `notes`."
- `write.md`: "Create a file named should-not-exist.txt containing the letter x."

```bash
D="$H/delegate-codex.sh"
J=$(bash "$D" submit --repo "$S" --prompt-file edit.md --label e2e-edit --role fast_worker)
bash "$D" status "$J" --repo "$S" --wait --timeout 600; bash "$D" result "$J" --repo "$S"
R=$(bash "$D" resume "$J" --repo "$S" --prompt-file question.md)
bash "$D" status "$R" --repo "$S" --wait --timeout 600; bash "$D" result "$R" --repo "$S"
W=$(bash "$D" submit --repo "$S" --prompt-file notes.md --label e2e-wt --role fast_worker --worktree e2e/cursor-wt)
bash "$D" status "$W" --repo "$S" --wait --timeout 600
O=$(bash "$D" submit --repo "$S" --prompt-file write.md --label e2e-ro --role fast_worker --read-only)
bash "$D" status "$O" --repo "$S" --wait --timeout 600; bash "$D" result "$O" --repo "$S"
```

Confirm each item on disk, not from the worker's report:
- `greeting.txt` reads `hello world`.
- `log.jsonl` of `$J` holds a `shellToolCall` for `bash check.sh` whose completed result's stdout contains `CHECK_OK`.
- `meta` of `$J` records `backend=cursor`, `effort=model`, `permission_mode=force`; the submit printed the bypass warning naming `.cursor/cli.json`.
- `$R` resumed the same session: its `session_id` file equals `$J`'s, and its `init` event carries the same `session_id`. Its `result.usage.inputTokens` is its own invocation's count, not a running total.
- `$W`'s `meta` has a 40-character `base_commit`; branch `e2e/cursor-wt` has the `notes` commit (record whether it carries a `Co-authored-by: Cursor` trailer); `bash "$D" cleanup "$W" --repo "$S"` removes the worktree.
- `$O` runs `--mode plan` with no force flag in `run.sh`; `should-not-exist.txt` does not exist and `git -C "$S" status --porcelain` is empty.

- [ ] **Step 5: Receipt and cost receipt**

```bash
python3 "$H/make-receipt.py" --repo "$S" --phase "delegated implementation" --claude-session none \
  --checks "cursor e2e" --codex-jobs 0 --cc-jobs 0 --copilot-jobs 0 --cursor-jobs 4 \
  --scope project --config-source project \
  --roles-used '[{"role":"fast_worker","host":"cursor","model":"gpt-5.4-mini-low","effort":"model","verified":true}]'
python3 "$H/render-cost-receipt.py" last --repo "$S" --no-open
python3 "$H/render-transcript.py" "$J" --repo "$S" --no-open
```

`make-receipt.py` refuses a count its job directories do not support, so exit 0 proves four cursor jobs. The cost receipt's cursor rows show tokens, the display name from `init`, and "n/a - no cost figure"; `$J` and `$R` are two rows whose input tokens sum. Open the transcript page once and check that thinking is absent and tool calls read as one row each. (Check `render-transcript.py --help` for its exact arguments first.)

- [ ] **Step 6: Open questions, cheap probes only**

- Resume of a session that never existed: `cursor-agent -p "Reply ok" --output-format stream-json --trust --mode ask --resume "$(python3 -c 'import uuid;print(uuid.uuid4())')"` from `$S`. Record exit code, stderr, and whether it started a new session under that id.
- A `-p` run while logged out, and a mid-run API failure: do not log the user out or exhaust a quota to provoke them. Record each as `[open]` unless it happens on its own.
- Write each result into `docs/research/cursor-cli-specification.md` with its provenance tag and the date.

- [ ] **Step 7: Record and commit**

Fill in *Verification record* below with the installed revision, the helper paths, the four jobIds, and one line of evidence per R9.3 item. Commit:

```bash
git add docs/specs/plan_cursor-cli-backend.md docs/research/cursor-cli-specification.md
git commit -m "specs: Record the Cursor backend's live end-to-end verification"
```

---

## Verification record

Filled in by Task 8.

| R9.3 item | Evidence |
|---|---|
| Installed revision and helper paths | |
| One expected edit on disk | |
| A named repo check ran, output in `log.jsonl` | |
| `meta` records `backend=cursor` | |
| `resume` on the same session, usage counted once | |
| `--worktree` job pins its base and cleans up | |
| `--read-only` job leaves the tree unchanged | |
| Receipt counts the jobs under `cursor_jobs` | |
| Cost receipt shows tokens | |

## Not doing

Per the requirements' *Out of scope*: no Handoff writes to `.cursor/cli.json`, no `--sandbox` flag either way, no `create-chat` pre-assignment, no bracket effort form, no catalogue check at submit, no `--version` identity check, no Cursor preset, no cost figure, no stripping of Cursor's commit trailer, and no shared event-parsing module. Also not doing: hardening the codex catch-alls in `delegate-codex.sh`, and redrawing the chip diagrams.

## Follow-ups

- One event-normalization module for the Python parsers, as its own release with no behaviour change.
- A sandbox-based cursor `default`, if a Cursor build runs shells under `--sandbox enabled`.
- Make the bash catch-alls in `delegate-codex.sh` die on an unknown backend instead of defaulting to codex.
- Redraw `delegate-paths.svg`, `identities.svg`, and `handoff-lifecycle.svg` with a fourth backend chip.
