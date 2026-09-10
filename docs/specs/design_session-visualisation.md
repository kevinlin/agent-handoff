# Agent Handoff — session visualisation

## Context

`docs/specs/design_agent-handoff.md` is the design of record for the phases where a run is decided and done. `docs/specs/design_agent-handoff-evidence.md` covers what those phases leave on disk and the three artifacts that read it back. This document covers the fourth reader: one page that puts a whole run in front of a human at once, and lets them drill from the run into any single delegated job without leaving it.

Why a fourth reader rather than a fourth artifact: the evidence flows already answer their questions well one at a time. `render-transcript.py` answers "what did this job do". `render-cost-receipt.py` answers "what did this run consume". Neither answers "what happened, in what order, and where did the driver hand off" — the question a reviewer asks first and the three existing outputs can only answer by being read side by side in three tabs the reader arranges by hand.

Shipping in **v3.7.2**.

### Scope

Covered: the `/agent-handoff visualise` command, the loopback server behind it, the session shell page, the overview tab and its hotspot contract, and the two new read-only tabs.

| Out of scope | Where it lives |
| --- | --- |
| Plan, split, delegate, monitor, review | `docs/specs/design_agent-handoff.md` |
| The evidence store, transcript, receipt, cost receipt | `docs/specs/design_agent-handoff-evidence.md` |
| The job primitive | `scripts/delegate-codex.sh` header comment |
| Identity configuration and the setup wizard | `docs/config-schema.md`, `references/setup.md` |
| Generating the timeline SVG | a `baoyu-diagram` instruction in `SKILL.md`; no script does it |

### The rule this flow obeys, and the one it deliberately relaxes

The evidence design's rule is that every field in every artifact traces back to a file read during the run. This page keeps that rule for everything it computes and relaxes it in exactly one place, on purpose and with a label.

The overview is a **hand-authored SVG** produced by a diagram model reading the receipt and `goal.md`. It bands the run into the five phases, says what the spec review found, and explains why the anomaly was recorded. None of that exists in any file as structured data. That is what makes it worth looking at. It is also a narrative, so:

- The SVG is labelled on the page as a narrative illustration, and the tabs beside it as the measured record.
- Its **lane map is checked against evidence** — every lane must name a job the receipt indexes, every indexed job must have a lane, and every declared window must match the measured one. A diagram generated for a different receipt cannot render silently under this one.
- Its **prose is not checked**. A wrong token figure in the subtitle will display. This is the accepted cost of choosing a hand-authored overview over a generated chart, and it is recorded here so it is not later mistaken for an oversight.

Nothing else on the page is narrative. The job table, the session facts, the denial list, the anomaly list, and every discrepancy the verification reports are read from files the run wrote.

---

## Flow — `/agent-handoff visualise`

`/agent-handoff visualise [<receipt-file>]` starts a loopback HTTP server and opens one page. The selector is optional; omitted means the newest receipt under `.handoff/receipts/`, the rule `render-cost-receipt.py` already uses. `visualize` resolves the same way, as do prompts like "visualise the session" and "show me the session timeline".

```bash
python3 "$HANDOFF_DIR/scripts/handoff-session-ui.py" [<receipt-file>] --repo "$REPO"
```

The `-ui.py` suffix is the repo's existing signal that a script runs a server, as against the `render-*.py` pair that writes a file and opens it. The existing two commands stay: both work headless, and their output is what this page frames.

### Why this one has a server when the other two do not

`render-transcript.py` and `render-cost-receipt.py` write self-contained pages and open `file://`. Nothing about this page needs to write, so a server is not obviously justified. Two things justify it:

- **Reuse is by iframe.** The transcript and cost-receipt tabs are the existing pages, framed. Under one origin, that is literal reuse of the shipped implementations with no second parser and no extracted shared module. Between two `file://` documents it is not: the frames load, but the shell cannot script across them, so tab state, deep links into a job, and the git-ignore banner would each need a second implementation.
- **Logs are large and mostly unread.** Observed `log.jsonl` files in this repo run 72 KB to 1.3 MB. Inlining every indexed job into one page is several megabytes of which a reader opens one. Serving each on demand keeps the shell small.

The server is the setup UI's, copied: `ThreadingHTTPServer` bound to `127.0.0.1`, a per-run `secrets.token_urlsafe(24)` in the opened URL, a `Host` header check, and `hmac.compare_digest` on the supplied token. That guard matters more here than in setup. A job's `log.jsonl` holds whatever the worker read; the largest single event in the sampled set was 110 KB of file contents.

