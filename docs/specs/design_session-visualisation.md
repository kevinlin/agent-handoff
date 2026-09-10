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

- The SVG carries its own narrative label, written into the diagram by the generation prompt, and the image's `alt` names it as hand-authored. The page chrome does not repeat the claim.
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

`<jobId>` is checked three times before any file is opened: it must be a single safe path segment, it must appear in the receipt's indexed job set, and the path built from it must resolve inside `<repo>/.handoff/jobs/`. The renderer is then handed that **canonical absolute directory**, never the bare segment. `render-transcript.resolve()` is a human-facing selector — it accepts `last`, matches a directory relative to the process's cwd *before* it matches an indexed job name, and falls through to suffix and substring matching. Every one of those is a way for an HTTP request to reach a job the receipt does not index, so the server does not call it.

### The response policy differs per route, and the setup UI's cannot be copied

`handoff-setup-ui.py` sends `default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'` with `X-Frame-Options: DENY`. Copied unchanged, that policy blocks every frame and the overview image, so the feature does not render at all. The authentication guard transfers; the response policy has to be written per route.

| Route | `Content-Security-Policy` | Why |
| --- | --- | --- |
| `/` | `default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self'; frame-src 'self'; connect-src 'self'; frame-ancestors 'none'` | the shell frames its own routes and shows its own image, and is itself unframable |
| `/diagram.svg` | `default-src 'none'; style-src 'unsafe-inline'` | **no `script-src` at all** |
| `/transcript/<jobId>`, `/cost-receipt` | `default-src 'none'; style-src 'unsafe-inline'; script-src 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'self'` | framable by the shell only, and no outbound image fetch |

`X-Content-Type-Options: nosniff` and `Cache-Control: no-store` on all of them, as the setup UI already sends.

Two of those rows are load-bearing rather than tidy:

- **An `<img>` restricts an SVG; a URL bar does not.** SVG-as-image rules apply to the image context, not to opening `/diagram.svg` as a document. Under the setup UI's `script-src 'unsafe-inline'` a diagram opened directly would run script on this origin and could read every other authenticated route. Omitting `script-src` entirely is what closes that, and it costs nothing: the page never needs the SVG to be live. It also drops the `@import` of Google Fonts, so the diagram stops reaching out to a third party for a font it already has a fallback for.
- **Loopback binding constrains what can connect in, not what the browser fetches out.** The transcript viewer renders markdown images from agent prose, and a URL in captured output is content an untrusted worker chose. `img-src 'self' data:` on the framed routes is the egress boundary, and it is only available because these pages are now served rather than opened from disk.

**Every subresource URL carries the token.** An `<iframe>` or `<img>` request inherits neither the parent document's query string nor the setup UI's `X-Handoff-Token` header, so `src` values are built as `/transcript/<jobId>?token=…`. Without that the shell opens and every panel inside it is a 403.

### Caching, and jobs that have not finished

A cached rendering is served when it is newer than **every input it was built from**, not just the job's `log.jsonl`: a transcript reads `log.jsonl`, `meta`, `prompt.md`, and `exit_code`, and the cost receipt additionally reads the receipt file, every indexed job's `usage.json`, and the driver's own session transcript. Comparing one file would serve a page that says `RUNNING` for a job that finished a second after its last log write.

Both renderers write to their final path with no temporary file, so two concurrent requests can serve half a page. Renders are serialized behind one lock per output path, held across the render and the read of the bytes that answer the request.

A job the receipt indexes as `running` has no measured end. It is shown as `running` in the job table, contributes no computed window, and its declared lane window is reported `unverifiable (job running)` with its hotspot dropped. No end timestamp is invented for it, per the evidence design's rule that a gap is named and never zeroed.

### Reuse by import, not reimplementation

