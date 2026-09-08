# Transcript viewer for delegated jobs

## Context

Every delegated job writes `log.jsonl` into `<repo>/.handoff/jobs/<jobId>/`. Today the only ways to read it are `delegate-codex.sh result` (which prints the last agent message and a command count) and `tail`ing raw JSON. Neither answers "what did the worker actually do, and why did it conclude that" — the question a driver asks when a job comes back with a verdict they want to check, or when a monitoring tick shows an anomaly.

The four jobs in a real target repo (`rv-car`) show the shape of the problem:

| Item type | Count | Note |
| --- | --- | --- |
| `command_execution` (started + completed) | 111 pairs | largest single event is 110 KB of shell output |
| `agent_message` | 16 | the only markdown-bearing content |
| `file_change` (started + completed) | 10 pairs | paths and kind, no diff body |
| `mcp_tool_call` | 1 pair | `server`, `tool`, `arguments`, `result` |
| `web_search` | 2 pairs | |
| `error` items and one top-level `error` | 5 | |
| `thread.started`, `turn.started`, `turn.completed`, `turn.failed` | 6 | `turn.completed` carries the usage totals |

Logs run 23–139 lines and 72–656 KB. Inlining a whole log into one HTML page is comfortable at that size.

Outcome: `/agent-handoff transcript [<pid|jobId|folder>]` renders a job's `log.jsonl` as a single self-contained HTML page and opens it. The same page accepts a dropped `log.jsonl` when opened directly, with no generation step.

### Two findings that shaped the design

**`log.jsonl` has no timestamps.** Across all 280 events in the four sample jobs, the complete set of top-level keys is `type`, `thread_id`, `item`, `message`, `error`, `usage`. Codex `exec --json` emits no per-event clock, and neither does Claude's `stream-json`. Per-event local time cannot be rendered from the file. Codex rollout files under `~/.codex/sessions/` do carry ISO timestamps and could be joined on `session_id`, and `delegate-codex.sh` could stamp lines as it writes them — both were considered and rejected (see Not doing).

**There are two log formats, not one.** `write_claude_exec_line` in `scripts/delegate-codex.sh` writes Claude Code's `--output-format stream-json` when an identity's backend is `claude`; the Codex path writes the `item.*` envelope. The viewer handles both. `cmd_result` in the same file already carries a dual-format parser and is the reference mapping.

### Decisions already made

- **Job-level times only.** The header renders the job window — `meta.submitted_at` and the log's mtime — through `toLocaleString()`, which is real local time. Event rows carry sequence position and no clock, because that data does not exist. A dropped log has no `meta` and shows no clock at all, and says so.
- **Conversation-first reading model.** Agent messages are the spine of the page at full width. Tool activity collapses to one-line rows that expand on demand, with a filter to hide the trace entirely. The 16:111 ratio of messages to commands is what makes this the right default.
- **One artifact serves both entry points.** The generated page and the droppable page are the same file. A build step that produces two variants would be two things to keep in sync.
- **All parsing lives in the page's JavaScript.** If Python normalized events, the drop path would need a second parser in another language. Python only finds the job and writes the payload into the page.
- **Vendored marked, with its raw-HTML pass-through closed by renderer override.** Not DOMPurify.
- **No `references/transcript.md`.** `setup.md` and `tryout.md` are reference documents because they describe multi-step flows with packets and protocols. This is one command with one argument; SKILL.md carries it directly.

## Design

### Artifacts

**`assets/transcript-viewer.html`** — the whole product, around 55 KB. Vendored `marked.min.js` v15.0.12 (39 KB, MIT) inlined in a `<script>` block, then the viewer's own CSS and JS, then an empty payload slot and a dropzone. Opening this file directly from the skill's install directory and dropping a `log.jsonl` on it works with no other step.

**`scripts/render-transcript.py`** — around 120 lines. Resolves an identifier to a job directory, reads `log.jsonl`, `meta`, `exit_code` and the `cancelled` marker, substitutes the payload slot, writes `<repo>/.handoff/transcripts/<jobId>.html`, and opens it with `webbrowser.open()`. `--no-open` prints the path instead, for headless use. `--repo` as on every other repo-dependent script.

`.handoff/` is gitignored in both this repo and target repos, so generated transcripts are never committed.

### The payload slot

The template carries a placeholder the renderer replaces:

```html
<script id="handoff-payload" type="application/json">null</script>
```

Python writes a JSON object of `{meta, log_lines, exit_code, cancelled, log_mtime, job_id}` into that slot, with every `<` emitted as its six-character JSON unicode escape (backslash-u-0-0-3-c), so no closing-script sequence inside captured shell output can break out of the element. The page reads the slot on load; when it holds `null`, it shows the dropzone instead.

`log_lines` is the raw text of the file, unparsed. The page runs the same normalizer over it as over a dropped file.

### Normalization

Both formats collapse to one event list. **Events are keyed by item id, and a later event for an id replaces an earlier one.** That single rule folds the 111 `item.started`/`item.completed` pairs into 111 rows, and renders an in-flight job correctly, where an item has a `started` and no `completed` yet.

| Normalized kind | Codex `exec --json` | Claude `stream-json` |
| --- | --- | --- |
| `agent` | `item.completed` with `item.type` `agent_message` | `assistant` → `message.content[].text`; the `result` event's final text |
| `command` | `command_execution`: `command`, `aggregated_output`, `exit_code`, `status` | `content[].tool_use` joined to its `tool_result` by `tool_use_id` |
| `file_change` | `file_change` → `changes[]` of `{path, kind}` | `tool_use` on an edit or write tool |
| `mcp` | `mcp_tool_call`: `server`, `tool`, `arguments`, `result`, `error` | — |
| `web_search` | `web_search` → `query` | — |
| `error` | `error` items, and the top-level `error` event | `error` events, `is_error` on a result |
| header | `thread.started` → thread id; `turn.completed` → `usage` | `system` init event; `result` → `usage` |

