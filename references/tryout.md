# Agent Handoff Tryout (`/agent-handoff tryout`)

The first-run proof pass. Triggered by `/agent-handoff tryout` after `/agent-handoff config` has been applied. Each configured identity runs one small, self-contained micro-task; the result is a report that lets a first-time user conclude in one glance: my identities are actually live, on the models I chose. This is a real end-to-end run (it spends real quota, minutes not seconds at high effort tiers). `handoff-setup.py --smoke` is the bounded installation check: Codex identities use the delegate dry-run chain, each Claude identity uses one minimal tool-free fresh session, and each Copilot identity adds one no-tool `copilot -p` run that asks the CLI whether the configured model-and-effort pair is usable on this account — so model/effort/auth are genuinely checked on every backend. The tryout remains the proof that every configured identity can complete its intended work, not merely answer the installation probe.

## The three micro-tasks

Fixed content, independent of the target repo's state. Run each through `delegate-codex.sh submit --role <identity>`; the identity's configured backend picks the CLI — codex, claude, or copilot — and the row reports whichever one ran:

1. **fast_worker — mechanical**: "Sort the keys of this JSON object alphabetically at every nesting level and return only the formatted result: `{"b":{"z":1,"a":{"c":3}},"a":[2,1],"c":"x"}`" Pass = returns exactly the correctly sorted, valid JSON, nothing else.
2. **deep_reasoner — reasoning**: "A CLI tool stores per-project config. A is one dotfile per project in the repo (committed); B is one central file in the user's home keyed by project path. Name the decisive tradeoff and pick one for a tool whose users frequently rename and move project directories. Conclusion first, then at most three sentences of reasoning." Pass = takes a clear position and the reasoning addresses the directory-move consequence (B's key breaks on move / A travels with the repo).
3. **arbiter — blind cross-check**: the same question as task 2, sent verbatim, with zero mention of deep_reasoner or its answer (the contamination rule in `references/claude-driven.md`'s Arbiter Protocol applies in full). Pass = an independent position; agreement or a documented divergence are both healthy outcomes — the point is proving the second, independent channel works.

## The tryout report (fixed format)

```text
[Handoff tryout report]
identity       backend  model         effort  elapsed  result
fast_worker    codex    gpt-5.6-sol   medium  28s      PASS
deep_reasoner  claude   opus          high    1m42s    PASS
arbiter        codex    gpt-5.6-sol   xhigh   2m10s    PASS (agrees with deep_reasoner: B)
verdict: all identities live
```

- `elapsed` is measured, not estimated. High-effort tiers legitimately take minutes; report reality.
- `result` for the arbiter row states agreement or names the divergence in a few words. A divergence is not a failure.
- A failed row states the actual error (CLI missing, model rejected, timeout) and the fix pointer — rerun `/agent-handoff config` or install the missing CLI. `verdict` then lists which identities are live and which are not; never report `all identities live` on a partial pass.
- After each passing row, write `verified=true` + `verified_at` back to the config via `python3 "$HANDOFF_DIR/scripts/handoff-config.py" set --role <identity> --verified --verified-at <utc>` (goes through the same lock as every config write).
- Close the session with a normal Handoff Session Receipt; the three rows become its `roles_used` entries with `verified: true`.

## Rules

- Never substitute a different model to make a row pass, and never move a row to another backend to get past a rejection; a failing row fails visibly (no silent fallback — same principle as setup). A copilot row that fails on an unsupported model-and-effort pair reports the CLI's own message, which names the pair.
- Do not add project files, commits, or state: all three tasks are answer-only. The only writes are config `verified` flags and the `.handoff/receipts/` entry.
- If the user has not run `/agent-handoff config` yet, say so and route them there first instead of improvising unconfigured identities.