| Borrowed | From | For |
| --- | --- | --- |
| `load_receipt` | `render-cost-receipt.py` | fail-closed receipt parsing, unchanged |
| `main(["--no-open", ...])` | `render-cost-receipt.py` | the cost receipt rendering |
| `main`, `_is_ignored` | `render-transcript.py` | per-job transcript rendering and the git-ignore check. `resolve` is deliberately **not** borrowed |
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
data-axis='[{"t0":"2026-09-10T00:30:00+08:00","t1":"2026-09-10T02:30:00+08:00","x0":270,"x1":1230},
            {"t0":"2026-09-10T10:00:00+08:00","t1":"2026-09-10T10:30:00+08:00","x0":1300,"x1":1540}]'
data-lanes='[{"job":"job-2026-09-10T00-37-42-59402-spec-review","y0":300,"y1":362,
              "t0":"2026-09-10T00:37:42+08:00","t1":"2026-09-10T00:43:31+08:00"},
             {"lane":"driver","y0":180,"y1":250}]'
```

Those x values are the **tick positions**, 270–1230 and 1300–1540, not the axis strokes' outer endpoints. The strokes overhang the first and last tick, and taking them instead shifts the scale and the origin together, which produces hotspots that are wrong by a consistent amount — the hardest kind to notice.

Each lane declares its own `t0` and `t1`. Without them there is nothing to reconcile: computing a window from measured evidence and then comparing it against itself verifies nothing. The declared window is what the diagram asserts; the measured window is what the files say; the check is between the two.

Time maps to x piecewise. A job's **declared** window is intersected with each segment; every non-empty intersection produces one rect, and a window falling entirely in a gap produces none and is reported as outside the drawn axis. Nothing is clamped to a nearest edge — a clamped hotspot is a wrong hotspot that looks right.

A rect spans the job's time extent horizontally and the **full lane band** vertically. The vertical span is the forgiving part: a model's bar can sit anywhere in its row's height and the hotspot still covers it. It is not a whole-row target — clicking the lane's label, left of the axis, opens nothing.

A lane map is accepted only if all of this holds, and rejected whole otherwise, dropping the overview to the second rung:

- segments ordered, non-overlapping in both `t` and `x`, each with `t1 > t0` and `x1 > x0`, all coordinates finite
- a `viewBox` with positive width and height; a non-zero origin is subtracted before the percentage is taken, since `viewBox="0 0 …"` is a convention and not a guarantee
- at most one lane per job, no two lane bands overlapping, every timestamp offset-aware
- a `lane: "driver"` entry carries no window and is exempt from reconciliation; it is the only entry that may omit `t0`/`t1`

Reconciliation failure is **per lane, not per diagram**. A lane that reconciles keeps its hotspot; one that does not loses it and is named in the banner. A hotspot is never shown for a lane the evidence contradicts, and one bad lane never blanks the rest of the map.

A **show hotspots** toggle outlines them. Misalignment then costs one glance instead of one wrong click, and it is the calibration knob a declarative contract over hand-drawn geometry needs.

Clicking a delegated lane opens that job's transcript tab. Clicking a `driver` lane opens the session facts tab, since the driver's own Claude Code session is not a delegated job and `render-transcript.py` does not render it.

### Both assets exist before the browser opens

A reviewer who lands on an empty overview and an empty cost tab learns nothing about the run, and cannot tell a missing asset from a broken page. So neither is left to be discovered by clicking.

Only one of the two can be made here, and the split is not arbitrary:

- **The cost receipt is Python**, so it is rendered eagerly during startup, before the URL is printed. A receipt that cannot render is an exit 2 and no page at all, because the tab it would produce is a 400.
- **The diagram needs a model.** A renderer that spawned one would bill a vendor as a side effect of opening a page, which is the one thing this flow must never do quietly. So the script refuses instead: **exit 3**, the target path, and the generation prompt on stderr.

Exit 3 is a distinct code from exit 2 on purpose. It tells the caller "an asset is missing and only a model can make it", which is a different instruction than exit 2's "your input is wrong": one is answered by generating and re-running, the other by reading the message. `SKILL.md` binds the first to `baoyu-diagram` and tells the driver to do it without asking.

`--allow-missing-diagram` keeps the third rung reachable. Deleting a working fallback because a happy path now exists would be a regression, and generation is not always available.

### The fallback ladder

None of the three SVGs currently in `.handoff/receipts/diagram/` carry these attributes, so every rung is a real state on day one:

| State | Overview shows |
| --- | --- |
| SVG present, axis and lanes declared | the image with working hotspots and the toggle |
| SVG present, attributes absent or unparseable | the image, a banner saying the diagram declares no lane map, and the job table beside it |
| no SVG for this receipt, `--allow-missing-diagram` passed | the job table, plus the exact `baoyu-diagram` prompt for this receipt including the data-attribute contract, ready to copy |
| no SVG for this receipt, flag absent | nothing: exit 3, the prompt on stderr, no server bound |

The job table is not a second timeline implementation. It is one row per indexed job — window, role, backend, model, effort, state, duration — read from the same `meta` and `exit_code` the hotspot math uses, and it is present in every state as the drill-in that does not depend on a diagram.

### Verification: the job set and the windows

Computed server-side before the page renders, and reported as one banner:

- every `data-lanes` entry names a job the receipt indexes, or it is listed as unknown
- every indexed job has a lane, or it is listed as missing
- every lane's **declared** `t0`/`t1` matches the **measured** `meta.submitted_at` and `exit_code` mtime within 60 seconds, the tolerance a diagram that labels minutes needs
- a lane whose job is still running is reported `unverifiable (job running)` rather than passed or failed

"Lane map matches the 2 indexed jobs", or a count and the list. What this catches is the failure this design would otherwise invite: a diagram generated for one run rendering under another run's receipt, which is indistinguishable from a correct page without the check. What it does not catch is a wrong number in the SVG's own prose, by the decision recorded above.

---

## The session facts tab

From the receipt: phase, duration, checks, anomalies, `roles_used` with each entry's `verified` flag as it stands, scope, `config_source`, and the schema version. Those belong to the selected receipt by construction.

`.handoff/goal.md` does not. There is exactly one goal file, it lives in the working tree, and it describes whatever run is current — which is normally **not** the run an older receipt indexes. In this repo today the newest receipt indexes the v3.7.1 permission-mode work while `goal.md` describes the v3.7.2 visualisation feature, so a tab that read both and printed them together would produce a confident, coherent, wrong account of a run. That is worse than showing nothing.

So the association is established, not assumed: the jobIds in the goal file's `## Tasks` table are intersected with the jobIds the receipt indexes.

