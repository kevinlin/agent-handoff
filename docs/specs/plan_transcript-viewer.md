# Transcript Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Render a delegated job's `log.jsonl` as one self-contained HTML page, opened by `/agent-handoff transcript [<pid|jobId|folder>]`, and readable by dropping a log file onto the same page.

**Architecture:** Two artifacts. `assets/transcript-viewer.html` is the whole product — vendored marked, the normalizer, the renderer, a payload slot and a dropzone. `scripts/render-transcript.py` is thin: it resolves an identifier to a job directory, reads that job's files, substitutes the payload slot, writes to `<repo>/.handoff/transcripts/<jobId>.html`, and opens it. All log parsing lives in the page's JavaScript so the drop path needs no second parser.

**Tech Stack:** Python 3 stdlib only (argparse, json, pathlib, subprocess, webbrowser, unittest). Vendored marked v15.0.12. Node for tests only — the normalizer and escaping helpers are pure string functions, so they test without a DOM.

**Spec:** `docs/specs/design_transcript-viewer.md`

## Global Constraints

- **No runtime dependencies.** The HTML must work opened from `file://` with no network. Every asset is inlined.
- **marked is pinned to v15.0.12**, sha256 `3e7e7d7feb3e5d58cb6c804f68ab5c24cc7e5eb6270fd6e5cbb9124739217d0c`, 39903 bytes, MIT. Vendor it byte-for-byte; do not upgrade it inside this plan.
- **The repo is English-only.** `scripts/english-only-scan.py` fails on CJK in any tracked file, including UI strings.
- **Untrusted input rule.** Every string in `log.jsonl` reaches the DOM through `textContent`, except agent and reasoning message text, which goes through marked with the three overrides from Task 5. Never `innerHTML` on log content.
- **Adding a top-level file means updating three places:** the file, the README File Map, and `scripts/check-skill-repo.sh`'s required-file list.
- **Version bump touches four places together:** `SKILL.md` frontmatter, the README badge, `CHANGELOG.md`, `docs/releases/`.
- **Hyphenated script names are not importable.** Tests load scripts with `importlib.util.spec_from_file_location`, following `tests/test_goal_sync.py`.
- **`main(argv)` returns an exit status**; it does not call `sys.exit` internally. Follows `scripts/goal-sync.py`.

## File Structure

| File | Responsibility |
| --- | --- |
| `scripts/render-transcript.py` | Resolve identifier → job dir; derive state; build payload; write and open the page. CLI only, no parsing of log content. |
| `assets/transcript-viewer.html` | Vendored marked, normalizer, escaping boundary, renderer, header, dropzone. The product. |
| `tests/test_render_transcript.py` | Python half: resolution, collisions, state derivation, payload escaping. |
| `tests/test_transcript_viewer.mjs` | JS half: normalizer correlation and malformed input; renderer output allowlist. Extracts the script blocks from the HTML — no build step, no second copy. |
| `tests/fixtures/transcript/` | Small crafted logs: codex pairs, malformed lines, claude tool use, XSS payloads. |

---

### Task 1: Job discovery, identifier resolution, and state derivation

Pure functions over a `.handoff/jobs/` tree. No HTML, no output file.

**Files:**
- Create: `scripts/render-transcript.py`
- Test: `tests/test_render_transcript.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `read_meta(job_dir: Path) -> dict[str, str]` — parses `key=value` lines; missing file returns `{}`.
  - `job_state(job_dir: Path) -> str` — one of `CANCELLED`, `DONE`, `FAILED`, `RUNNING`.
  - `list_jobs(repo: Path) -> list[Path]`
  - `resolve(repo: Path, argument: str | None) -> Path` — raises `Ambiguous(candidates)` or `NotFound(message)`.
  - Exceptions `Ambiguous(Exception)` with `.candidates: list[Path]`, and `NotFound(Exception)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_render_transcript.py
from __future__ import annotations

import importlib.util
import os
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
                 pid=None, cancelled=False):
        job = self.jobs / name
        job.mkdir()
        (job / "log.jsonl").write_text(log, encoding="utf-8")
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


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_render_transcript -v`
Expected: collection error — `render-transcript.py` does not exist.

- [ ] **Step 3: Write the implementation**

```python
#!/usr/bin/env python3
"""Render a delegated job's log.jsonl as one self-contained HTML page.

The page in assets/transcript-viewer.html is the whole viewer; this script
only locates a job, reads its state files, and injects a payload. All log
parsing lives in the page's JavaScript so that dropping a log file onto the
same page needs no second parser in another language.
"""

from __future__ import annotations

import os
from pathlib import Path

RESERVED_LAST = "last"


class Ambiguous(Exception):
    def __init__(self, candidates: list[Path]):
        super().__init__("more than one job matches")
        self.candidates = candidates


class NotFound(Exception):
    pass


def read_meta(job_dir: Path) -> dict[str, str]:
    """Parse a job's `key=value` meta file. A missing file is not an error:
    resumed jobs legitimately omit backend and role."""
    meta: dict[str, str] = {}
    path = job_dir / "meta"
    if not path.is_file():
        return meta
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            meta[key.strip()] = value.strip()
    return meta


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ProcessLookupError, PermissionError) as error:
        return isinstance(error, PermissionError)
    return True


def job_state(job_dir: Path) -> str:
    """Mirror delegate-codex.sh job_state(). Absence of exit_code is never
    RUNNING on its own: a worker that died without writing one is FAILED."""
    if (job_dir / "cancelled").is_file():
        return "CANCELLED"
    exit_code = job_dir / "exit_code"
    if exit_code.is_file():
        return "DONE" if exit_code.read_text(encoding="utf-8").strip() == "0" else "FAILED"
    pid_file = job_dir / "pid"
    if pid_file.is_file():
        raw = pid_file.read_text(encoding="utf-8").strip()
        if raw.isdigit() and _pid_alive(int(raw)):
            return "RUNNING"
    return "FAILED"


def list_jobs(repo: Path) -> list[Path]:
    root = repo / ".handoff" / "jobs"
    if not root.is_dir():
        return []
    return sorted(p for p in root.iterdir() if p.is_dir())


def _sort_key(job: Path) -> tuple[str, float, str]:
    # submitted_at has one-second precision, so ties break on mtime then name.
    return (read_meta(job).get("submitted_at", ""), job.stat().st_mtime, job.name)


def resolve(repo: Path, argument: str | None) -> Path:
    """Resolve an identifier to a job directory. Order matters: the reserved
    `last` selector is checked before label matching so a job labelled `last`
    cannot intercept it."""
    jobs = list_jobs(repo)

    if argument and ("/" in argument or Path(argument).is_dir()):
        candidate = Path(argument).expanduser()
        if not candidate.is_dir():
            raise NotFound(f"not a directory: {argument}")
        return candidate

    if not jobs:
        raise NotFound(f"no jobs under {repo / '.handoff' / 'jobs'}")

    if argument is None or argument == RESERVED_LAST:
        return max(jobs, key=_sort_key)

    exact = [j for j in jobs if j.name == argument]
    if exact:
        return exact[0]

    by_pid: list[Path] = []
    if argument.isdigit():
        for job in jobs:
            pid_file = job / "pid"
            if pid_file.is_file() and pid_file.read_text(encoding="utf-8").strip() == argument:
                by_pid.append(job)

    by_suffix = [j for j in jobs if j.name.endswith(argument) or argument in j.name]

    # A digit argument matching both a pid and a label is a real collision;
    # resolving it by step order is exactly the silent-wrong-job case.
    combined = list(dict.fromkeys(by_pid + by_suffix))
    if len(combined) > 1:
        raise Ambiguous(sorted(combined))
    if combined:
        return combined[0]
    raise NotFound(f"no job matches {argument!r}")
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_render_transcript -v`
Expected: PASS, 18 tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/render-transcript.py tests/test_render_transcript.py
git commit -m "feat: job resolution and state derivation for the transcript viewer"
```