Arguments mirror the setup UI: `--repo`, `--port` (default: an available port), `--no-open`.

### Routes, and the identifier guard

Every route is a GET. Nothing outside `.handoff/` is written, and nothing is written at all except the two cached renderings.

| Route | Serves |
| --- | --- |
| `/` | the shell, `assets/session-view.html`, session payload injected by `handoff_runtime.inject` |
| `/diagram.svg` | the SVG bytes for this receipt, when one exists |
| `/transcript/<jobId>` | that job's transcript page, rendered on demand and cached under `.handoff/transcripts/` |
| `/cost-receipt` | the cost receipt HTML, rendered on demand into `.handoff/cost-receipts/` |

`<jobId>` is checked twice before any file is opened: it must be a single safe path segment, and it must appear in the receipt's indexed job set. That is the cost receipt reader's rule, applied to a value now arriving over HTTP rather than out of a file. There is no filesystem walk from a request path.

A cached rendering is served when it exists and is newer than the job's `log.jsonl`; otherwise it is re-rendered, because a running job's log grows.

### Reuse by import, not reimplementation

| Borrowed | From | For |
| --- | --- | --- |
| `load_receipt` | `render-cost-receipt.py` | fail-closed receipt parsing, unchanged |
| `main(["--no-open", ...])` | `render-cost-receipt.py` | the cost receipt rendering |
| `resolve`, `main`, `_is_ignored` | `render-transcript.py` | per-job transcript rendering and the git-ignore check |
| `read_meta`, `job_state`, `inject` | `handoff_runtime.py` | job facts and payload injection |

Loaded through the `importlib` spec pattern `handoff-setup-ui.py` already uses on `handoff-setup.py`, because the filenames carry hyphens. Renderers are called in process with `--no-open` rather than by subprocess: one interpreter, and the existing exit-code and warning behaviour is inherited rather than re-derived.

Only 4 of the 17 job directories in this repo have a rendered transcript, so on-demand rendering is a requirement of the feature, not an optimisation of it.

---

## The overview tab

### The SVG is an image, never DOM

The diagram is served into an `<img>`, with a transparent hotspot layer positioned over it. It is never inserted into the document.

An `<img>` cannot script. Inserting an SVG document into the DOM would reopen the whole question the evidence design's escaping rules exist to answer: a `<script>` element inserted through `innerHTML` does not execute, but an `onload` on an SVG element does. The file is locally generated and low risk, and the cheapest way to keep it that way is to never give it a DOM to run in.

So the page never reads the SVG's internals. **Python parses the root element** with `xml.etree` and puts three values in the payload: `viewBox`, `data-axis`, `data-lanes`. Hotspots are positioned as percentages of the viewBox, and the overlay is sized to the image's own box (`img { width: 100%; height: auto; display: block }`, overlay `inset: 0`), so the mapping holds at any width with no measurement in JavaScript.

### A broken axis needs segments, not endpoints

The reference diagram, `receipt-20260910T022551Z_handoff-session-timeline.svg`, has a broken time axis: 00:30–02:30 drawn at x 270–1240, then 10:00–10:30 at x 1292–1560, with an eight-hour gap between them. A single start/end pair cannot describe that, and hotspots computed from one would land on the wrong lane with no visible symptom. The declared axis is therefore a list of segments, and the lane map a list of bands:

```
data-axis='[{"t0":"2026-09-10T00:30:00+08:00","t1":"2026-09-10T02:30:00+08:00","x0":270,"x1":1240},
            {"t0":"2026-09-10T10:00:00+08:00","t1":"2026-09-10T10:30:00+08:00","x0":1292,"x1":1560}]'
data-lanes='[{"job":"job-2026-09-10T00-37-42-59402-spec-review","y0":300,"y1":362},
             {"lane":"driver","y0":180,"y1":250}]'
```

Time maps to x piecewise. A job's measured window is intersected with each segment; every non-empty intersection produces one rect, and a window falling entirely in a gap produces none and is reported as outside the drawn axis. Nothing is clamped to a nearest edge — a clamped hotspot is a wrong hotspot that looks right.

Hotspots span the full lane band rather than the drawn bar, so a click anywhere in the `deep_reasoner` row opens that job. That is more forgiving of a model's pixel choices than requiring bar-exact geometry, and it is why the lane band is what the diagram declares.

A **show hotspots** toggle outlines them. Misalignment then costs one glance instead of one wrong click, and it is the calibration knob a declarative contract over hand-drawn geometry needs.