| Intersection | Session facts shows |
| --- | --- |
| non-empty | the goal file as this run's, with Goal, Checkpoint Rule, Spec Review, Delivery and the task table, each jobId linking to its transcript tab |
| empty | "no goal file matches this receipt" — the working tree's `goal.md` is reachable behind an explicit disclosure labelled as the current goal, never presented as this run's |

`goal.md` is free-form prose written by a driver and a monitor loop, so only the pipe table is parsed structurally. The other sections render as plain text blocks. A table that does not parse shows its raw section rather than an error, and the intersection is then empty, so the file is labelled rather than attributed.

`load_receipt` validates run membership and the fields the cost receipt reads; it does not validate everything this tab consumes. A malformed `roles_used` renders as `not recorded` for that row rather than an empty list, which would read as "no roles were used".

Every value reaches the DOM through `textContent`.

---

## The denials and anomalies tab

Denials are read per indexed job: claude's `permission_denials[]` on the last `result`, copilot's typed `tool.execution_complete` carrying `error.code == "denied"`. Codex reports none, and the tab says so rather than printing a zero — the evidence design's rule that a gap is named and never zeroed.

A copilot denial event carries the call id and the error, and nothing else. The tool name and its arguments are on the matching `tool.execution_start`, so the command column exists only if the two are joined on `toolCallId` — the join `assets/transcript-viewer.html` already performs for its own rows. A denial whose start event is absent is kept with its details reported unavailable, never dropped for being incomplete.

A claude job with no terminal `result` has not reported denials rather than reported none. It renders `not yet available`, the distinction `render-cost-receipt.py` already preserves.