---

### Task 2: Payload build, template injection, and the CLI

**Files:**
- Modify: `scripts/render-transcript.py` (append to Task 1's module)
- Modify: `tests/test_render_transcript.py` (append test classes)

**Interfaces:**
- Consumes: `read_meta`, `job_state`, `resolve`, `Ambiguous`, `NotFound` from Task 1.
- Produces:
  - `build_payload(job_dir: Path) -> dict` — the object injected into the page.
  - `inject(template: str, payload: dict) -> str`
  - `PAYLOAD_PATTERN: re.Pattern` — matches the slot element in the template.
  - `main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# append to tests/test_render_transcript.py, above `if __name__`
# Add `json` and `re` to the import block at the top of the file.
import json
import re


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
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python3 -m unittest tests.test_render_transcript -v`
Expected: FAIL — `module 'render_transcript' has no attribute 'build_payload'`.

- [ ] **Step 3: Write the implementation**

```python
# append to scripts/render-transcript.py
# Add these to the import block at the top of the file.

import argparse
import json
import re
import subprocess
import sys
import webbrowser
from datetime import datetime, timezone

DEFAULT_TEMPLATE = Path(__file__).resolve().parents[1] / "assets" / "transcript-viewer.html"
PAYLOAD_PATTERN = re.compile(
    r'(<script id="handoff-payload" type="application/json">)(.*?)(</script>)', re.S)


def _iso(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, timezone.utc).isoformat(timespec="seconds")


def build_payload(job_dir: Path) -> dict:
    meta = read_meta(job_dir)
    log = job_dir / "log.jsonl"
    exit_code_file = job_dir / "exit_code"
    return {
        "job_id": job_dir.name,
        "meta": meta,
        "log_text": log.read_text(encoding="utf-8", errors="replace") if log.is_file() else "",
        "state": job_state(job_dir),
        "exit_code": exit_code_file.read_text(encoding="utf-8").strip()
        if exit_code_file.is_file() else None,
        "exit_code_mtime": _iso(exit_code_file.stat().st_mtime)
        if exit_code_file.is_file() else None,
        "log_mtime": _iso(log.stat().st_mtime) if log.is_file() else None,
        "generated_at": _iso(datetime.now(timezone.utc).timestamp()),
        "parent_job_id": meta.get("parent"),
    }


def inject(template: str, payload: dict) -> str:
    """Replace the payload slot. Escaping `<` as \\u003c keeps any closing-script
    sequence inside captured output from ending the element early. This guards
    the element boundary only; DOM insertion is the page's own boundary."""
    if not PAYLOAD_PATTERN.search(template):
        raise ValueError("template has no handoff-payload slot")
    encoded = json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c")
    # A lambda, not a replacement string: re.sub interprets backslashes in a
    # replacement, and the encoded payload is full of them.
    return PAYLOAD_PATTERN.sub(
        lambda m: m.group(1) + encoded + m.group(3), template, count=1)


def _is_ignored(path: Path) -> bool:
    try:
        done = subprocess.run(["git", "check-ignore", "-q", str(path)],
                              cwd=path.parent, capture_output=True)
    except OSError:
        return False
    return done.returncode == 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("job", nargs="?", default=None,
                        help="jobId, job folder path, pid, remembered label, or 'last'.")
    parser.add_argument("--repo", type=Path, default=Path.cwd(),
                        help="Repository root (default: current directory).")
    parser.add_argument("--template", type=Path, default=DEFAULT_TEMPLATE,
                        help="Viewer template (default: the skill's assets copy).")
    parser.add_argument("--no-open", action="store_true",
                        help="Write the page and print its path instead of opening it.")
    args = parser.parse_args(argv)

    try:
        job_dir = resolve(args.repo, args.job)
    except Ambiguous as error:
        print("more than one job matches; name one of these exactly:", file=sys.stderr)
        for candidate in error.candidates:
            print(f"  {candidate.name}", file=sys.stderr)
        return 2
    except NotFound as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 2

    try:
        template = args.template.read_text(encoding="utf-8")
    except OSError as error:
        print(f"ERROR: cannot read template: {error}", file=sys.stderr)
        return 2

    out_dir = args.repo / ".handoff" / "transcripts"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{job_dir.name}.html"
    payload = build_payload(job_dir)
    payload["output_is_ignored"] = _is_ignored(out_path)
    out_path.write_text(inject(template, payload), encoding="utf-8")

    if not payload["output_is_ignored"]:
        print(f"WARNING: {out_path} is not ignored by Git. A transcript embeds "
              f"captured command output; do not commit it.", file=sys.stderr)

    print(out_path)
    if not args.no_open:
        webbrowser.open(out_path.as_uri())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_render_transcript -v`
Expected: PASS, 28 tests.

- [ ] **Step 5: Commit**

```bash
git add scripts/render-transcript.py tests/test_render_transcript.py
git commit -m "feat: payload injection and CLI for the transcript viewer"
```

---

### Task 3: The page shell with vendored marked

**Files:**
- Create: `assets/transcript-viewer.html`

**Interfaces:**
- Produces: a page defining `window.HandoffViewer = { normalize, renderMarkdown, escapeHtml, safeHref }` (filled in by Tasks 4–6), a `#handoff-payload` slot, `#dropzone`, `#header`, `#stream`.

- [ ] **Step 1: Vendor marked**

```bash
mkdir -p assets
curl -fsSL https://cdn.jsdelivr.net/npm/marked@15.0.12/marked.min.js -o /tmp/marked.min.js
shasum -a 256 /tmp/marked.min.js
```

Expected: `3e7e7d7feb3e5d58cb6c804f68ab5c24cc7e5eb6270fd6e5cbb9124739217d0c`.
If the hash differs, stop — do not vendor an unverified build.

- [ ] **Step 2: Confirm it passes this repo's three gates before inlining**

```bash
python3 - <<'PY'
import re
text = open('/tmp/marked.min.js', encoding='utf-8').read()
cjk = re.compile("[\u3000-\u303f\u3040-\u30ff\u4e00-\u9fff\uff00-\uffef]")
RISKY = r'git reset --hard|rm -rf|force push|--force'  # risk-ok: detection pattern
print("CJK hits:", len(cjk.findall(text)))
print("risky:", len(re.findall(RISKY, text)))  # risk-ok: detection pattern
print("secretish:", len(re.findall(r'gho_|ghp_|sk-[A-Za-z0-9_-]{20,}|AKIA[0-9A-Z]{16}', text)))
PY
```

Expected: all three zero.

- [ ] **Step 3: Write the shell**

Create `assets/transcript-viewer.html` with this structure. Inline the vendored
file verbatim where marked. The page must define its own `<title>` and styles;
it is opened directly from `file://`, so nothing loads over the network.

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Handoff Transcript</title>
<style>
  :root {
    color-scheme: light dark;
    --bg: #fbfbfa; --fg: #1a1a19; --muted: #6b6b68; --line: #e3e3e0;
    --card: #ffffff; --accent: #4a5cd6; --ok: #2f7d4f; --bad: #b3412e;
  }
  @media (prefers-color-scheme: dark) {
    :root { --bg:#16161a; --fg:#e8e8e6; --muted:#9a9a96; --line:#2c2c31;
            --card:#1e1e23; --accent:#8b9aff; --ok:#5fbe84; --bad:#e2705a; }
  }
  * { box-sizing: border-box; }
  body { margin:0; background:var(--bg); color:var(--fg); font:14px/1.6
    ui-sans-serif, -apple-system, "Segoe UI", Roboto, sans-serif; }
  main { max-width: 60rem; margin: 0 auto; padding: 1.5rem 1rem 6rem; }
  #header { border:1px solid var(--line); border-radius:.5rem;
    background:var(--card); padding:.9rem 1rem; margin-bottom:1.25rem; }
  #header dl { display:grid; grid-template-columns:auto 1fr; gap:.15rem .75rem;
    margin:0; font-size:.85rem; }
  #header dt { color:var(--muted); }
  #header dd { margin:0; font-variant-numeric:tabular-nums; }
  .row { border-bottom:1px solid var(--line); padding:.35rem 0; }
  .row summary { cursor:pointer; display:flex; gap:.6rem; align-items:baseline;
    font-family:ui-monospace, SFMono-Regular, Menlo, monospace; font-size:.82rem; }
  .row .glyph { flex:none; width:1.2rem; text-align:center; }
  .row .label { flex:1; overflow:hidden; text-overflow:ellipsis;
    white-space:nowrap; }
  .row .size { flex:none; color:var(--muted); font-size:.78rem; }
  .row pre { overflow-x:auto; background:var(--card); border:1px solid var(--line);
    border-radius:.4rem; padding:.6rem .75rem; font-size:.8rem; margin:.5rem 0 .25rem; }
  .agent { padding:1rem 0 1.25rem; border-bottom:1px solid var(--line); }
  .agent :is(h1,h2,h3) { font-size:1.05rem; margin:1rem 0 .4rem; }
  .agent pre { overflow-x:auto; background:var(--card); border:1px solid var(--line);
    border-radius:.4rem; padding:.6rem .75rem; }
  .agent code { font-size:.85em; }
  .muted { color:var(--muted); font-style:italic; }
  .bad { color:var(--bad); }
  .ok { color:var(--ok); }
  #dropzone { border:2px dashed var(--line); border-radius:.6rem;
    padding:3rem 1rem; text-align:center; color:var(--muted); }
  #dropzone.over { border-color:var(--accent); color:var(--fg); }
  #controls { display:flex; gap:1rem; align-items:center; margin-bottom:.75rem;
    font-size:.85rem; }
