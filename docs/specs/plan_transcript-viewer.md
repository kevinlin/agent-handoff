# Transcript Viewer Implementation Plan

**Goal:** Render a delegated job's `log.jsonl` as one self-contained HTML page, opened by `/agent-handoff transcript [<pid|jobId|folder>]`, and readable by dropping a log file onto the same page.

**Architecture:** Two artifacts. `assets/transcript-viewer.html` is the whole product — vendored marked, the normalizer, the renderer, a payload slot and a dropzone. `scripts/render-transcript.py` is thin: it resolves an identifier to a job directory, reads that job's files, substitutes the payload slot, writes to `<repo>/.handoff/transcripts/<jobId>.html`, and opens it. All log parsing lives in the page's JavaScript so the drop path needs no second parser.

**Tech Stack:** Python 3 stdlib only (argparse, json, pathlib, subprocess, webbrowser, unittest). Vendored marked v15.0.12. Node for tests only — the normalizer and escaping helpers are pure string functions, so they test without a DOM.

**Spec:** [`design_transcript-viewer.md`](design_transcript-viewer.md)

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
| `scripts/render-transcript.py` | Resolve identifier → job dir; derive state; build payload; write and open the page. |
| `assets/transcript-viewer.html` | Vendored marked, normalizer, escaping boundary, renderer, header, dropzone. |
| `tests/test_render_transcript.py` | Python half: resolution, collisions, state derivation, payload escaping. |
| `tests/test_transcript_viewer.mjs` | JS half: normalizer correlation and malformed input; renderer output allowlist. |
| `tests/fixtures/transcript/` | Small crafted logs: codex pairs, malformed lines, claude tool use, XSS payloads. |

---

### Task 1: Job discovery, identifier resolution, and state derivation

Implemented pure functions for job discovery (`list_jobs`, `read_meta`, `job_state`, `resolve`) and the two exception types `Ambiguous` and `NotFound` in `scripts/render-transcript.py`. Resolution handles paths, the reserved `last` selector, exact directory names, pid lookup, and suffix matching, with collision detection to prevent silent wrong-job picks.

### Task 2: Payload build, template injection, and the CLI

Added `build_payload` to assemble job metadata, state, and log text into a JSON dict, and `inject` to substitute it into the HTML template's payload slot with `<` escaped as `\u003c` to guard the script-element boundary. The `main(argv)` CLI wires resolution, injection, and `webbrowser.open()` together, with `--no-open` for headless use.

### Task 3: The page shell with vendored marked

Created the self-contained HTML page shell (`assets/transcript-viewer.html`) with vendored `marked` v15.0.12 (sha256-verified), a `#handoff-payload` slot, a dropzone, CSS for light/dark mode, and a boot script that parses the payload on load and reads dropped files.

### Task 4: The normalizer

Implemented the dual-format log normalizer in the page's `#viewer-normalize` script block. Correlates Codex `item.started`/`item.completed` pairs by item id, maps Claude `tool_use`/`tool_result` pairs by tool-use id, and classifies every row into one of: `agent`, `reasoning`, `command`, `tool`, `file_change`, `mcp`, `web_search`, `error`, `lifecycle`, `unparsed`, `unknown`. Interior malformed lines render as visible `unparsed line N` rows; a truncated trailing line reports as partial rather than erroring.

### Task 5: The markdown and escaping boundary

Closed marked's raw-HTML pass-through with three renderer overrides in the `#viewer-render` script block: `html()` escapes its input so raw HTML in agent messages displays as visible text, `link()` rejects non-`http(s)/mailto/#` schemes and calls `parseInline` on tokens to preserve link content, and `image()` rejects unsafe schemes. Exported `escapeHtml`, `safeHref`, and `renderMarkdown` on `window.HandoffViewer`.

### Task 6: Header and row rendering

Built the `mount(payload)` function that renders a header strip (job id, label, role, backend, model, state, time window) and the event stream (agent messages as full-width markdown prose, everything else as collapsible one-line rows). Added a "Conversation only" toggle to hide trace rows and a drop-to-replace path for loading a different log.

### Task 7: Skill surface, gates, and version bump

Wired the feature into the skill surface: trigger in `SKILL.md` description, `## Transcript` section under Tool Location, File Map entries in `README.md`, `check_file` lines in `check-skill-repo.sh`, regression prompts in `test-prompts.json`, JS test step in CI (`checks.yml`), and the v3.6.0 version bump across `SKILL.md`, `README.md`, `CHANGELOG.md`, and `docs/releases/v3.6.0.md`.

### Task 8: Verify the Claude backend column against a real log

Validated the Claude `stream-json` mapping against a real `backend=claude` job that exercised tool use and tool failure, then updated `tests/fixtures/transcript/claude-tools.jsonl` to match the actual event shapes. This closed the untested-path gap noted in the design.

---

## Verification

All gates pass: `python3 -m unittest discover -s tests`, `node --test tests/test_transcript_viewer.mjs`, `bash scripts/check-skill-repo.sh .`, `python3 scripts/english-only-scan.py`, `python3 scripts/run-test-prompts.py`, `bash install.sh --dry-run`. Real-log rendering verified against every sample job.

## Not covered by automated checks

- **DOM insertion.** The node tests assert that `renderMarkdown` emits only allowlisted markup. That the *rest* of the page uses `textContent` is enforced by reading the code: `innerHTML` appears exactly once in `assets/transcript-viewer.html`, in `renderRow`, applied only to `renderMarkdown` output. Grep for it in review: `grep -n "innerHTML" assets/transcript-viewer.html` must return one line.
- **Browser rendering.** Layout, the fold-out behaviour, and the filter toggle are checked by eye in Task 6.
- **Skill dispatch.** `run-test-prompts.py` validates prompt structure only. That `/agent-handoff transcript` renders rather than delegating is checked by running it.

## Changelog

- 2026-09-08 — **Compacted post-implementation.** Removed step-by-step tasks, file-by-file diffs, code snippets, and verification commands now that the feature has shipped. Preserved Goal, Global Constraints, File Structure, task intent summaries, and follow-ups. Original plan recoverable via git history.