This tab exists because a denied tool call does not move a job's exit code: a job can report success on a check it was refused. The transcript already labels an individual denied call in its own row, and the cost receipt already carries a count. What neither gives is one list across every job in the run, which is the form the question "was anything blocked in this run" actually takes.

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
| A stale transcript is not served | cache invalidated against every input's mtime | a running job shown as it was |
| A half-written page is not served | one render lock per output path | a truncated transcript |
| A diagram cannot script on this origin | no `script-src` on `/diagram.svg` | an SVG opened directly reading every other route |
| Captured output cannot fetch outward | `img-src 'self' data:` on the framed routes | a URL in worker output phoning home when a transcript is opened |
| A goal file is not attributed to a run it does not belong to | jobId intersection with the receipt's index | a coherent, wrong account of a run |
| A declared window is checked against a measured one | `t0`/`t1` on every lane | a verification that compares a value to itself |
| A committed transcript is noticed | `_is_ignored`, surfaced in the shell | captured shell output in Git history |

Asserted by prose rather than by a script: that the SVG is a narrative and the tabs are the record. The generation prompt asks the diagram to say so itself; nothing enforces that it complies, or that a reader believes it.

## Risks and known limits

- **The data-attribute contract is a prompt convention, not a validated interface.** A diagram model that ignores it produces a non-interactive overview. The fallback ladder is the mitigation; there is no way to make a hand-authored file comply.
- **The narrative is unverified where it is most quotable.** Token figures and finding counts in the SVG's subtitle are exactly the numbers a reader will repeat, and the check does not touch them. Deliberate, per the decision above; the mitigation is the label and the measured tabs beside it.
- **One real generation has now satisfied the contract.** The diagram for `receipt-20260910T073002Z`, generated from the printed prompt outside the implementing session, parsed and reconciled on the first attempt: `Lane map matches the 3 indexed jobs`, four hotspots. That closes the open question of whether a diagram model can emit well-formed `data-axis` and `data-lanes` at all. It is one sample, so it does not establish a rate, and the three older diagrams still predate the contract.
- **The declared window is checked; the drawn bar is not.** Reconciliation proves the diagram's *claims* match the files. It cannot prove the bar was drawn where the claim says, so a diagram that declares correct windows and draws them elsewhere passes the check and misplaces its hotspots. The **show hotspots** toggle is the only thing that catches it, and it needs a human to look.
- **`goal.md` parsing is best-effort.** The file has no schema. A reformatted task table degrades to raw text, silently by design.
- **A token in a URL is visible to anything that can read the browser's history or the shell that printed it.** Inherited from the setup UI, which has the same property, and not made worse here.
- **No size cap on the SVG or on a served transcript.** Observed logs are comfortable in one page; a much larger one is untested, the same limit the transcript viewer already carries.
- **The server holds the terminal.** Like the setup UI, it runs until Ctrl-C. A user who closes the tab and keeps working leaves a process bound to a port.

## Not doing

- **Generating the SVG in Python.** No script draws it. The script requires one, names the target path, and hands over the prompt; `SKILL.md` binds that to `baoyu-diagram` and makes generating it automatic rather than offered. The prompt also carries the presentation requirements the page depends on and a model would otherwise choose against: a light theme (`baoyu-diagram` defaults to dark), a 12px minimum font size, clock labels in the local timezone, and one sub-timeline per identity forked from the driver lane.
- **A deterministic chart as the overview.** Considered and declined: the editorial content — phase bands, what a spec review found, why an anomaly was recorded — is the reason to look at the page, and none of it exists as structured data. The job table covers the geometry-only case.
- **Checking the SVG's narrative numbers.** Would require the page to establish what the prose means, which is a parser for English.
- **A static shareable bundle.** The transcript and cost receipt pages are already self-contained files, which is the sharing path.
- **Aggregation across runs.** One receipt in, one page.
- **New evidence written during a run.** No receipt schema change, and no edits to `delegate-codex.sh`, `make-receipt.py`, `validate-receipt.py`, or either existing renderer's behaviour.
- **A `references/*.md` flow document.** One command with one optional argument; `SKILL.md` carries it directly, as it does for the other two renderers.