Any event that matches no rule renders as a labelled row with its JSON collapsed inside, rather than being dropped. The sample logs already contain a `turn.failed` and a top-level `error` that `cmd_result` ignores; silently discarding unrecognised events is how a viewer lies about what happened.

**Untested path, stated plainly.** All four sample jobs are `backend=codex`. The Claude column above is written from reading `write_claude_exec_line`, not from a captured log. Implementation must verify it against one real Claude-backed job before the feature is called done.

### Page structure

A header strip carries what `meta` knows: label, role, backend, model, effort, `read_only`, the job window in local time, final exit code or cancelled state, and the token usage from `turn.completed`. Under it, the event stream.

Agent messages render as full-width markdown prose. Every other kind renders as a single line that expands to a `<pre>` on click: a status glyph, a one-line summary, and a size or count. A command row reads as its command, exit code, and output size; a file-change row as its paths; an MCP row as `server.tool`. A filter control hides all non-agent rows.

### Escaping and the markdown boundary

Every string in `log.jsonl` is untrusted. `aggregated_output` is whatever file the worker happened to read — one sample event is 110 KB of a file's contents. Rendered through `innerHTML` from a `file://` page, a `<script>` in that content would execute.

- **Markdown is applied to agent message text only.** Commands, shell output, MCP results, file paths and unknown-event JSON go through `textContent`.
- **marked does not sanitize; it passes raw HTML through by design.** Two renderer overrides close it without a second vendored library:
  - `renderer.html` returns the token's raw text escaped, so a `<script>` in an agent message displays as visible text and never executes. This also preserves fidelity for prose that legitimately mentions `<div>` or `Vec<T>` — pre-escaping the input before parsing would double-escape those into a visible `&lt;div&gt;`.
  - `renderer.link` and `renderer.image` drop any `href` or `src` outside `http:`, `https:`, `mailto:` and `#`, which removes `javascript:` URLs.

The exact renderer signature in marked v15 is to be confirmed during implementation against a crafted fixture opened in a real browser. The approach holds regardless; the API detail is what needs checking.

`marked.min.js` v15.0.12 was scanned against this repo's three gates before being chosen: zero CJK characters, zero secret-pattern hits, zero high-risk command text.

### Identifier resolution

The argument is optional. Resolution runs in order and stops at the first match:

1. Exact directory name under `<repo>/.handoff/jobs/`.
2. Unique suffix match on the directory name, which covers the label a user remembers (`spec-review`).
3. A job whose `pid` file holds that value.
4. Omitted, or the literal `last`: the newest job by `meta.submitted_at`, falling back to directory mtime.

More than one candidate at any step prints the candidates and exits without guessing.

## Not doing

- **Stamping timestamps into `log.jsonl` as it is written.** Piping each CLI's stdout through a line-stamper in `delegate-codex.sh` would give real per-event local time for both backends and survive the drop path. It also modifies a runtime primitive that every job depends on, and only new jobs would benefit — existing logs stay timeless either way. Deferred; if per-event timing becomes the point rather than a nicety, this is the way to get it.
- **Reading Codex rollout files for timestamps.** `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl` carries per-event ISO timestamps and joins on the `session_id` the job already stores. It is Codex-only, same-machine-only, a third format to parse, and it breaks the drop path, where the file a user drags carries no timestamps at any price.
- **Inlining DOMPurify.** Correct and standard, and 22 KB of second vendored blob to solve what two renderer overrides solve.
- **Diff bodies for file changes.** `file_change` events carry paths and kind, no content. Rendering a diff would mean reading the repo at some commit, which makes the page depend on repo state instead of the log.
- **Search, export, or a two-pane outline.** Browser find covers search on a page this size. The rest is speculative until someone reads a transcript and asks for it.
- **A `references/transcript.md` flow document.** One command with one argument does not need a protocol document.

## Changes

| File | Change |
| --- | --- |
| `assets/transcript-viewer.html` | new — vendored marked, viewer, payload slot, dropzone |
| `scripts/render-transcript.py` | new — resolve, inject, open |
| `tests/test_render_transcript.py` | new — resolution and escaping |
| `SKILL.md` | `transcript` trigger in the description; a short section under Tool Location; version bump |
| `README.md` | File Map entries for both new files; version badge |
| `scripts/check-skill-repo.sh` | `check_file` for both new files |
| `test-prompts.json` | trigger prompt and `must_not` entries |
| `CHANGELOG.md`, `docs/releases/` | 3.5.0 → 3.6.0 |

## Verification

```bash
python3 -m unittest tests.test_render_transcript

# real logs, all four sample jobs
python3 scripts/render-transcript.py --repo /Users/keli/dev/rv-car --no-open
python3 scripts/render-transcript.py spec-review --repo /Users/keli/dev/rv-car --no-open

bash scripts/check-skill-repo.sh .
python3 scripts/english-only-scan.py
python3 scripts/run-test-prompts.py
```

Manual checks that no unit test covers:

- Open the generated page for the `spec-review` job. The 110 KB command output stays folded, the five agent messages render as markdown, the header shows a local-time job window.
- Open `assets/transcript-viewer.html` directly and drop a `log.jsonl` on it. The transcript renders; the header states that no timestamps are available.
- Render a fixture log whose `aggregated_output` contains `<script>alert(1)</script>` and whose agent message contains both a raw `<img onerror>` tag and a `[link](javascript:alert(1))`. Nothing executes, the tags display as text, the link is inert.
- One real `backend=claude` job, to verify the stream-json column of the mapping table.
