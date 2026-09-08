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
| `web_search` | 2 pairs | `query` is truncated; the full list is in `action.queries` |
| `error` items, plus one top-level `error` event | 5 | |
| `thread.started`, `turn.started`, `turn.completed`, `turn.failed` | 11 | 4 + 4 + 2 + 1; `turn.completed` carries the usage totals |

280 events in total. Logs run 23–139 lines and 72–656 KB. Inlining a whole log into one HTML page is comfortable at that size.

Outcome: `/agent-handoff transcript [<pid|jobId|folder>]` renders a job's `log.jsonl` as a single self-contained HTML page and opens it. The same page accepts a dropped `log.jsonl` when opened directly, with no generation step.

### Two findings that shaped the design

**`log.jsonl` has no timestamps.** Across all 280 events in the four sample jobs, the complete set of top-level keys is `type`, `thread_id`, `item`, `message`, `error`, `usage`. Codex `exec --json` emits no per-event clock. The Claude side is unverified — no captured `stream-json` log exists here, and the flag set in `write_claude_exec_line` neither requests nor suppresses timestamps — so the viewer reads a per-event time where the format supplies one and falls back to sequence position where it does not. Codex rollout files under `~/.codex/sessions/` do carry ISO timestamps and could be joined on `session_id`, and `delegate-codex.sh` could stamp lines as it writes them; both were considered and rejected (see Not doing).

**There are two log formats, not one.** `write_claude_exec_line` in `scripts/delegate-codex.sh` writes Claude Code's `--output-format stream-json` when an identity's backend is `claude`; the Codex path writes the `item.*` envelope. The viewer handles both.

### Decisions already made

- **Job-level times only.** The header renders the job window through `toLocaleString()`, which is real local time. Event rows carry sequence position, and a per-event clock only where the format supplies one.
- **Conversation-first reading model.** Agent messages are the spine of the page at full width. Tool activity collapses to one-line rows that expand on demand, with a filter to hide the trace entirely. The 16:111 ratio of messages to commands is what makes this the right default.
- **One artifact serves both entry points.** The generated page and the droppable page are the same file. A build step producing two variants would be two things to keep in sync.
- **All parsing lives in the page's JavaScript.** If Python normalized events, the drop path would need a second parser in another language. Python only finds the job and writes the payload into the page.
- **Vendored marked, with its raw-HTML pass-through closed by renderer override.** Not DOMPurify.
- **No `references/transcript.md`.** `setup.md` and `tryout.md` are reference documents because they describe multi-step flows with packets and protocols. This is one command with one argument; SKILL.md carries it directly.

## Design

### Artifacts

**`assets/transcript-viewer.html`** — the whole product, around 55 KB. Vendored `marked.min.js` v15.0.12 (39 KB, MIT) inlined in a `<script>` block, then the viewer's own CSS and JS, then an empty payload slot and a dropzone. Opening this file directly from the skill's install directory and dropping a `log.jsonl` on it works with no other step.

**`scripts/render-transcript.py`** — around 150 lines. Resolves an identifier to a job directory, reads the job's files, substitutes the payload slot, writes `<repo>/.handoff/transcripts/<jobId>.html`, and opens it with `webbrowser.open()`. `--no-open` prints the path instead, for headless use. `--repo` as on every other repo-dependent script.

The renderer checks whether its output path is ignored by Git (`git check-ignore -q`) and prints a one-line warning when it is not. `.handoff/` is gitignored in this repo and in `rv-car`, but that is not a repo-wide guarantee: `handoff-setup.py` accepts `--exclude-choice self` and `track`, both of which leave `.handoff/` tracked. A transcript embeds captured command output, so committing one by accident is a real disclosure.

### The payload slot

The template carries a placeholder the renderer replaces:

```html
<script id="handoff-payload" type="application/json">null</script>
```

Python writes a JSON object into that slot with every `<` emitted as its six-character JSON unicode escape (backslash-u-0-0-3-c), so no closing-script sequence inside captured output can break out of the element. That escaping protects the *element boundary* only; it says nothing about what happens when a string is later put into the DOM, which the Escaping section governs separately.

Payload fields: `job_id`, `meta` (parsed `key=value` pairs), `log_text`, `state`, `exit_code`, `exit_code_mtime`, `log_mtime`, `generated_at`, `parent_job_id`, `output_is_ignored`.

