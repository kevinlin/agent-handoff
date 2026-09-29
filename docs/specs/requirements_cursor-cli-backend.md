# Requirements: Cursor CLI as the fourth backend

Target version: **3.9.0**. Receipt schema: **v7**.

Design: [design_cursor-cli-backend.md](design_cursor-cli-backend.md). Facts: [../research/cursor-cli-specification.md](../research/cursor-cli-specification.md), probed on Cursor CLI build `2026.09.28-64d2043`.

## Why

A user with a Cursor subscription gets a fourth meter to delegate onto. Through that one meter they also reach models the other three backends don't offer: Composer, Grok, Kimi, Gemini, and GLM, alongside Claude and GPT variants.

The bar is the one Copilot met in v3.7.0, and the one `docs/specs/design_agent-handoff.md` states as an invariant: **all backends are the same job**. A cursor-backed row gets the same jobId, job directory, monitor loop, bounded `resume`, worktree lifecycle, and receipt evidence as the other three. Where Cursor differs, the difference stays inside the scripts and never becomes a branch in the flow.

## Scope

In: routing and config, the delegation primitive, permission posture, session and resume, monitoring, setup (terminal and web), smoke, the session receipt, the cost receipt, the session view, the transcript viewer, prose, and tests.

Out: see *Out of scope* at the end.

## Requirements

Each requirement has an acceptance check. "Refused" always means before a job directory exists, with a message that names the fix.

### R1. Routing and configuration

**R1.1** `cursor` is a fourth accepted `backend` value in the config engine, both setup surfaces, and `submit`. `schema_version` stays `2`.
Acceptance: `handoff-config.py set --role fast_worker --backend cursor --model claude-opus-5-5-high --effort model` writes a config that validates; an unknown backend is still refused.

**R1.2** A cursor identity's `model` is a Cursor catalogue slug, and its `effort` is exactly `model`. Any other effort is refused at setup and at submit, and the message says Cursor carries effort in the model id.
Acceptance: a cursor identity with `effort = "high"` is refused by `handoff-setup.py` and by `delegate-codex.sh submit --role`.

**R1.3** `model = "auto"` is refused for a cursor identity at setup and at submit, on the same grounds as Copilot: an identity is a deliberate choice.
Acceptance: both refusals exist, and each names `handoff-config.py set` as the fix.

**R1.4** A per-job `--backend` that contradicts a role configured as `cursor`, or a cursor `--backend` that contradicts another role, is refused, as it is today for the other backends.

**R1.5** No preset names a cursor identity. Cursor is chosen through custom mode or the web wizard.

### R2. Launching a job

**R2.1** The worker binary resolves from `HANDOFF_CURSOR_BIN`, otherwise from `cursor-agent` on PATH. It never resolves from `cursor` (the IDE launcher) or `agent`. Submit, resume, and setup's availability check use the same rule.
Acceptance: with a fake `cursor` earlier on PATH and a fake `cursor-agent` later, the job's `meta` records the `cursor-agent` path. With neither installed, submit fails and names `HANDOFF_CURSOR_BIN`.

**R2.2** A fresh job's generated `run.sh` changes into the working directory and runs `cursor-agent -p "$PROMPT" --output-format stream-json --trust --workspace "$WORKDIR" --model "$MODEL"`, plus the posture flag (R3), with stdin from `/dev/null`, stdout to `log.jsonl`, and stderr to `stderr.log`.

**R2.3** A resume's `run.sh` changes into the parent's working directory and runs `cursor-agent -p "$PROMPT" --output-format stream-json --trust --resume "$SESSION_ID"`, plus the posture flag, with no `--model` and no `--workspace`.

**R2.4** No generated cursor command line contains `--worktree`, `--sandbox`, `--approve-mcps`, `--api-key`, `--stream-partial-output`, or `--yolo`.

**R2.5** Worktree rows behave identically on cursor: base pinned to an immutable SHA before the job directory exists, fix rounds inherit the tree, and `cleanup` refuses a dirty tree.
Acceptance: the shared `BackendLifecycle` tests pass for a cursor subclass.