## Verification

The acceptance is the full CI set in `.github/workflows/checks.yml`, not a subset of it:

```bash
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
bash scripts/check-skill-repo.sh .
python3 scripts/english-only-scan.py
python3 scripts/run-test-prompts.py
bash -n scripts/delegate-codex.sh
python3 -m py_compile scripts/*.py
bash install.sh --dry-run
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

The new script's `py_compile` entry goes in `checks.yml`, which is where that list lives; `check-skill-repo.sh` has no compilation list and does not gain one.

New coverage in `tests/test_session_ui.py`:

- piecewise axis mapping across a segment boundary, across a gap, and under a non-zero `viewBox` origin
- lane reconciliation: unknown lane, missing lane, windows inside and outside the 60-second tolerance, a running job reported unverifiable, and one bad lane leaving the others' hotspots intact
- lane-map rejection: unordered or overlapping segments, `x1 <= x0`, a non-finite coordinate, a duplicate job, overlapping bands, a naive timestamp — each dropping the overview to the second rung
- a jobId that is a path segment, one that is not, one absent from the receipt, and one naming a directory that exists in the process cwd — the last asserting the server does not use `resolve()`
- token and `Host` guard rejection, and the per-route header policy asserted route by route, including the absence of `script-src` on `/diagram.svg`
- a subresource URL carrying its token
- SVG root parsing with valid, absent, and malformed attributes
- goal-to-receipt intersection: matched, unmatched, and an unparseable task table
- denial extraction per backend: the copilot `toolCallId` join, a denial whose start event is missing, a claude job with no terminal result reading `not yet available`, and codex reporting none rather than zero
- a malformed `roles_used` reading `not recorded`

The existing suites are not modified. If the imports are faithful they pass untouched, which is the same proof the cost receipt plan's first task used for its helper move.

Gates to extend: `check_file` entries for `scripts/handoff-session-ui.py` and `assets/session-view.html`, a `py_compile` entry, and a `test-prompts.json` case for the new command. Version `3.7.2` in `SKILL.md` frontmatter, the README badge, `CHANGELOG.md`, and a new `docs/releases/v3.7.2.md`, bumped together.

`assets/session-view.html` gets an `/interface-kit` pass. The two existing pages both use `light-dark()` but define **different token names and different palette values**, so there is no single shared set to join; the new page follows the cost receipt's editorial language, and neither existing page is restyled to match it. What it does carry over is the behaviour: keyboard-reachable tabs with focus rings, scroll containers that scroll inside themselves rather than pushing the page sideways, `prefers-reduced-motion` honoured, and hover gated behind `(hover: hover)`.

All three diagrams currently in `.handoff/receipts/diagram/` predate the lane contract, so opening the repo's own newest receipt exercises the **second** rung only. Reaching the other two takes prepared inputs, and the manual check names them:

| Rung | Input |
| --- | --- |
| interactive | the newest receipt's diagram, hand-annotated with `data-axis` and `data-lanes` by the driver in Phase 4; `tests/fixtures/session-view/annotated-timeline.svg` covers the same path in tests |
| no lane map | any of the three diagrams as they stand |
| no diagram | a receipt whose stamp has no file in `.handoff/receipts/diagram/` |

Manual checks no automated check covers: with the annotated diagram, switch on **show hotspots** and confirm the outlines sit over the bars rather than beside them; on the unannotated one, confirm the banner and the job table; with no diagram, confirm the printed generation prompt is usable as written. Then open `/diagram.svg` directly in a tab and confirm it renders as an inert image, and confirm each framed tab loads rather than 403s.

## Changing any of this

`references/darwin-ratchet.md` is the gate. The dimensions here are: what the overview is allowed to assert, how a click resolves to a job, what the verification checks, and what the two new tabs read. Change one at a time and keep the change only when repo evidence improves.

The dimension most likely to erode is the first. The pressure will be to check less of the diagram, or to let its prose reach the summary of a run, because a narrative overview reads better than a measured one. A change there needs a test that fails when the rule is broken, not a paragraph promising to hold it.