`log_text` is the raw text of the file, unparsed. The page runs the same normalizer over it as over a dropped file.

### Normalization

Both formats collapse to one ordered event list. Every normalized row keeps a **source position** — the index of the line that first produced it — and the list renders in source-position order.

**Correlation, not blanket replacement.** Only Codex `item.*` events carrying an `item.id` are correlated: a later event for the same id replaces the earlier row's content while keeping its original source position. That is what folds the 111 `item.started`/`item.completed` pairs into 111 chronological rows, and what renders an in-flight job whose last item has a `started` and no `completed`. `item.updated` correlates the same way. Events with no item id — `thread.started`, `turn.started`, `turn.completed`, `turn.failed`, and the top-level `error` — are never correlated and each keeps its own position. Claude content blocks are separate rows within their event, correlated only through `tool_use_id`.

| Normalized kind | Codex `exec --json` | Claude `stream-json` |
| --- | --- | --- |
| `agent` | `agent_message` → `text` | `assistant` → each `content[]` block of type `text` |
| `reasoning` | `reasoning` → `text` | `thinking` blocks where present |
| `command` | `command_execution`: `command`, `aggregated_output`, `exit_code`, `status` | `tool_use` naming a shell tool, joined to its `tool_result` |
| `tool` | — | any other `tool_use`, labelled by tool name, joined to its `tool_result` |
| `file_change` | `file_change` → `changes[]` of `{path, kind}`, plus `status` | a *successful* `tool_use` on an edit or write tool |
| `mcp` | `mcp_tool_call`: `server`, `tool`, `arguments`, `result`, `error`, `status` | `tool_use` on an MCP-named tool |
| `web_search` | `web_search` → `action.queries` where present, else `query` | — |
| `error` | `error` items, and the top-level `error` event | `is_error` on a `tool_result`; `error` on an event; `result.errors[]` |
| header | `thread.started` → thread id; `turn.completed` → `usage` | `system` init event; `result` → `usage` |

Claude-specific rules, since this is where a naive mapping loses evidence:

- **`tool_result` lives in a `user` event**, inside `message.content[]`, and joins its `tool_use` by `tool_use_id`. Its `content` is a string or a nested block list; both render.
- **Joining keeps both halves.** The result fills in the row's output; the tool's name and input stay on the row. A result never replaces the call.
- **A `tool_use` is not a command.** Read, Glob, WebFetch and MCP calls render as `tool` rows with their own labels. An edit or write tool becomes a `file_change` row only when its result is not an error — a failed Edit must not read as a completed file change.
- **Unrecognized blocks inside a recognized event** render as their own collapsed rows in place, rather than being dropped because the enclosing `assistant` event was understood.
- **The terminal `result` event usually repeats the last assistant text.** Render the final answer once: where `result.result` equals the last `agent` row's text, mark that row final instead of adding a row.
- **`parent_tool_use_id` is retained** on the row and shown as an attribution label. No nested-thread UI.

Any event or block matching no rule renders as a labelled row with its JSON collapsed inside, rather than being dropped. Expanded detail for a *recognized* row shows its full source object too, so a field the table does not name — `status`, `action.queries`, an unforeseen addition — stays reachable. The sample logs already contain a `turn.failed` and a top-level `error` that `cmd_result` ignores; silently discarding events is how a viewer lies about what happened.

**Malformed input.** A trailing line that fails to parse is treated as an in-flight partial write and rendered as a single muted "log continues" marker, because the worker appends to `log.jsonl` while the page may be reading it. Any *interior* line that fails to parse renders as a visible `unparsed line N` row carrying its raw text. One bad line never blanks the transcript, and never disappears without a mark: a transcript that quietly drops evidence is worse than no transcript.

**Untested path, stated plainly.** All four sample jobs are `backend=codex`. The Claude column is written from reading `write_claude_exec_line` and the tool-use message shape, not from a captured log. The existing Claude fixture in `tests/test_delegate_role.py` has a `tool_use` with no `id`, and no `tool_result` or `user` event at all, so it cannot validate the join. Implementation must capture one real Claude-backed job that uses at least one tool and has at least one tool failure.

### Page structure

A header strip carries what the job's own files know: label, role, backend, model, effort, `read_only`, the job window, state, and the token usage from `turn.completed`.