### R3. Permission posture

**R3.1** A read-only job passes `--mode plan` and never `--force`. <!-- risk-ok: Cursor CLI flag name -->

**R3.2** `permission_mode = "default"` and `permission_mode = "allow-all"` both pass `--force`. The docs state plainly that the posture field changes nothing on Cursor. Explicit deny rules in the user's or project's Cursor config are the only narrowing. <!-- risk-ok: Cursor CLI flag name -->

**R3.3** Job `meta` records the requested `permission_posture` and its source, as today, and the effective `permission_mode` as `force` or `plan`.

**R3.4** Every cursor job that is not read-only prints the bypass warning. For cursor, the remedy line names deny rules in `.cursor/cli.json`, or `--read-only` for a job that must not write.

**R3.5** Handoff writes nothing to `.cursor/cli.json`, `~/.cursor/`, or a relocated `CURSOR_CONFIG_DIR`.

### R4. Session and resume

**R4.1** The session id is read from the job log by the existing extractor. Cursor puts `session_id` on every event from line one. Nothing is pre-assigned.

**R4.2** `resume` continues the parent's session on the parent's backend, worktree, posture, and `read_only`, and the review-round caps apply unchanged.

**R4.3** A cursor job killed after its first event can be resumed. One killed before any event is refused with the existing "no session id" error.
Acceptance: a fixture log ending mid-run with no `result` event resumes and keeps the session id.

### R5. Monitoring and result

**R5.1** `status` reads the last event type and the denial count in its one parsed pass. A cursor denial is a `tool_call` event with subtype `completed` whose result has a `rejected` key.

**R5.2** `result` on a cursor job:
- the agent message is the text of the **last `assistant` event**, never `result.result`, which concatenates every assistant message;
- commands come from `shellToolCall.args.command` on `tool_call/started`;
- denied tools are named by their tool kind;
- usage is the last `result` event's `usage`;
- `thinking` events are skipped;
- the `errors` list is present in `--json` output, as on every backend.

**R5.3** A non-zero denial count is reported the way it is for claude and copilot. `permission_denied` stays advisory; the job's state still derives from its exit code.

### R6. Setup and smoke

**R6.1** Setup reports cursor as available exactly when R2.1's resolution succeeds.

**R6.2** The web wizard's cursor model list comes from `cursor-agent models` at page load, one option per `slug - Display name` line, with `auto` dropped. When the CLI is missing, not logged in, or times out, no cursor model is offered and the page names the fix (`cursor-agent login`, or `HANDOFF_CURSOR_BIN`). No model is ever guessed.

**R6.3** In both wizards, a cursor identity's effort is `model`, set automatically and not editable.

**R6.4** A configured slug missing from today's catalogue stays in the form, marked, and is never silently replaced.

**R6.5** Smoke checks a cursor identity with the existing dry-run chain, then one tool-free `cursor-agent -p … --mode ask` run on the configured slug. A refused slug fails with Cursor's own stderr, verbatim. A pass writes `verified = true`.

**R6.6** The terminal wizard offers `cursor` as a backend.

### R7. Evidence

**R7.1** The session receipt goes to **v7**: `cursor_jobs` and `cursor_job_durations` are required fields, `receipt_schema_version` is `7`, and `roles_used[].host` accepts `cursor`.

**R7.2** `make-receipt.py` splits jobs four ways by the `backend=` line in `meta`. `validate-receipt.py` accepts v7 and rejects v6. `render-cost-receipt.py` reads v7.
Acceptance: a repo with one job per backend produces a receipt with four correct counts, which both `validate-receipt.py` and `render-cost-receipt.py` accept.

**R7.3** The cost receipt folds a cursor job as follows:
- token counters come from the last `result.usage`;
- reasoning tokens are unknown;
- the USD figure is `None`;
- denials count `rejected` results;
- the model is the `system/init` display name;
- a job with no `result` event has unknown usage, never zero;
- the usage of a parent and its fix round is summed.

