# Session evidence commands

Four commands read or move the evidence a run saved under `<repo>/.handoff/`. None of them delegates anything or starts a job. Each is one script: run it, relay what it prints, and act on its exit code. The three that open a page take `--no-open`, which prints the path or URL instead.

## Transcript

`/agent-handoff transcript [<pid|jobId|folder>]`, or a request to see a job's transcript or conversation history:

```bash
python3 "$HANDOFF_DIR/scripts/render-transcript.py" [<selector>] --repo "$REPO"
```

It renders the job's `log.jsonl` as one HTML page and opens it. With no selector, or `last`, it takes the newest job. A folder path, an exact jobId, a pid and a label all resolve. When more than one job matches (a label and its `-r2` fix round, for instance) the script lists the candidates and exits. Run it again with one of the printed names.

## Cost receipt

`/agent-handoff cost-receipt [<receipt-file>]`, or a question about what a run cost or consumed:

```bash
python3 "$HANDOFF_DIR/scripts/render-cost-receipt.py" [<receipt-file>] --repo "$REPO"
```

With no argument, or `last`, it reads the newest saved receipt under `.handoff/sessions/` and writes a Markdown page and an HTML page beside it, in that session's folder.

Every number on it is measured, and each backend reports in its own unit:

| Backend | What its jobs carry |
|---|---|
| codex | token counters and no cost figure, because the work ran on a subscription |
| claude | the CLI's own `total_cost_usd`, quoted unrounded and labelled CLI-reported |
| copilot | token counters, premium requests and nano-AIU. These are AI credits, never a currency figure |
| cursor, antigravity | token counters and no cost figure |

The summary figures overlap by design, so never add them. No counter on this page establishes a saving.

## Session page

`/agent-handoff visualise [<receipt-file>]` (also `visualize`), "visualise the session", or "show me the session timeline":

```bash
python3 "$HANDOFF_DIR/scripts/handoff-session-ui.py" [<receipt-file>] --repo "$REPO"
```

With no argument it takes the newest saved receipt. It serves one page on `127.0.0.1` behind a per-run token: a timeline overview, the transcripts, the cost receipt, session facts and denials. `--port` picks the port, and `--no-open` prints the URL instead of opening it. The server runs until Ctrl-C, so keep the terminal open. It writes only cached transcript and cost-receipt pages, under `.handoff/`.

The overview is a hand-authored SVG timeline, a narrative illustration whose prose nothing verifies. The other tabs are the measured record. Drawing the SVG needs a model, so the script cannot do it. Act on what it reports:

- **Exit 3: no diagram for this receipt.** Nothing opened. The script printed the target path and the generation prompt, and that prompt carries the whole drawing and lane-map contract. Generate the SVG with the `baoyu-diagram` skill (with `baoyu-image-gen` if that is the one installed, or write the SVG directly if neither is), save it at the printed path, and run the command again. Do this without asking: it writes one gitignored file and starts no job.
- **After any new or changed diagram,** check that the page's banner reconciles, and use **Show hotspots** to check that the lanes line up with the drawing. A lane map can be valid and still disagree with the picture.
- **Exit 0 with "declares no lane map".** The page is open and usable. It shows the image and the job table, without hotspots. Ask the user before spending anything, and say the page already works. Offer the cheap fix first: annotate the existing SVG in place, by reading its tick `x` positions and lane band `y` bounds from the file and adding `viewBox`, `data-axis` and `data-lanes` to the root. Redraw from the printed prompt only when the file has no readable geometry or the user asks for a new diagram. Then run the command again.
- **Exit 2.** A bad receipt, a bad selector, or a cost receipt that cannot render. Read the message. Do not retry unchanged.
- `--allow-missing-diagram` opens the page on its job table alone, for when generation is unavailable or the user wants the page as it stands.

## Migrate

`/agent-handoff migrate [<repo>]`, or a request to move a repo's evidence out of the pre-3.11.0 folders (`.handoff/receipts/`, `cost-receipts/`, `transcripts/`) into `.handoff/sessions/`. The repo defaults to the current one.

1. Run the plan. It writes nothing:

   ```bash
   python3 "$HANDOFF_DIR/scripts/migrate-sessions.py" --repo "$REPO"
   ```

2. Show the user its `summary:` line and every `rewrite`, `skip` and `leave` line.
3. Apply it. The command was the go-ahead:

   ```bash
   python3 "$HANDOFF_DIR/scripts/migrate-sessions.py" --repo "$REPO" --apply
   ```

Then act on the exit code:

- **0:** done. Run each printed `re-render:` command exactly as printed. Those cost receipts were rendered before their receipt got its session id.
- **1:** something was skipped or left in place. Report each line and stop. Never move, rename or delete a reported file by hand.
- **2:** no `.handoff/` there, or a bad argument. Say so; there is nothing to migrate.

A repo with no legacy folders plans nothing and exits 0. The command moves gitignored evidence only.