</style>
</head>
<body>
<main>
  <div id="header" hidden></div>
  <div id="controls" hidden>
    <label><input type="checkbox" id="only-agent"> Conversation only</label>
    <span id="counts" class="muted"></span>
  </div>
  <div id="stream"></div>
  <div id="dropzone">Drop a <code>log.jsonl</code> here to read it.</div>
</main>

<script id="handoff-payload" type="application/json">null</script>

<script id="vendor-marked">
/* marked v15.0.12 — MIT — https://github.com/markedjs/marked
   Vendored verbatim; sha256 3e7e7d7feb3e5d58cb6c804f68ab5c24cc7e5eb6270fd6e5cbb9124739217d0c */
MARKED_PLACEHOLDER
</script>

<script id="viewer-normalize">
/* Task 4 fills this in. */
</script>

<script id="viewer-render">
/* Tasks 5 and 6 fill this in. */
</script>

<script id="viewer-boot">
(function () {
  const slot = document.getElementById('handoff-payload');
  let payload = null;
  try { payload = JSON.parse(slot.textContent); } catch (_) { payload = null; }
  if (payload) window.HandoffViewer.mount(payload);

  const zone = document.getElementById('dropzone');
  const stop = (e) => { e.preventDefault(); e.stopPropagation(); };
  ['dragenter', 'dragover'].forEach(n => document.addEventListener(n, (e) => {
    stop(e); zone.classList.add('over');
  }));
  ['dragleave', 'drop'].forEach(n => document.addEventListener(n, (e) => {
    stop(e); zone.classList.remove('over');
  }));
  document.addEventListener('drop', async (e) => {
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!file) return;
    // A dropped log carries no meta, so mount with an empty job record rather
    // than merging into whatever job the page was generated for.
    window.HandoffViewer.mount({
      job_id: file.name, meta: {}, log_text: await file.text(),
      state: null, exit_code: null, exit_code_mtime: null,
      log_mtime: null, generated_at: null, parent_job_id: null
    });
  });
})();
</script>
</body>
</html>
```

- [ ] **Step 4: Splice the vendored library into the shell**

Write the shell with the literal token `MARKED_PLACEHOLDER` alone on a line
inside `<script id="vendor-marked">`, then replace it with the verified build:

```bash
python3 - <<'EOF'
import hashlib, pathlib
lib = pathlib.Path('/tmp/marked.min.js').read_text(encoding='utf-8')
digest = hashlib.sha256(lib.encode('utf-8')).hexdigest()
expected = '3e7e7d7feb3e5d58cb6c804f68ab5c24cc7e5eb6270fd6e5cbb9124739217d0c'
assert digest == expected, f'refusing to vendor an unverified build: {digest}'
page = pathlib.Path('assets/transcript-viewer.html')
html = page.read_text(encoding='utf-8')
assert 'MARKED_PLACEHOLDER' in html, 'placeholder already replaced'
page.write_text(html.replace('MARKED_PLACEHOLDER', lib), encoding='utf-8')
print('vendored', len(lib), 'bytes')
EOF
```

Expected: `vendored 39903 bytes`. The token must not survive —
`grep -c MARKED_PLACEHOLDER assets/transcript-viewer.html` returns 0.

- [ ] **Step 5: Verify the shell loads and marked is live**

```bash
python3 -c "
import re,pathlib
html = pathlib.Path('assets/transcript-viewer.html').read_text(encoding='utf-8')
assert 'handoff-payload' in html
assert 'marked' in html
print('bytes:', len(html))
"
open assets/transcript-viewer.html
```

Expected: the dropzone renders; the browser console reports no errors.
The page will not do anything on drop yet — `HandoffViewer` arrives in Task 4.

- [ ] **Step 6: Commit**

```bash
git add assets/transcript-viewer.html
git commit -m "feat: transcript viewer page shell with vendored marked v15.0.12"
```

---

### Task 4: The normalizer

Turns either log format into one ordered row list. This is the highest-risk
logic in the feature: a mistake here makes the viewer lie about what happened.
It is a pure string-to-array function, so it tests in node with no DOM.

**Files:**
- Modify: `assets/transcript-viewer.html` (`#viewer-normalize` block)
- Create: `tests/test_transcript_viewer.mjs`
- Create: `tests/fixtures/transcript/codex-basic.jsonl`, `tests/fixtures/transcript/claude-tools.jsonl`