Clicking a delegated lane opens that job's transcript tab. Clicking a `driver` lane opens the session facts tab, since the driver's own Claude Code session is not a delegated job and `render-transcript.py` does not render it.

### The fallback ladder

None of the three SVGs currently in `.handoff/receipts/diagram/` carry these attributes, so every rung is a real state on day one:

| State | Overview shows |
| --- | --- |
| SVG present, axis and lanes declared | the image with working hotspots and the toggle |
| SVG present, attributes absent or unparseable | the image, a banner saying the diagram declares no lane map, and the job table beside it |
| no SVG for this receipt | the job table, plus the exact `baoyu-diagram` prompt for this receipt including the data-attribute contract, ready to copy |

The job table is not a second timeline implementation. It is one row per indexed job — window, role, backend, model, effort, state, duration — read from the same `meta` and `exit_code` the hotspot math uses, and it is present in every state as the drill-in that does not depend on a diagram.

### Verification: the job set and the windows

Computed server-side before the page renders, and reported as one banner:

- every `data-lanes` entry names a job the receipt indexes, or it is listed as unknown
- every indexed job has a lane, or it is listed as missing
- every declared lane window matches the measured `meta.submitted_at` and `exit_code` mtime within 60 seconds, the tolerance a diagram that labels minutes needs

"Lane map matches the 2 indexed jobs", or a count and the list. What this catches is the failure this design would otherwise invite: a diagram generated for one run rendering under another run's receipt, which is indistinguishable from a correct page without the check. What it does not catch is a wrong number in the SVG's own prose, by the decision recorded above.

---

## The session facts tab

From `.handoff/goal.md`: Goal, Checkpoint Rule, Spec Review, Delivery, and the `## Tasks` table — id, identity, task, acceptance, depends, effort, status, jobId — with each jobId linking to that job's transcript tab. From the receipt: phase, duration, checks, anomalies, `roles_used` with each entry's `verified` flag as it stands, scope, `config_source`, and the schema version.

`goal.md` is free-form prose written by a driver and a monitor loop, so only the pipe table is parsed structurally. The other sections render as plain text blocks. A table that does not parse shows its raw section rather than an error: this tab is a convenience over a file the user can already open, and it should degrade to showing the file rather than refusing.

Every value reaches the DOM through `textContent`.

---

## The denials and anomalies tab

Denials are read per indexed job: claude's `permission_denials[]` on the last `result`, copilot's typed `tool.execution_complete` carrying `error.code == "denied"`. Codex reports none, and the tab says so rather than printing a zero — the evidence design's rule that a gap is named and never zeroed.

This tab exists because a denied tool call does not move a job's exit code. A job can report success on a check it was refused, and the cost receipt's denial count is the only place that currently surfaces it.

Anomalies are the receipt's own `anomalies:` line plus three derived signals, each read rather than recalled: any job in `FAILED` or `CANCELLED` state, any non-zero exit code, and any non-empty `stderr.log`.

### The export boundary, and where this page's differs

The cost receipt counts denials and never exports the denied command string, because it is a committed, publishable artifact. This page is served on loopback and never committed, and the transcript tab beside it already shows every command in full. Withholding the denied command here would protect nothing, so the tab shows the tool, the command, and the job it came from.

That is a different boundary from the cost receipt's, and it is drawn by a different property: not "what may be published" but "what is already on this page". `render-cost-receipt.py`'s own boundary is unchanged.

The git-ignore check `render-transcript.py` already performs is surfaced in the shell as a banner rather than only on stderr, because a page the user is looking at is where a warning about that page's output belongs.

---

## Where this fails closed, and where it does not

| Guard | Enforced by | What fails without it |
| --- | --- | --- |
| Only this machine connects | `Host` check plus a per-run token, both from the setup UI | job logs, containing read file contents, served to the network |
| A request path cannot escape the jobs directory | single-safe-segment check, then membership in the receipt's indexed set | a page reading arbitrary files over HTTP |
| An SVG for another run cannot render silently | lane-set and window reconciliation | a confident, wrong overview |
| A gap in the drawn axis produces no hotspot | piecewise intersection, no clamping | a click opening the wrong job |
| An undeclared diagram is still usable | the fallback ladder | a feature that works only on diagrams generated after it shipped |
| Receipt parsing has one implementation | `load_receipt` imported, not copied | two readers disagreeing about run membership |
| A stale transcript is not served | cache invalidated against `log.jsonl` mtime | a running job shown as it was |
| A committed transcript is noticed | `_is_ignored`, surfaced in the shell | captured shell output in Git history |