- **State** is derived exactly as `job_state()` derives it: `cancelled` marker → CANCELLED; `exit_code` present → DONE or FAILED by its value; else a live pid → RUNNING; else FAILED, meaning the worker died without writing an exit code. Absence of `exit_code` alone is never rendered as "running".
- **The job window** runs from `meta.submitted_at` to the `exit_code` file's mtime, the same evidence `make-receipt.py` uses for job duration. The log's mtime is shown separately and labelled "last log write". A running job shows an open-ended window.
- **The page is a snapshot.** `generated_at` renders in the header, so a page of a job that was still running is not mistaken for its final state.
- **Resumed jobs carry different metadata.** The real resumed sample has `label=resume`, `model=inherit`, `mode=resume`, `parent=<jobId>`, and **no `backend` or `role` line at all**. Missing fields render as "not recorded" and `inherit` renders as "inherited from parent", never as a blank or a guess. The parent job id is shown as a link where its directory is present.

Under the header, the event stream. Agent messages render as full-width markdown prose. Every other kind renders as a single line that expands on click: a status glyph, a one-line summary, and a size or count.

### Escaping and the markdown boundary

Every string in `log.jsonl` is untrusted. `aggregated_output` is whatever file the worker happened to read — one sample event is 110 KB of a file's contents.

**How the risk actually works.** A `<script>` element inserted through `innerHTML` does *not* execute; the HTML spec disables it. The live vectors are event-handler attributes that fire on insertion or shortly after — `<img src=x onerror=…>`, `<svg onload=…>` — and `javascript:` URLs on links and images. So the boundary is not "keep script tags out", it is "never build markup from log content".

- **Markdown is applied to agent and reasoning message text only.** Commands, shell output, tool results, MCP payloads, file paths, header values, thread ids, unknown type names, row labels and `unparsed line N` bodies all reach the DOM through `textContent`.
- **Overriding a marked renderer means inheriting its escaping duties.** The v15 signatures are token objects, confirmed against the pinned build: `html({text})`, `link({href, title, tokens})`, `image({href, title, text, tokens})`. `link` receives *tokens*, not rendered text, so an override must call `this.parser.parseInline(tokens)` or it silently drops the link's contents. The three overrides:
  - `html` returns its `text` escaped, so raw HTML in an agent message displays as visible text. This also preserves fidelity for prose that legitimately mentions `<div>` or `Vec<T>`; pre-escaping the input before parsing would double-escape those into a visible `&lt;div&gt;`.
  - `link` and `image` reject any `href`/`src` whose scheme falls outside `http:`, `https:`, `mailto:` and `#`, rendering the rejected URL as plain text rather than a live link, and attribute-escape the surviving `href` and `title` themselves, since replacing marked's renderer replaces its escaping too.

`marked.min.js` v15.0.12 was scanned against this repo's three gates before being chosen: zero CJK characters, zero secret-pattern hits, zero high-risk command text.

### Identifier resolution

The argument is optional. Resolution runs in order and stops at the first match:

1. **A path** — an argument containing a `/`, or naming an existing directory, is taken as a job folder directly. This is the `<folder>` case in the trigger.
2. **The reserved selector `last`**, or an omitted argument: the newest job by `meta.submitted_at`. This is checked *before* label matching, so a job whose label is literally `last` cannot intercept it; that job stays reachable by its full directory name. `submitted_at` has one-second precision, so ties break on directory mtime, then on name, descending.
3. **Exact directory name** under `<repo>/.handoff/jobs/`.
4. **All-digit argument matched against `pid` files.**
5. **Suffix match on the directory name**, covering the label a user remembers.

Two collision rules make the silent-wrong-job cases visible. An all-digit argument matching both a pid and a label suffix is reported as ambiguous rather than resolved by step order. And a suffix match returns **every** matching round: `glazing-slideout-capture` matches both the original job and its `-r2` resume, and the renderer lists both rather than picking the original and hiding a later round. Any step yielding more than one candidate prints them and exits without guessing.

## Not doing