**Interfaces:**
- Produces: `window.HandoffViewer.normalize(logText) -> { rows, partial }` where each
  row has `{ kind, pos, raw }` plus kind-specific fields. `kind` is one of
  `agent`, `reasoning`, `command`, `tool`, `file_change`, `mcp`, `web_search`,
  `error`, `lifecycle`, `unparsed`, `unknown`.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/transcript/codex-basic.jsonl` — note the deliberate bad
interior line and truncated final line:

```
{"type":"thread.started","thread_id":"th-1"}
{"type":"turn.started"}
{"type":"item.started","item":{"id":"i1","type":"command_execution","command":"ls","status":"in_progress"}}
NOT JSON AT ALL
{"type":"item.completed","item":{"id":"i1","type":"command_execution","command":"ls","aggregated_output":"a\nb","exit_code":0,"status":"completed"}}
{"type":"item.completed","item":{"id":"i2","type":"agent_message","text":"# Done\n\nAll **good**."}}
{"type":"item.completed","item":{"id":"i3","type":"web_search","query":"trunc...","action":{"type":"search","queries":["full one","full two"]}}}
{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":2}}
{"type":"item.started","item":{"id":"i4","type":"command_e
```

`tests/fixtures/transcript/claude-tools.jsonl`:

```
{"type":"system","subtype":"init","session_id":"sess-1","model":"opus"}
{"type":"assistant","message":{"content":[{"type":"text","text":"Checking."},{"type":"tool_use","id":"tu1","name":"Bash","input":{"command":"pytest -q"}},{"type":"weird_block","note":"unknown"}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"tu1","content":"2 passed","is_error":false}]}}
{"type":"assistant","message":{"content":[{"type":"tool_use","id":"tu2","name":"Edit","input":{"file_path":"/x/a.py"}}]}}
{"type":"user","message":{"content":[{"type":"tool_result","tool_use_id":"tu2","content":"File not found","is_error":true}]}}
{"type":"assistant","message":{"content":[{"type":"text","text":"All set."}]}}
{"type":"result","subtype":"success","result":"All set.","usage":{"input_tokens":11,"output_tokens":7}}
```

- [ ] **Step 2: Write the failing test**

```javascript
// tests/test_transcript_viewer.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';
import vm from 'node:vm';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const html = readFileSync(join(root, 'assets', 'transcript-viewer.html'), 'utf8');
const fixture = (name) =>
  readFileSync(join(here, 'fixtures', 'transcript', name), 'utf8');

// Pull the page's own script blocks and run them, so the tests exercise the
// shipped code rather than a second copy that can drift.
function blockOf(id) {
  const m = html.match(new RegExp(`<script id="${id}"[^>]*>([\\s\\S]*?)</script>`));
  assert.ok(m, `missing script block ${id}`);
  return m[1];
}
// runInThisContext, not createContext: a separate VM realm gives arrays a
// different Array.prototype, and deepStrictEqual then rejects structurally
// equal values as "not reference-equal".
globalThis.window = globalThis.window || {};
vm.runInThisContext(blockOf('viewer-normalize'));
const { normalize } = globalThis.window.HandoffViewer;

test('folds started/completed pairs into one row at the first position', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  const command = rows.filter(r => r.kind === 'command');
  assert.equal(command.length, 1);
  assert.equal(command[0].exit_code, 0);
  assert.equal(command[0].output, 'a\nb');
  assert.equal(command[0].pos, 2, 'keeps the item.started line position');
});

test('events with no item id each keep their own row', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  assert.equal(rows.filter(r => r.kind === 'lifecycle').length, 3);
});

test('an interior malformed line is visible, not dropped', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  const bad = rows.filter(r => r.kind === 'unparsed');
  assert.equal(bad.length, 1);
  assert.equal(bad[0].line, 4);
  assert.match(bad[0].text, /NOT JSON AT ALL/);
});

test('a truncated trailing line is reported as partial, not as a row', () => {
  const { rows, partial } = normalize(fixture('codex-basic.jsonl'));
  assert.equal(partial, true);
  assert.equal(rows.filter(r => r.kind === 'unparsed').length, 1);
});

test('web_search keeps the full query list, not the truncated one', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  const search = rows.find(r => r.kind === 'web_search');
  assert.deepEqual(search.queries, ['full one', 'full two']);
});

test('claude tool_use joins its tool_result and keeps both halves', () => {
  const { rows } = normalize(fixture('claude-tools.jsonl'));
  const bash = rows.find(r => r.tool_use_id === 'tu1');
  assert.equal(bash.kind, 'command');
  assert.equal(bash.command, 'pytest -q');
  assert.equal(bash.output, '2 passed');
});

test('a failed Edit is an error row, never a file change', () => {
  const { rows } = normalize(fixture('claude-tools.jsonl'));
  const edit = rows.find(r => r.tool_use_id === 'tu2');
  assert.equal(edit.is_error, true);
  assert.notEqual(edit.kind, 'file_change');
});

test('an unrecognized block inside a known assistant event survives', () => {
  const { rows } = normalize(fixture('claude-tools.jsonl'));
  assert.ok(rows.some(r => r.kind === 'unknown' && r.type === 'weird_block'));
});

test('the final answer appears once, not twice', () => {
  const { rows } = normalize(fixture('claude-tools.jsonl'));
  const finals = rows.filter(r => r.kind === 'agent' && r.text === 'All set.');
  assert.equal(finals.length, 1);
  assert.equal(finals[0].final, true);
});