Asserted by prose rather than by a script: that the SVG is a narrative and the tabs are the record. The page carries the label; nothing enforces that a reader believes it.

## Risks and known limits

- **The data-attribute contract is a prompt convention, not a validated interface.** A diagram model that ignores it produces a non-interactive overview. The fallback ladder is the mitigation; there is no way to make a hand-authored file comply.
- **The narrative is unverified where it is most quotable.** Token figures and finding counts in the SVG's subtitle are exactly the numbers a reader will repeat, and the check does not touch them. Deliberate, per the decision above; the mitigation is the label and the measured tabs beside it.
- **No interactive example ships with the feature.** All three existing diagrams predate the contract, so the interactive path is exercised only by fixtures until one is regenerated.
- **`goal.md` parsing is best-effort.** The file has no schema. A reformatted task table degrades to raw text, silently by design.
- **A token in a URL is visible to anything that can read the browser's history or the shell that printed it.** Inherited from the setup UI, which has the same property, and not made worse here.
- **No size cap on the SVG or on a served transcript.** Observed logs are comfortable in one page; a much larger one is untested, the same limit the transcript viewer already carries.
- **The server holds the terminal.** Like the setup UI, it runs until Ctrl-C. A user who closes the tab and keeps working leaves a process bound to a port.

## Not doing

- **Generating the SVG in Python.** No script draws it. `SKILL.md` instructs the driver to offer generation with `baoyu-diagram` when one is missing, and the overview's third rung prints the prompt.
- **A deterministic chart as the overview.** Considered and declined: the editorial content — phase bands, what a spec review found, why an anomaly was recorded — is the reason to look at the page, and none of it exists as structured data. The job table covers the geometry-only case.
- **Checking the SVG's narrative numbers.** Would require the page to establish what the prose means, which is a parser for English.
- **A static shareable bundle.** The transcript and cost receipt pages are already self-contained files, which is the sharing path.
- **Aggregation across runs.** One receipt in, one page.
- **New evidence written during a run.** No receipt schema change, and no edits to `delegate-codex.sh`, `make-receipt.py`, `validate-receipt.py`, or either existing renderer's behaviour.
- **A `references/*.md` flow document.** One command with one optional argument; `SKILL.md` carries it directly, as it does for the other two renderers.

## Verification

```bash
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
bash scripts/check-skill-repo.sh .
python3 scripts/english-only-scan.py
python3 scripts/run-test-prompts.py
```

New coverage in `tests/test_session_ui.py`: piecewise axis mapping across a segment boundary and across a gap; lane reconciliation for an unknown lane, a missing lane, and windows inside and outside the 60-second tolerance; a jobId that is a path segment, one that is not, and one absent from the receipt; token and `Host` guard rejection; SVG root parsing with valid, absent, and malformed attributes; the `goal.md` task table parsed and its raw fallback; denial extraction per backend, including codex reporting none rather than zero.

The existing suites are not modified. If the imports are faithful they pass untouched, which is the same proof the cost receipt plan's first task used for its helper move.

Gates to extend: `check_file` entries for `scripts/handoff-session-ui.py` and `assets/session-view.html`, a `py_compile` entry, and a `test-prompts.json` case for the new command. Version `3.7.2` in `SKILL.md` frontmatter, the README badge, `CHANGELOG.md`, and a new `docs/releases/v3.7.2.md`, bumped together.

`assets/session-view.html` gets an `/interface-kit` pass on the same `light-dark()` token set as `assets/transcript-viewer.html` and `assets/cost-receipt.html`, so the three pages remain one design system: keyboard-reachable tabs with focus rings, scroll containers that scroll inside themselves rather than pushing the page sideways, `prefers-reduced-motion` honoured, and hover behaviour gated behind `(hover: hover)`.

Manual checks no automated check covers: open a session whose diagram declares a lane map and confirm the hotspot outlines sit over the bars; open one whose diagram declares none and confirm the banner and the job table; open one with no diagram and confirm the printed prompt is usable as written.

## Changing any of this

`references/darwin-ratchet.md` is the gate. The dimensions here are: what the overview is allowed to assert, how a click resolves to a job, what the verification checks, and what the two new tabs read. Change one at a time and keep the change only when repo evidence improves.

The dimension most likely to erode is the first. The pressure will be to check less of the diagram, or to let its prose reach the summary of a run, because a narrative overview reads better than a measured one. A change there needs a test that fails when the rule is broken, not a paragraph promising to hold it.