- **Stamping timestamps into `log.jsonl` as it is written.** Piping each CLI's stdout through a line-stamper in `delegate-codex.sh` would give real per-event local time for both backends and survive the drop path. It also modifies a runtime primitive every job depends on, and only new jobs would benefit. Deferred; if per-event timing becomes the point rather than a nicety, this is the way to get it.
- **Reading Codex rollout files for timestamps.** `~/.codex/sessions/YYYY/MM/DD/rollout-<ts>-<session_id>.jsonl` carries per-event ISO timestamps and joins on the `session_id` the job already stores. It is Codex-only, same-machine-only, a third format to parse, and it breaks the drop path, where the file a user drags carries no timestamps at any price.
- **Inlining DOMPurify.** Correct and standard, and 22 KB of second vendored blob to solve what three renderer overrides solve.
- **Diff bodies for file changes.** `file_change` events carry paths and kind, no content. Rendering a diff would mean reading the repo at some commit, which makes the page depend on repo state instead of the log.
- **A nested view of subagent threads.** `parent_tool_use_id` is retained and labelled; a tree UI on top of it is speculative until a real log needs one.
- **Search, export, or a two-pane outline.** Browser find covers search on a page this size.
- **A `references/transcript.md` flow document.** One command with one argument does not need a protocol document.

## Changes

| File | Change |
| --- | --- |
| `assets/transcript-viewer.html` | new — vendored marked, normalizer, renderer, payload slot, dropzone |
| `scripts/render-transcript.py` | new — resolve, read job state, inject, open |
| `tests/test_render_transcript.py` | new — resolution, collisions, state derivation, escaping |
| `tests/fixtures/` | new — Codex and Claude sample logs, including tool failure and malformed lines |
| `SKILL.md` | `transcript` trigger in the description; a short section under Tool Location; version bump |
| `README.md` | File Map entries for the new files; version badge |
| `scripts/check-skill-repo.sh` | `check_file` for both new files |
| `test-prompts.json` | trigger prompt and `must_not` entries |
| `CHANGELOG.md`, `docs/releases/` | 3.5.0 → 3.6.0 |

## Verification

```bash
python3 -m unittest tests.test_render_transcript

# every sample job, not a subset
for j in /Users/keli/dev/rv-car/.handoff/jobs/*/; do
  python3 scripts/render-transcript.py "$(basename "$j")" \
    --repo /Users/keli/dev/rv-car --no-open
done

bash scripts/check-skill-repo.sh .
python3 scripts/english-only-scan.py
python3 scripts/run-test-prompts.py
```

`test_render_transcript.py` covers the Python half: each resolution step, the pid-versus-label collision, the multi-round suffix case, the reserved `last` selector against a job labelled `last`, state derivation across all five `job_state()` outcomes, a resumed job's missing `backend`/`role`, and payload escaping of a log containing a closing-script sequence.

The normalizer is JavaScript, so it needs its own observable checks against fixture logs, driven in a real browser: pair folding, events with no item id keeping distinct positions, an in-flight job whose last item has no completion, a truncated trailing line rendering as "log continues", an interior malformed line rendering as `unparsed line N`, a mixed Claude assistant event carrying text plus tool_use plus an unrecognized block, a failed Edit not rendering as a file change, and the final answer appearing once rather than twice.

Security checks assert the resulting DOM. A fixture that only proves "no alert fired" gives false confidence, since an inert `<script>` proves nothing on its own. Against a fixture whose command output and agent message carry `<img src=x onerror=…>`, `<svg onload=…>`, a `[link](javascript:…)`, a closing-script sequence, and a link whose title contains a quote: assert that the rendered subtree contains no element with an `on*` attribute, no `href`/`src` outside the allowed schemes, and that the payloads are present as text.

Manual checks that no automated check covers:

- Open the generated page for the `spec-review` job. The 110 KB command output stays folded, the agent messages render as markdown, the header shows a local-time job window and a `generated_at` snapshot line.
- Open `assets/transcript-viewer.html` directly and drop a `log.jsonl` on it. Then drop a *second* log on the same page and confirm the previous job's header metadata is cleared rather than merged. Repeat on a generated page, which starts with a payload already loaded.
- Capture one real `backend=claude` job that uses at least one tool and has at least one tool failure, and check the stream-json column against it. A text-only success cannot verify the join. Confirm from that log whether Claude's events carry timestamps.
- Confirm `/agent-handoff transcript` dispatches to the renderer without entering the delegation flow. `run-test-prompts.py` validates prompt structure only and cannot prove dispatch.