test('rows come back in source order', () => {
  const { rows } = normalize(fixture('codex-basic.jsonl'));
  const positions = rows.map(r => r.pos);
  assert.deepEqual(positions, [...positions].sort((a, b) => a - b));
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `node --test tests/test_transcript_viewer.mjs`
Expected: FAIL — `missing script block viewer-normalize` content, or
`window.HandoffViewer` undefined.

- [ ] **Step 4: Implement the normalizer**

Replace the `#viewer-normalize` block's body in `assets/transcript-viewer.html`:

```javascript
(function () {
  const HV = (window.HandoffViewer = window.HandoffViewer || {});

  const SHELL_TOOLS = new Set(['Bash', 'BashOutput', 'Shell']);
  const EDIT_TOOLS = new Set(['Edit', 'Write', 'NotebookEdit', 'MultiEdit']);

  function mapCodexItem(item) {
    const t = item.type;
    if (t === 'agent_message') return { kind: 'agent', text: item.text || '' };
    if (t === 'reasoning') return { kind: 'reasoning', text: item.text || '' };
    if (t === 'command_execution') return {
      kind: 'command', command: item.command || '',
      output: item.aggregated_output || '', exit_code: item.exit_code,
      status: item.status };
    if (t === 'file_change') return {
      kind: 'file_change', changes: item.changes || [], status: item.status };
    if (t === 'mcp_tool_call') return {
      kind: 'mcp', server: item.server, tool: item.tool, args: item.arguments,
      result: item.result, error: item.error, status: item.status };
    if (t === 'web_search') return {
      kind: 'web_search',
      queries: (item.action && item.action.queries) ||
               (item.query ? [item.query] : []) };
    if (t === 'error') return { kind: 'error', text: item.message || '' };
    return { kind: 'unknown', type: t };
  }

  function mapClaudeBlock(block) {
    if (block.type === 'text') return { kind: 'agent', text: block.text || '' };
    if (block.type === 'thinking')
      return { kind: 'reasoning', text: block.thinking || block.text || '' };
    if (block.type === 'tool_use') {
      const name = block.name || '';
      const input = block.input || {};
      const base = { tool_use_id: block.id, tool: name, args: input };
      if (SHELL_TOOLS.has(name))
        return { ...base, kind: 'command', command: input.command || '', output: '' };
      if (name.startsWith('mcp__'))
        return { ...base, kind: 'mcp', server: name.split('__')[1] || '' };
      if (EDIT_TOOLS.has(name))
        // Provisional: demoted to `error` by its result when the edit failed.
        return { ...base, kind: 'file_change', pending_edit: true,
                 changes: input.file_path ? [{ path: input.file_path, kind: 'update' }] : [] };
      return { ...base, kind: 'tool' };
    }
    return { kind: 'unknown', type: block.type };
  }

  HV.normalize = function normalize(logText) {
    const rows = [];
    const byItemId = new Map();
    const byToolUseId = new Map();
    const lines = String(logText == null ? '' : logText).split('\n');
    let partial = false;

    const add = (row) => { rows.push(row); return row; };

    lines.forEach((line, index) => {
      const raw = line.trim();
      if (!raw) return;

      let event;
      try {
        event = JSON.parse(raw);
      } catch (_) {
        // Only the last non-empty line may be a partial write from a live job.
        const restEmpty = lines.slice(index + 1).every(l => !l.trim());
        if (restEmpty) partial = true;
        else add({ kind: 'unparsed', pos: index, line: index + 1, text: raw });
        return;
      }

      const type = event.type || '';
      const item = event.item || null;

      // --- Codex envelope -------------------------------------------------
      if (item && type.indexOf('item.') === 0) {
        const mapped = mapCodexItem(item);
        if (item.id && byItemId.has(item.id)) {
          const existing = byItemId.get(item.id);
          Object.assign(existing, mapped, { pos: existing.pos, raw: item });
          return;
        }
        const row = add({ pos: index, id: item.id, raw: item, ...mapped });
        if (item.id) byItemId.set(item.id, row);
        return;
      }

      // --- Claude stream-json ---------------------------------------------
      if (type === 'assistant' || type === 'user') {
        const blocks = (event.message && event.message.content) || [];
        const list = Array.isArray(blocks)
          ? blocks : [{ type: 'text', text: String(blocks) }];
        list.forEach((block) => {
          if (block && block.type === 'tool_result') {
            const target = byToolUseId.get(block.tool_use_id);
            const text = typeof block.content === 'string'
              ? block.content
              : JSON.stringify(block.content);
            if (!target) {
              add({ kind: 'error', pos: index, raw: block,
                    text: 'tool_result with no matching tool_use' });
              return;
            }
            target.output = text;
            target.is_error = Boolean(block.is_error);
            // A failed edit is not a file change.
            if (target.pending_edit && target.is_error) {
              target.kind = 'error';
              target.text = text;
            }
            delete target.pending_edit;
            return;
          }
          const mapped = mapClaudeBlock(block || {});
          const row = add({
            pos: index, raw: block, parent_tool_use_id: event.parent_tool_use_id,
            ...mapped });
          if (row.tool_use_id) byToolUseId.set(row.tool_use_id, row);
        });
        return;
      }

      if (type === 'result') {
        const finalText = event.result || '';
        const lastAgent = [...rows].reverse().find(r => r.kind === 'agent');
        if (finalText && lastAgent && lastAgent.text.trim() === finalText.trim()) {
          lastAgent.final = true;               // render once, marked as final
        } else if (finalText) {
          add({ kind: 'agent', pos: index, text: finalText, final: true, raw: event });
        }
        add({ kind: 'lifecycle', pos: index, label: 'result',
              usage: event.usage, raw: event });
        (event.errors || []).forEach(e =>
          add({ kind: 'error', pos: index, text: String(e), raw: event }));
        return;
      }

      if (type === 'system') {
        add({ kind: 'lifecycle', pos: index, label: 'session start',
              detail: event.session_id, raw: event });
        return;
      }

      // --- Lifecycle and loose errors, never correlated --------------------
      if (type === 'thread.started')
        return add({ kind: 'lifecycle', pos: index, label: 'thread started',
                     detail: event.thread_id, raw: event });
      if (type === 'turn.started')
        return add({ kind: 'lifecycle', pos: index, label: 'turn started', raw: event });
      if (type === 'turn.completed')
        return add({ kind: 'lifecycle', pos: index, label: 'turn completed',
                     usage: event.usage, raw: event });
      if (type === 'turn.failed')
        return add({ kind: 'error', pos: index, text: 'turn failed', raw: event });
      if (type === 'error')
        return add({ kind: 'error', pos: index,
                     text: event.message || event.error || 'error', raw: event });

      add({ kind: 'unknown', pos: index, type, raw: event });
    });

    rows.sort((a, b) => a.pos - b.pos);
    return { rows, partial };
  };
})();
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `node --test tests/test_transcript_viewer.mjs`
Expected: PASS, 10 tests.

- [ ] **Step 6: Check it against all four real logs**

```bash
node -e "
const {readFileSync}=require('fs'), vm=require('vm'), {globSync}=require('fs');
const html=readFileSync('assets/transcript-viewer.html','utf8');
const block=html.match(/<script id=\"viewer-normalize\"[^>]*>([\s\S]*?)<\/script>/)[1];
const ctx={window:{},console}; ctx.globalThis=ctx; vm.createContext(ctx); vm.runInContext(block,ctx);
for (const f of globSync('/Users/keli/dev/rv-car/.handoff/jobs/*/log.jsonl')) {
  const {rows,partial}=ctx.window.HandoffViewer.normalize(readFileSync(f,'utf8'));
  const c={}; for(const r of rows) c[r.kind]=(c[r.kind]||0)+1;
  console.log(f.split('/').slice(-2)[0].slice(0,46).padEnd(48), String(rows.length).padStart(3), partial?'PARTIAL':'', JSON.stringify(c));
}"
```

Expected, matching the prototype run: `spec-review` gives 39 rows
(30 command, 5 agent, 3 lifecycle, 1 error); `roles-and-lighting` gives 75 rows
including 1 mcp and 8 file_change; no job reports PARTIAL.

- [ ] **Step 7: Commit**

```bash
git add assets/transcript-viewer.html tests/test_transcript_viewer.mjs tests/fixtures/
git commit -m "feat: dual-format log normalizer with correlation and malformed-line handling"
```

---

### Task 5: The markdown and escaping boundary

marked passes raw HTML through by design — verified: `marked.parse('<img src=x onerror=alert(1)>')`
returns that string unchanged. Three renderer overrides close it.

**Files:**
- Modify: `assets/transcript-viewer.html` (`#viewer-render` block)
- Modify: `tests/test_transcript_viewer.mjs`
- Create: `tests/fixtures/transcript/xss.jsonl`

**Interfaces:**
- Consumes: `HandoffViewer.normalize` from Task 4.
- Produces: `HandoffViewer.escapeHtml(s) -> string`, `HandoffViewer.safeHref(url) -> string|null`,
  `HandoffViewer.renderMarkdown(text) -> string` (HTML string, allowlisted tags only).

- [ ] **Step 1: Write the fixture**

`tests/fixtures/transcript/xss.jsonl`:

```
{"type":"item.completed","item":{"id":"x1","type":"agent_message","text":"raw: <img src=x onerror=alert(1)>\n\n<svg onload=alert(1)></svg>\n\n[click](javascript:alert(1))\n\n![pic](javascript:alert(1))\n\n[ok](https://example.com \"a \\\" quote\")\n\nprose <div> and Vec<T>\n\ntext </script><img src=y onerror=alert(2)> more"}}
{"type":"item.completed","item":{"id":"x2","type":"command_execution","command":"cat evil.html","aggregated_output":"<img src=x onerror=alert(3)><script>alert(4)</script>","exit_code":0,"status":"completed"}}
```

- [ ] **Step 2: Write the failing test**

```javascript
// append to tests/test_transcript_viewer.mjs

// The render block needs marked, so build a context with both.
let cachedViewer = null;
function renderContext() {
  if (cachedViewer) return cachedViewer;
  globalThis.window = globalThis.window || {};
  vm.runInThisContext(blockOf('vendor-marked'));   // defines `marked`
  vm.runInThisContext(blockOf('viewer-normalize'));
  vm.runInThisContext(blockOf('viewer-render'));
  cachedViewer = globalThis.window.HandoffViewer;
  return cachedViewer;
}

// The renderer may only emit markup on these allowlists. Asserting the
// structure beats scanning for "onerror": an escaped &lt;img onerror=…&gt;
// is inert text, and a substring scan flags it as a false positive.
const ALLOWED_TAGS = new Set(['p','br','hr','a','img','em','strong','del','code',
  'pre','blockquote','ul','ol','li','h1','h2','h3','h4','h5','h6',
  'table','thead','tbody','tr','th','td']);
const ALLOWED_ATTRS = new Set(['href','src','alt','title','class','rel','align']);
const SAFE_SCHEME = /^(https?:|mailto:|#)/i;

function markupViolations(rendered) {
  const found = [];
  for (const m of rendered.matchAll(/<\/?([a-zA-Z][\w-]*)((?:\s+[^<>]*)?)\/?>/g)) {
    const tag = m[1].toLowerCase();
    if (!ALLOWED_TAGS.has(tag)) { found.push(`tag <${tag}>`); continue; }
    for (const a of (m[2] || '').matchAll(
        /([a-zA-Z-]+)\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/g)) {
      const name = a[1].toLowerCase();
      const value = a[2].replace(/^["']|["']$/g, '');
      if (!ALLOWED_ATTRS.has(name)) found.push(`attr ${name}`);
      if ((name === 'href' || name === 'src') && !SAFE_SCHEME.test(value.trim()))
        found.push(`scheme ${value}`);
    }
  }
  return found;
}

test('rendered agent markdown emits only allowlisted markup', () => {
  const HV = renderContext();
  const { rows } = HV.normalize(fixture('xss.jsonl'));
  const agent = rows.find(r => r.kind === 'agent');
  const rendered = HV.renderMarkdown(agent.text);
  assert.deepEqual(markupViolations(rendered), []);
});

test('hostile markup survives as visible text rather than vanishing', () => {
  const HV = renderContext();
  const { rows } = HV.normalize(fixture('xss.jsonl'));
  const rendered = HV.renderMarkdown(rows.find(r => r.kind === 'agent').text);
  assert.match(rendered, /&lt;img src=x onerror=alert\(1\)&gt;/);
});

test('javascript URLs never become links', () => {
  const HV = renderContext();
  const rendered = HV.renderMarkdown('[click](javascript:alert(1))');
  assert.doesNotMatch(rendered, /<a[^>]+href="javascript:/i);
  assert.match(rendered, /javascript:alert\(1\)/);  // shown as text
});

test('link text still renders through parseInline', () => {
  const HV = renderContext();
  const rendered = HV.renderMarkdown('[**bold** link](https://example.com)');
  assert.match(rendered, /<a href="https:\/\/example\.com"[^>]*><strong>bold<\/strong> link<\/a>/);
});

test('data URI images are rejected', () => {
  const HV = renderContext();
  const rendered = HV.renderMarkdown('![a](data:text/html;base64,PHN2Zz4=)');
  assert.deepEqual(markupViolations(rendered), []);
});

test('ordinary markdown still works', () => {
  const HV = renderContext();
  const rendered = HV.renderMarkdown('# H\n\n- a\n- b\n\n```js\nlet x=1;\n```');
  assert.match(rendered, /<h1>H<\/h1>/);
  assert.match(rendered, /<li>a<\/li>/);
  assert.match(rendered, /<code class="language-js">/);
});

test('safeHref accepts the four schemes and rejects the rest', () => {
  const HV = renderContext();
  for (const ok of ['https://a.b', 'http://a.b', 'mailto:a@b.c', '#frag'])
    assert.ok(HV.safeHref(ok), ok);
  for (const bad of ['javascript:alert(1)', 'data:text/html,x', 'vbscript:x',
                     ' javascript:alert(1)', 'JaVaScRiPt:alert(1)'])
    assert.equal(HV.safeHref(bad), null, bad);
});
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `node --test tests/test_transcript_viewer.mjs`
Expected: FAIL — `HandoffViewer.renderMarkdown is not a function`.

- [ ] **Step 4: Implement the boundary**

Replace the `#viewer-render` block's body:

```javascript
(function () {
  const HV = (window.HandoffViewer = window.HandoffViewer || {});
  const ENTITIES = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' };

  HV.escapeHtml = function escapeHtml(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, c => ENTITIES[c]);
  };

  const SAFE_SCHEME = /^(https?:|mailto:|#)/i;
  HV.safeHref = function safeHref(url) {
    const trimmed = String(url == null ? '' : url).trim();
    return SAFE_SCHEME.test(trimmed) ? trimmed : null;
  };

  // marked does not sanitize; it passes raw HTML through by design. Replacing
  // a renderer also replaces marked's own escaping, so each override escapes
  // what it emits. link() receives *tokens*, not rendered text — without
  // parseInline the link's contents are silently dropped.
  const renderer = new marked.Renderer();

  renderer.html = ({ text }) => HV.escapeHtml(text);

  renderer.link = function ({ href, title, tokens }) {
    const inner = this.parser.parseInline(tokens);
    const safe = HV.safeHref(href);
    if (!safe) return `${inner} [${HV.escapeHtml(href)}]`;
    const titleAttr = title ? ` title="${HV.escapeHtml(title)}"` : '';
    return `<a href="${HV.escapeHtml(safe)}"${titleAttr} rel="noreferrer noopener">${inner}</a>`;
  };

  renderer.image = function ({ href, title, text }) {
    const safe = HV.safeHref(href);
    if (!safe) return `${HV.escapeHtml(text || '')} [${HV.escapeHtml(href)}]`;
    const titleAttr = title ? ` title="${HV.escapeHtml(title)}"` : '';
    return `<img src="${HV.escapeHtml(safe)}" alt="${HV.escapeHtml(text || '')}"${titleAttr}>`;
  };

  marked.use({ renderer });

  HV.renderMarkdown = function renderMarkdown(text) {
    return marked.parse(String(text == null ? '' : text));
  };
})();
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `node --test tests/test_transcript_viewer.mjs`
Expected: PASS, 17 tests.

- [ ] **Step 6: Commit**

```bash
git add assets/transcript-viewer.html tests/test_transcript_viewer.mjs tests/fixtures/
git commit -m "feat: close marked's raw-HTML pass-through with escaping renderer overrides"
```

---

### Task 6: Header and row rendering

**Files:**
- Modify: `assets/transcript-viewer.html` (`#viewer-render` block, append)

**Interfaces:**
- Consumes: `normalize`, `renderMarkdown`, `escapeHtml` from Tasks 4–5.
- Produces: `HandoffViewer.mount(payload)` — called by the boot block and the dropzone.

- [ ] **Step 1: Implement mount**

Append to the `#viewer-render` block, inside the same IIFE:

```javascript
  const $ = (id) => document.getElementById(id);

  function localTime(iso) {
    if (!iso) return null;
    const d = new Date(iso);
    return isNaN(d) ? null : d.toLocaleString();
  }

  function definition(list, term, value) {
    if (value == null || value === '') return;
    const dt = document.createElement('dt');
    dt.textContent = term;                    // untrusted: textContent only
    const dd = document.createElement('dd');
    dd.textContent = value;
    list.append(dt, dd);
  }

  function renderHeader(payload) {
    const box = $('header');
    box.textContent = '';
    const meta = payload.meta || {};
    const list = document.createElement('dl');

    definition(list, 'job', payload.job_id);
    definition(list, 'label', meta.label);
    // A resumed job records neither backend nor role, and model=inherit.
    definition(list, 'role', meta.role || (payload.parent_job_id ? 'not recorded' : null));
    definition(list, 'backend', meta.backend || (payload.parent_job_id ? 'not recorded' : null));
    definition(list, 'model',
      meta.model === 'inherit' ? 'inherited from parent' : meta.model);
    definition(list, 'effort', meta.effort);
    definition(list, 'read only', meta.read_only);
    definition(list, 'parent', payload.parent_job_id);
    definition(list, 'state', payload.state);

    const started = localTime(meta.submitted_at);
    const ended = localTime(payload.exit_code_mtime);
    if (started) definition(list, 'window', ended ? `${started} → ${ended}`
                                                  : `${started} → still running`);
    definition(list, 'last log write', localTime(payload.log_mtime));
    definition(list, 'page generated', localTime(payload.generated_at));
    if (!started && !ended)
      definition(list, 'timing', 'no timestamps in this log');

    box.append(list);
    box.hidden = false;
  }

  function summarize(row) {
    if (row.kind === 'command') return row.command;
    if (row.kind === 'file_change')
      return (row.changes || []).map(c => `${c.kind} ${c.path}`).join(', ');
    if (row.kind === 'mcp') return `${row.server || '?'}.${row.tool || '?'}`;
    if (row.kind === 'tool') return row.tool || 'tool';
    if (row.kind === 'web_search') return (row.queries || []).join(' | ');
    if (row.kind === 'lifecycle') return row.label + (row.detail ? ` ${row.detail}` : '');
    if (row.kind === 'unparsed') return `unparsed line ${row.line}`;
    if (row.kind === 'error') return row.text || 'error';
    return row.type || row.kind;
  }

  function glyphFor(row) {
    if (row.kind === 'error' || row.is_error) return '!';
    if (row.kind === 'unparsed') return '?';
    if (row.kind === 'command')
      return row.exit_code === 0 ? 'ok' : (row.exit_code == null ? '..' : 'x');
    if (row.kind === 'file_change') return 'w';
    return '-';
  }

  function bytes(n) {
    if (!n) return '';
    return n < 1024 ? `${n} B` : `${(n / 1024).toFixed(1)} KB`;
  }

  function renderRow(row) {
    if (row.kind === 'agent' || row.kind === 'reasoning') {
      const article = document.createElement('article');
      article.className = 'agent';
      // The ONLY innerHTML in the viewer, and only on renderMarkdown output,
      // whose emitted markup is allowlisted by the Task 5 overrides.
      article.innerHTML = HV.renderMarkdown(row.text || '');
      if (row.kind === 'reasoning') article.classList.add('muted');
      return article;
    }
    const details = document.createElement('details');
    details.className = 'row';
    const summary = document.createElement('summary');

    const glyph = document.createElement('span');
    glyph.className = 'glyph ' + (glyphFor(row) === 'ok' ? 'ok'
                                : glyphFor(row) === 'x' ? 'bad' : '');
    glyph.textContent = glyphFor(row);

    const label = document.createElement('span');
    label.className = 'label';
    label.textContent = summarize(row);      // untrusted: textContent

    const size = document.createElement('span');
    size.className = 'size';
    size.textContent = bytes((row.output || '').length);

    summary.append(glyph, label, size);
    details.append(summary);

    const body = document.createElement('pre');
    // Full source object, so a field the mapping table does not name stays
    // reachable rather than being silently lost.
    body.textContent = (row.output ? row.output + '\n\n' : '') +
      (row.text && row.kind !== 'error' ? row.text + '\n\n' : '') +
      JSON.stringify(row.raw, null, 1);
    details.append(body);
    return details;
  }

  HV.mount = function mount(payload) {
    const { rows, partial } = HV.normalize(payload.log_text || '');
    renderHeader(payload);

    const stream = $('stream');
    stream.textContent = '';
    rows.forEach(row => stream.append(renderRow(row)));

    if (partial) {
      const note = document.createElement('p');
      note.className = 'muted';
      note.textContent = 'log continues — the last line was still being written';
      stream.append(note);
    }

    const counts = rows.reduce((acc, r) => (acc[r.kind] = (acc[r.kind] || 0) + 1, acc), {});
    $('counts').textContent = Object.entries(counts)
      .map(([k, v]) => `${v} ${k}`).join(' · ');
    $('controls').hidden = false;
    $('dropzone').textContent = 'Drop another log.jsonl to replace this transcript.';

    const toggle = $('only-agent');
    toggle.onchange = () => {
      stream.querySelectorAll('.row').forEach(el => { el.hidden = toggle.checked; });
    };
  };
```

- [ ] **Step 2: Verify against every real log**

```bash
for j in /Users/keli/dev/rv-car/.handoff/jobs/*/; do
  python3 scripts/render-transcript.py "$(basename "$j")" \
    --repo /Users/keli/dev/rv-car --no-open
done
open /Users/keli/dev/rv-car/.handoff/transcripts/job-2026-09-07T23-09-04-40919-spec-review.html
```

Check by eye, and record the result in the commit message:
- The 110 KB command output is folded, not blocking the page.
- The five agent messages render as markdown with headings and lists.
- The header shows a local-time window and a `page generated` line.
- The `-r2` resume job shows `model: inherited from parent`, `role: not recorded`,
  and its parent job id.
- "Conversation only" hides every trace row and leaves the agent messages.

- [ ] **Step 3: Verify the two drop paths**

```bash
open assets/transcript-viewer.html
```

- Drop `job-2026-09-07T23-09-04-40919-spec-review/log.jsonl`. It renders, and the
  header says no timestamps are in this log.
- Drop a *second*, different log on the same page. The previous job's header is
  replaced, not merged.
- Repeat both on a generated page, which starts with a payload already loaded.

- [ ] **Step 4: Confirm no console errors, then commit**

```bash
git add assets/transcript-viewer.html
git commit -m "feat: transcript header and row rendering"
```

---

### Task 7: Skill surface, gates, and version bump

**Files:**
- Modify: `SKILL.md`, `README.md`, `scripts/check-skill-repo.sh`,
  `test-prompts.json`, `CHANGELOG.md`
- Create: `docs/releases/v3.6.0.md`
- Modify: `.github/workflows/checks.yml`

- [ ] **Step 1: Add the trigger to SKILL.md**

In the `description` frontmatter, after the `tryout` clause, add:
`"/agent-handoff transcript" or "show me the transcript" / "conversation history" of a job (renders a delegated job's log.jsonl as HTML),`

Bump `version: 3.5.0` to `version: 3.6.0`.

Then add this section after `## Tool Location`:

```markdown
## Transcript

On `/agent-handoff transcript [<pid|jobId|folder>]`, or when the user asks to see
a job's transcript or conversation history, render it and open it:

```bash
python3 "$HANDOFF_DIR/scripts/render-transcript.py" [<selector>] --repo "$REPO"
```

The selector is optional; omitted or `last` means the newest job. A folder path,
an exact jobId, a pid, and a remembered label all resolve. When more than one job
matches — a label and its `-r2` resume round, for instance — the script lists the
candidates and exits without guessing; pass one of the printed names.

This renders and opens a page. It does not delegate anything and starts no job.
```

- [ ] **Step 2: Add both files to the required-file gate**

In `scripts/check-skill-repo.sh`, beside the other `check_file` lines:

```bash
check_file "scripts/render-transcript.py"
check_file "assets/transcript-viewer.html"
```

- [ ] **Step 3: Add the File Map entries**

In `README.md`'s File Map, in path order:

```text
assets/transcript-viewer.html           Self-contained transcript viewer; also reads a dropped log.jsonl
scripts/render-transcript.py            Renders a job's log.jsonl into the viewer and opens it
```

Update the version badge from `3.5.0` to `3.6.0`.

- [ ] **Step 4: Add the regression prompts**

Append to `test-prompts.json`. The `must_not` list is what keeps the transcript
command from being answered by starting a delegation run:

```json
{
  "id": "transcript-renders-not-delegates",
  "prompt": "/agent-handoff transcript last",
  "expected_behavior": "Runs scripts/render-transcript.py against the newest job in .handoff/jobs and opens the generated HTML page. Does not plan, split, or submit any job.",
  "must_not": ["delegate-codex.sh submit", "goal.md", "Handoff Session Receipt"]
},
{
  "id": "transcript-natural-language",
  "prompt": "Show me the conversation history of the last job",
  "expected_behavior": "Recognizes this as the transcript command and renders the newest job's log.jsonl, rather than summarizing the job from its result.",
  "must_not": ["delegate-codex.sh submit", "codex exec"]
}
```

- [ ] **Step 5: Add the JS test to CI**

In `.github/workflows/checks.yml`, after the `python3 -m unittest discover` step:

```yaml
      - name: Viewer tests
        run: node --test tests/test_transcript_viewer.mjs
```

`ubuntu-latest` ships node, so this needs no setup step. The normalizer and the
escaping helpers are the feature's highest-risk logic and they are JavaScript;
leaving them to manual checks alone is the worse trade.

- [ ] **Step 6: Write the changelog and release note**

Add a `## 3.6.0` section to `CHANGELOG.md` naming the new command, both new
files, and the two log formats it reads. Create `docs/releases/v3.6.0.md`
following the shape of `docs/releases/v2.0.1.md`.

- [ ] **Step 7: Run every gate**

```bash
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
bash scripts/check-skill-repo.sh .
python3 scripts/english-only-scan.py
python3 scripts/run-test-prompts.py
bash install.sh --dry-run
```

Expected: all pass; `check-skill-repo.sh` reports `fail=0`. One pre-existing
pre-existing WARN in `handoff-setup.py` is unrelated to this work.

- [ ] **Step 8: Commit**

```bash
git add SKILL.md README.md scripts/check-skill-repo.sh test-prompts.json \
        CHANGELOG.md docs/releases/v3.6.0.md .github/workflows/checks.yml
git commit -m "feat: wire /agent-handoff transcript into the skill surface (v3.6.0)"
```

---

### Task 8: Verify the Claude backend column against a real log

The Claude mapping in Task 4 is written from reading `write_claude_exec_line`
and the tool-use message shape. No captured `stream-json` log exists in this
repo, and the fixture in `tests/test_delegate_role.py` has a `tool_use` with no
`id` and no `tool_result`, so it cannot validate the join. This task closes that.

**Files:**
- Modify: `assets/transcript-viewer.html` if the real shape differs
- Modify: `tests/fixtures/transcript/claude-tools.jsonl` to match reality

- [ ] **Step 1: Configure a claude-backed identity if none is set**

```bash
python3 "$HANDOFF_DIR/scripts/handoff-config.py" resolve --repo .
```

`fast_worker` already resolves to `backend = "claude"` in this repo's config.

- [ ] **Step 2: Submit a job that must use a tool and must hit one failure**

```bash
cat > /tmp/claude-probe.md <<'EOF'
Read README.md and report its first heading. Then attempt to read the file
docs/definitely-not-here.md, which does not exist, and report what happened.
Do not create or modify any file.
EOF
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit --repo . \
  --prompt-file /tmp/claude-probe.md --label claude-probe \
  --role fast_worker --read-only
```

- [ ] **Step 3: Inspect the real event shapes**

```bash
JOB=.handoff/jobs/<jobId>
python3 -c "
import json,collections,sys
c=collections.Counter(); blocks=collections.Counter()
for line in open('$JOB/log.jsonl'):
    if not line.strip(): continue
    e=json.loads(line); c[e.get('type')]+=1
    for b in (e.get('message') or {}).get('content') or []:
        blocks[b.get('type')]+=1
print('events:', dict(c)); print('blocks:', dict(blocks))
print('top-level keys:', sorted({k for l in open('$JOB/log.jsonl') if l.strip() for k in json.loads(l)}))
"
```

Record three things: whether any event carries a timestamp field, whether
`tool_result` blocks appear under a `user` event with a `tool_use_id`, and
whether `result.result` repeats the final assistant text.

- [ ] **Step 4: Render it and compare**

```bash
python3 scripts/render-transcript.py <jobId> --repo . --no-open
open .handoff/transcripts/<jobId>.html
```

The tool call must show its name and input on one row with its result; the
failed read must render as an error, not as a completed file change; the final
answer must appear once.

- [ ] **Step 5: Fix the mapping if reality differs, and update the fixture**

Replace `tests/fixtures/transcript/claude-tools.jsonl` with lines taken from the
real log (trimmed, with any absolute paths rewritten). Re-run
`node --test tests/test_transcript_viewer.mjs` until it passes against the real
shape. If Claude does emit per-event timestamps, update the spec's Context
section, which currently records this as unverified.

- [ ] **Step 6: Commit**

```bash
git add assets/transcript-viewer.html tests/fixtures/transcript/claude-tools.jsonl \
        docs/specs/design_transcript-viewer.md
git commit -m "test: validate the Claude stream-json mapping against a real job"
```

---

## Verification

The whole gate, as CI runs it plus the two manual passes:

```bash
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
bash scripts/check-skill-repo.sh .
python3 scripts/english-only-scan.py
python3 scripts/run-test-prompts.py
bash install.sh --dry-run

# real logs, all four
for j in /Users/keli/dev/rv-car/.handoff/jobs/*/; do
  python3 scripts/render-transcript.py "$(basename "$j")" \
    --repo /Users/keli/dev/rv-car --no-open
done
```

Manual, once, per Task 6 Step 3: drop a log onto the bare template, then drop a
second one, and confirm the first job's header is replaced rather than merged.

## Not covered by automated checks

- **DOM insertion.** The node tests assert that `renderMarkdown` emits only
  allowlisted markup. That the *rest* of the page uses `textContent` is enforced
  by reading the code: `innerHTML` appears exactly once in
  `assets/transcript-viewer.html`, in `renderRow`, applied only to
  `renderMarkdown` output. Grep for it in review:
  `grep -n "innerHTML" assets/transcript-viewer.html` must return one line.
- **Browser rendering.** Layout, the fold-out behaviour, and the filter toggle
  are checked by eye in Task 6.
- **Skill dispatch.** `run-test-prompts.py` validates prompt structure only. That
  `/agent-handoff transcript` renders rather than delegating is checked by
  running it.