**R7.4** **No backend's log is read by another backend's parser.** The Claude branch of `fold_usage` and the Copilot branch of the session view's `denials()` become explicit. A cursor job then cannot reach either one.
Acceptance: a cursor job with two rejections reports 2 in the cost receipt and in the session view, not 0.

**R7.5** The cost receipt's summary names jobs on the Cursor meter and says Cursor reports tokens but no cost figure. The denials sentence names claude, copilot, and cursor, in the Markdown and in `assets/cost-receipt.html`.

**R7.6** The transcript viewer renders a cursor log:
- the branch is chosen by `meta.backend`;
- `thinking` events are dropped;
- `tool_call` started and completed fold on `call_id` into one row with the outcome (`success`, `rejected`, or `spawnError`); the ids contain an embedded newline;
- `assistant` becomes an agent row;
- `system/init` and `result` become lifecycle rows, init showing the model that ran.

A dropped log with no metadata is identified as cursor by a `tool_call` or `thinking` event. That check runs before any check that selects claude. It never uses `system`, `assistant`, or `result`.

### R8. Documentation and version

**R8.1** The version is 3.9.0 everywhere it appears (SKILL.md frontmatter, README badges, CHANGELOG, `docs/releases/v3.9.0.md`), bumped together.

**R8.2** SKILL.md's description names all four backends. Two-backend and three-backend lists are swept from `references/*.md`, README, the user guide, text-only diagram labels, and `CLAUDE.md`.

**R8.3** `docs/specs/design_agent-handoff.md` and `docs/specs/design_agent-identities-and-config.md` are updated after implementation to describe what shipped: the permission table, the effort enums, the fail-closed table, and the risks.

**R8.4** The flow prose states the Cursor-specific limits where a driver will read them:
- the posture no-op (R3.2);
- the `Co-authored-by: Cursor` trailer that Cursor's own attribution setting adds to worker commits;
- `(NO ZDR)` in a model's display name means no zero data retention.

**R8.5** `bash scripts/check-skill-repo.sh .` adds no new warning lines. Every line that names Cursor's `--force` carries a `risk-ok` marker. <!-- risk-ok: Cursor CLI flag name -->

### R9. Verification

**R9.1** Everything `.github/workflows/checks.yml` runs passes, including the ledger reproducibility check.

**R9.2** The tests assert what the worker can actually do, not only which flags were passed:
- the posture flags per posture, and their absence under read-only;
- the flags that must never appear (R2.4);
- the IDE-launcher trap (R2.1);
- the killed-job fixture (R4.3);
- the denial count, and the no-fall-through checks (R7.4);
- agent-message selection (R5.2).

**R9.3** A live end-to-end run on a real `cursor-agent`, in a scratch repo with a cursor-backed identity, confirms:
- one expected edit on disk;
- one named repo check that actually ran, with its output in `log.jsonl`;
- `meta` recording `backend=cursor`;
- a `resume` on the same session, with its usage counted once;
- a `--worktree` job that pins its base and cleans up;
- a receipt counting the job under `cursor_jobs`;
- a cost receipt showing its tokens.

## Out of scope

- Writing Handoff-owned permissions into `.cursor/cli.json` or a relocated `CURSOR_CONFIG_DIR`.
- Any `--sandbox` flag, either way.
- `create-chat` pre-assignment, and the bracket effort form.
- A catalogue check at submit, and a `--version` identity check.
- A Cursor preset.
- A cost figure. Cursor reports none, and one must not be made up.
- Stripping Cursor's commit trailer.
- Consolidating event parsing into one module. That is a follow-up, recorded in the design.

## Open questions carried into implementation

- **Mid-run API failure shape.** Not provoked in probing. Implementation should provoke one (an exhausted quota or a revoked login) or record it as unknown, and the monitor keeps reading both `log.jsonl` and `stderr.log`.
- **Running without `--trust`.** Not probed. `--trust` is always passed, so this only matters if a later Cursor build changes what it does.
