# Identity tryout

`/agent-handoff tryout` proves that each of the three core identities is live on the model the user chose. It does not exercise the optional e2e pair: when those are configured, say so in the report. It runs after `/agent-handoff config` has been applied. With no config yet, say so and route the user there instead of improvising identities.

Setup's smoke test only probes the installation. The tryout is a real run in which each identity completes one small task, so it spends real quota and can take minutes at a high effort.

Before submitting anything:

1. Resolve the config: `python3 "$HANDOFF_DIR/scripts/handoff-config.py" --repo "$REPO" resolve`.
2. Read `references/backends/<backend>.md` for each backend it uses, and run each file's preflight check.
3. Stamp the run, so the closing receipt has a start to measure from: `python3 "$HANDOFF_DIR/scripts/make-receipt.py" --start --repo "$REPO"`.

## The three micro-tasks

The content is fixed and does not depend on the target repo. Submit each with `delegate-job.sh submit --role <identity>`, so the identity's configured backend runs it. The tryout writes no goal file and needs no loop: the three jobIds are the ones `submit` printed, and each job is short enough to wait on with `delegate-job.sh status <jobId> --repo "$REPO" --wait --timeout 300`. A job still running after two such waits with no new event in its `log.jsonl` is stalled. Judge each job by the anomaly and `permission_denied` rules in Phase 3 of `references/delegation-flow.md`, and collect it as the first two steps of Phase 4 say. A failed, stalled or permission-denied job fails its row, and the anomaly goes in the receipt.

1. **fast_worker, mechanical.** "Sort the keys of this JSON object alphabetically at every nesting level and return only the formatted result: `{"b":{"z":1,"a":{"c":3}},"a":[2,1],"c":"x"}`" Pass: exactly the correctly sorted, valid JSON and nothing else.
2. **deep_reasoner, reasoning.** "A CLI tool stores per-project config. A is one dotfile per project in the repo (committed); B is one central file in the user's home keyed by project path. Name the decisive tradeoff and pick one for a tool whose users frequently rename and move project directories. Conclusion first, then at most three sentences of reasoning." Pass: a clear position whose reasoning addresses the directory move (B's key breaks on a move, A travels with the repo).
3. **arbiter, blind cross-check.** The same question as task 2, verbatim. The packet must not mention deep_reasoner, its answer, or a hint of which way it leans: a packet that leaks any of them produces fake agreement, so rerun the row. Pass: an independent position. Agreement and a documented divergence are both healthy outcomes, because the point is that the second channel works.

## The report

```text
[Handoff tryout report]
identity       backend  model         effort  elapsed  result
fast_worker    codex    gpt-5.6-sol   medium  28s      PASS
deep_reasoner  claude   opus          high    1m42s    PASS
arbiter        codex    gpt-5.6-sol   xhigh   2m10s    PASS (agrees with deep_reasoner: B)
verdict: all identities live
```

- `elapsed` is measured, never estimated.
- The arbiter row states agreement or names the divergence in a few words. A divergence is not a failure.
- A failed row states the actual error (CLI missing, model rejected, timeout) and the fix: rerun `/agent-handoff config`, or install the missing CLI. A copilot row that fails on an unsupported model-and-effort pair reports the CLI's own message, which names the pair. `verdict` then lists which identities are live and which are not. Never report `all identities live` on a partial pass.
- Never substitute a model to make a row pass, and never move a row to another backend to get past a rejection. A failing row fails visibly.
- After each passing row, record it in the config file that defines the identity: `python3 "$HANDOFF_DIR/scripts/handoff-config.py" --repo "$REPO" --scope <scope> set --role <identity> --verified --verified-at <utc>`. The scope is `project` when `handoff-config.py --repo "$REPO" --scope project get <identity>.backend` prints a backend, and `global` when that command fails (`key not found`). The `source` line of `resolve` names the top layer only, not where each identity lives.
- The tasks are answer-only. The tryout adds no project files and no commits. It writes only under `.handoff/` (the start marker, the job evidence, the receipt) and the `verified` flags in the config.
- Close with a Handoff Session Receipt (`references/session-receipt.md`). The three rows become its `roles_used` entries, with `verified` read from the config as the receipt rules say. The tryout's own PASS or FAIL for each row is in the report.
