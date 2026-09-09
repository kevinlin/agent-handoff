# Support Claude Code → Claude Code handoff

## Context

In a real run from `/Users/keli/dev/rv-car`, the driver hit a `fast_worker` identity configured
with `backend = claude`, was refused by `delegate-codex.sh`, and re-routed the work to a
**different identity** (`deep_reasoner`) purely because that one was Codex-backed. It said so:

> Since the point of the handoff is running on Codex's subscription rather than Claude's,
> I sent both jobs to deep_reasoner — the only Codex identity with high effort.

That is the config being overridden by the driver's own cost reasoning. Handoff is not only a
cost split — a second reason to hand work off is to keep the driver's context window clean of
unrelated execution history. CC → CC must be a first-class path, and the flow must follow the
repo's configuration rather than re-deciding the vendor per run.

Outcome: an identity's configured `backend` decides which CLI runs the job, always. A
claude-backed row becomes a real background job with the same jobId, monitoring loop, fix-round
`resume`, worktree lifecycle, and receipt evidence a codex-backed row gets today. **Every
handoff flow behaves the same on either backend, and the tests prove it rather than the prose
claiming it.**

## Root cause (verified by reading the flow end to end)

Four layers, only one of them mechanical:

1. **The primitive does not exist.** `scripts/delegate-codex.sh:245-247` fail-closes on
   `backend = claude`. The only alternative the flow offers is "spawn the `handoff-<identity>`
   subagent" — in-process, blocking, no jobId, no `.handoff/jobs/` state, no `/loop` monitoring,
   no bounded fix round, no receipt evidence. Phases 2–5 of `references/claude-driven.md` are
   written entirely in `delegate-codex.sh` verbs, so a claude-backed row has nowhere to go.
   `references/goal-template.md:61` even says such rows keep `jobId` as `-`.
2. **The error is a dead end, not a command.** It names a subagent rather than something the
   driver can run, so the cheapest runnable resolution is to change the role.
3. **The framing licenses the swap.** `references/fable5-principles.md:62` — "Saving planner
   spend means moving execution onto the subscription meter (Codex)". Its channel table ranks the
   Claude subagent last. `SKILL.md` leads with cost. The driver quoted this reasoning verbatim.
4. **No rule forbids the substitution.** Nothing in prose or `test-prompts.json` says an identity
   is never swapped to change vendor.

## Approach

Generalise `delegate-codex.sh` to dispatch on backend rather than writing a second script. The
job lifecycle (job dir, `meta`, pid, `exit_code`, `status`/`result`/`resume`/`cancel`/`cleanup`/
`list`, worktrees, config-driven role resolution) is already vendor-agnostic; only binary
discovery, the exec line, and the event-stream shape differ. Sharing that code is what makes
parity real instead of asserted — the worktree protocol in particular is pure git and gains
nothing from a per-backend implementation.

Confirmed against the local CLI: `claude -p --output-format stream-json --verbose --model
--effort --permission-mode --permission-prompts --resume` covers everything needed. Claude
stream-json carries a top-level `session_id`, so the existing session extractor works unchanged.

**Decisions taken (from review):**
- Claude worker default permission mode: `acceptEdits` plus `--permission-prompts none`, with
  `HANDOFF_CLAUDE_PERMISSION_MODE` as the escape hatch. Edits proceed; Bash follows the user's
  own `settings.json` allowlist.
  **Superseded in v3.5.1 — the default is now `bypassPermissions`.** The pair was wrong, not merely
  conservative: `--permission-prompts none` means anything that would prompt is denied
  automatically, and `acceptEdits` auto-approves edits and nothing else, so together they are a
  deny-everything-not-already-allowed channel. A delegated job is `--print` on a background pid,
  where nothing can answer a prompt at all. The fallback the decision assumed, the user's
  `settings.json` allowlist, is exactly the thing nobody is present to extend.
- Receipt schema bumps to **v5** adding `cc_jobs` and `cc_job_durations`, alongside the existing
  `codex_jobs` / `codex_job_durations` which narrow to codex-backed jobs only.
- Script keeps the name `delegate-codex.sh` (referenced in 30 files including historical
  `CHANGELOG.md` and `docs/specs/`). Its header and usage state it drives both backends.
- **Parity is the goal, not a caveat.** Every flow below runs identically on either backend, and
  task 5 makes the test suite assert that.

A prototype of task 1 was built and verified end to end before planning, then reverted. Facts
below marked "proven" came from that run.

## Parity audit

| Flow | Today | After |
|---|---|---|
| Delegation (Phase 2) | codex only; claude refused | either backend, same `submit` |
| Monitoring (Phase 3) | `status`/`result` read pid + JSONL | unchanged; already backend-agnostic, now covered by a test |
| Fix round (Phase 4 `resume`) | codex session only | resumes on the parent job's backend |
| e2e worktree lifecycle | script-owned for codex, hand-run git for claude | one shared code path, tests parametrised over both |
| Spec review (`--read-only`) | claude backend refused, prose says spawn a subagent | `--permission-mode plan` job on either backend |
| Arbiter protocol | prose says "each through its own backend"; only codex worked | both solvers run as real jobs |
| Tryout | subagent for claude, delegate for codex | same delegate path for both |
| Receipt | `codex_jobs` only | `codex_jobs` + `cc_jobs`, partitioned from job `meta` |
| Worker runs its own acceptance checks | codex yes, via its sandbox | claude **no** until v3.5.1 — the row this audit did not think to include |

## Tasks

### 1. `scripts/delegate-codex.sh` — backend dispatch

- Header comment + `usage()`: it is the delegation primitive for both backends; the name is
  historical. Document `HANDOFF_CLAUDE_BIN` and `HANDOFF_CLAUDE_PERMISSION_MODE`.
- `resolve_codex_bin` → `resolve_worker_bin <backend>`; the Darwin app-bundle probe stays
  codex-only. Keep writing `CODEX_BIN` / `CODEX_BIN_SOURCE` / `CODEX_VERSION` so `meta` keys and
  `resume`'s parent-binary reuse stay unchanged.
- New `validate_effort <backend> <effort>`: claude takes `low|medium|high|xhigh|max`, codex keeps
  `minimal|low|medium|high|xhigh|max|ultra`. Efforts are per CLI, never one shared enum.
- `cmd_submit`: replace the `backend != codex` refusal with `BACKEND="$ROLE_BACKEND"`. Add an
  optional `--backend` for role-less ad-hoc jobs, and **refuse** `--backend` that contradicts a
  named role — that is the guard replacing the old fail-close.
- `meta` and `--dry-run` record `backend` and new `backend_source` instead of a hardcoded
  `backend=codex`.
- `write_run_script`: claude branch emits
  `claude --print --output-format stream-json --verbose --permission-prompts none
  --permission-mode <acceptEdits|plan> --effort <e> [--model <m>|--resume <sid>] -- "$PROMPT"`,
  preceded by `cd "$WORKDIR"`. `--read-only` maps to `--permission-mode plan`. Skip the
  codex-only `--skip-git-repo-check` path.
- Credential boundary: mirror `handoff_runtime.clean_claude_env()` in the generated `run.sh` with
  `for name in "${!ANTHROPIC_@}" "${!CLAUDE_CODE_@}"; do unset "$name"; done` then
  `export CLAUDECODE=""`. **Proven necessary**: the first prototype used `sed` alternation and
  silently failed on macOS (BSD sed has no `\|` in BRE) — the child inherited a live
  `ANTHROPIC_API_KEY`. Bash prefix expansion is exact and needs no subprocess.
- `cmd_resume`: read `backend` from the parent `meta`, defaulting to `codex` for jobs written
  before this change. Also carry the parent's `role` into the resume `meta` — the user guide's
  own Observations section (`docs/user-guide/agent-handoff.html:527`) flags that resumed jobs
  lose identity provenance, and this closes it for free.
- `cmd_result` parser: add Claude stream-json branches — `type: assistant` text and `tool_use`
  blocks, and the terminal `type: result` carrying `result` and `usage`.

Verify: `bash -n`, `python3 -m unittest tests.test_delegate_role`.

### 2. Receipt schema v5 — `cc_jobs` / `cc_job_durations`

- `docs/receipt-schema.json`: `$id` → `handoff.receipt.v5`; add `cc_jobs` (integer-as-string) and
  `cc_job_durations` (same grammar as `codex_job_durations`); `receipt_schema_version` → `5`.
  Describe `codex_jobs` as codex-backed jobs and `cc_jobs` as claude-backed ones.
- `scripts/validate-receipt.py`: both fields in `REQUIRED_FIELDS`, reuse `JOB_DURATIONS`, version
  check `4` → `5`. Add `"delegated implementation"` to `PHASES`: with CC → CC, `"codex
  implementation"` is a false phase name for a run whose work ran on Claude.
- `scripts/make-receipt.py`: `job_durations()` partitions by each job `meta`'s `backend=` line
  (absent → codex, for legacy job dirs) and returns both strings; new `--cc-jobs` argument
  alongside `--codex-jobs`.
- `tests/test_receipt.py`: v5 round-trip, both counts, a mixed-backend repo partitioning
  correctly, a v4 receipt now failing validation.
- Regenerate `examples/session-receipt.md`. Leave `examples/v2.0.*-conversation-cost-receipt.*`
  alone — historical artifacts in an older format.
- Check whether `scripts/showcase-cost-ledger.py` or `examples/showcase-cost-ledger.json` embed
  receipt fields; if so, regenerate under `SOURCE_DATE_EPOCH=1782921600` and confirm no diff.

Verify: `python3 -m unittest tests.test_receipt`, plus the receipt roundtrip in `CLAUDE.md`.

### 3. Flow prose — the load-bearing half

Layers 2–4 of the root cause are prose. Without these the driver will rationalise around the new
mechanism exactly as it did around the old one.

- `references/fable5-principles.md` — rewrite "Delegate Execution, Don't Downshift the Planner".
  Two reasons to delegate: subscription metering **and** keeping the driver's context clean.
  Replace the three-channel table: the Handoff job is one channel that runs on either backend per
  config; the in-process subagents are the escape hatches. Add the rule plainly: never swap an
  identity to move a job to another vendor; change the config instead.
- `references/claude-driven.md` — Phase 2's fail-closed paragraph becomes "the identity's backend
  picks the CLI"; keep the no-config hard error. Phase 1's spec-review dispatch (`:32`) drops
  "a `backend = claude` identity is refused by that tool by design". Phase 3/4/5 wording
  generalised from "Codex session" to "the worker session". "Sub Agent Routing" keeps its
  three-level lookup but states when it applies: an in-session subagent, not the delegated-job
  path.
- `references/goal-template.md` — drop "rows whose identity resolves to a claude backend keep
  `-`"; every delegated row gets a jobId. Update the execution-time line at `:57`.
- `references/handoff-template.md` — the Spec Review Packet dispatch line, and any "Claude →
  Codex" packet naming that now covers both.
- `SKILL.md` — description, Overview, Routing Rules, Configuration, Output Contract (new receipt
  fields). Version `3.4.0` → `3.5.0`.
- `README.md` — badge, identity table, File Map if needed. `CHANGELOG.md` — `## v3.5.0`.
- `docs/config-schema.md` — note that `backend = "claude"` is a fully supported execution
  channel, not a subagent-only marker.

Verify: `bash scripts/check-skill-repo.sh .`, `python3 scripts/english-only-scan.py`.

### 4. Regression prompts — `test-prompts.json`

New cases, with `must_not` entries:
- A claude-backed identity is delegated on its own backend and gets a jobId.
- The driver **never** substitutes a different identity to change vendor or meter; if the
  configured identity is wrong for the work, it says so and asks rather than silently re-routing.
- A fix round on a claude-backed job resumes the same session on the same backend.
- An e2e worktree row behaves identically whichever backend the earlier stages ran on.
- Receipt splits `codex_jobs` and `cc_jobs` rather than reporting all jobs as Codex.

Verify: `python3 scripts/run-test-prompts.py`.

### 5. Backend parity — tests and the e2e protocol doc

The point of this task is that parity is asserted by the suite, not by prose.

- `tests/test_delegate_role.py`: split `WorktreeTests` (currently 8 codex-only lifecycle tests at
  `:237-350`) into a backend-agnostic base class plus two concrete subclasses, codex and claude.
  All eight assertions — create, record `worktree`/`branch`/`base_commit`, immutable SHA pinning,
  invalid base refused before the job dir exists, run script uses the worktree as cwd, `cleanup`
  idempotent, `cleanup` refuses a dirty tree, `resume` lands in the parent worktree — run on both.
  Only `test_run_script_uses_the_worktree_as_working_directory` needs a backend-aware assertion
  (codex fresh uses `-C "$WORKDIR"`, claude and codex resume use `cd "$WORKDIR"`); assert
  `WORKDIR` reaches the exec line either way.
- `make_env` gains a fake `claude` alongside the fake `codex`, with `HANDOFF_CLAUDE_BIN` set.
- Add a monitoring parity test: `status` and `result` return the same shape for a claude job
  (this is what makes the Phase 3 `/loop` claim testable rather than assumed).
- `references/e2e-gauntlet.md`: rewrite the "Claude backend" section (`:52-61`) — both backends
  use `delegate-codex.sh submit --worktree <branch> --base <sha>`; the hand-run `git worktree
  add` recipe goes away. Keep the note that the Task tool's `isolation: "worktree"` is not used.
  **Delete the "Parity is not claimed" paragraph (`:63`)** and state instead that one code path
  serves both backends and the lifecycle test runs against both.
- `references/tryout.md`: the dispatch line ("subagent spawn for `backend=claude`,
  `delegate-codex.sh --role <identity>` for `backend=codex`") becomes one path for both. Leave
  the `handoff-setup.py --smoke` asymmetry alone — that is an installation probe, deliberately
  different, and out of scope here.
- `docs/specs/design_agent-handoff.md`: append one amendment line to the "Backend parity"
  risk bullet noting it was closed in v3.5.0. Do not rewrite the historical design text.

Verify: `python3 -m unittest tests.test_delegate_role` with both subclasses green.

### 6. User guide — `docs/user-guide/agent-handoff.html`

- Meta line `v3.4.0` → `v3.5.0`, reviewed date.
- Intro (`:173`) and "shape of the thing" (`:179`): handoff moves execution off the driver's
  meter **and** off its context window; Codex is one backend, not the definition.
- The job primitive table (`:213`) — `meta` now carries `backend_source`; the fail-closed row
  (`:219`) changes from "backend = claude is refused" to "the configured backend selects the CLI;
  a contradicting per-job override is refused".
- Stage 2 (`:267`, `:299`) and the boundary table (`:312-327`): "Driver → Codex job" becomes
  "Driver → delegated job"; the routing-cannot-be-moved-by-prose point stays and gets stronger.
- Stage 4 (`:378`): the cost claim sentence gains the context-window reason. The e2e paragraph
  (`:406`) currently ends "the Codex worktree path is script-owned and lifecycle-tested while the
  Claude-backend path follows the same written protocol by hand — parity is not claimed" —
  replace with the shared-code-path statement.
- Stage 5 (`:435`, `:437`): the v5 field list, and how the two job counts are partitioned.
- Drift-control table (`:474-475`): the row "work lands on the wrong agent or the wrong meter"
  now reads that routing comes from config and a contradicting override is refused.
- Observations (`:527`): the "resume loses identity metadata" observation is resolved by task 1 —
  rewrite it as fixed or drop it.

### 7. Diagrams — `docs/user-guide/diagrams/*.svg`

Text-only edits, no coordinate shifts, in four files:
- `phase2-delegate.svg` — the "Fail-closed, both ways" card: keep the no-config error, replace
  "backend = claude → refused, with a pointer to spawn the handoff-<identity> subagent" with the
  backend-selects-the-CLI rule and the contradicting-override refusal. `codex exec` node label
  becomes backend-neutral.
- `flow-overview.svg` — "backend=claude → spawn the subagent" becomes "backend picks the CLI".
- `phase1-plan-split.svg` — "the identity's backend picks the meter" gains "and the CLI".
- `phase5-wrap-up.svg` — the receipt evidence line gains the split job counts.
- `context-ladder.svg` needs no change.

Verify: `python3 -c "import xml.dom.minidom,glob;[xml.dom.minidom.parse(f) for f in
glob.glob('docs/user-guide/diagrams/*.svg')]"`, then open the page and check no clipped text.

## Verification

```bash
python3 -m unittest discover -s tests          # 178 tests green before this change
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
python3 scripts/english-only-scan.py

SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

Live end-to-end (needs a real `claude` CLI and a scratch repo with a claude-backed identity):

```bash
bash scripts/delegate-codex.sh submit --repo <tmp> --prompt-file <packet> \
  --label smoke --role fast_worker
bash scripts/delegate-codex.sh status <jobId> --repo <tmp> --wait --timeout 300
bash scripts/delegate-codex.sh result <jobId> --repo <tmp>
```

Confirm the job edited files, `meta` records `backend=claude`, and the receipt counts it under
`cc_jobs` rather than `codex_jobs`.

## Not doing

- No rename of `delegate-codex.sh`, no new delegation script, no config schema change.
- No per-identity `permission_mode` field, the env override covers the sandbox case. (Reversed in v3.7.1: each identity now has `permission_mode=default|allow-all`; the Claude env override remains supported.)
  *(v3.5.1: still true, but the reasoning was thin. An override only covers a case someone already
  knows to set, and nothing surfaced the need — the failing run's own report said the gates
  passed. The fix was to change the default and to report denials, not to add a field.)*
- No change to the Sub Agent Routing three-level lookup; it stays for in-session subagents.
- No change to `handoff-setup.py --smoke`'s deliberate codex/claude asymmetry.
- No rewrite of `CHANGELOG.md` or `docs/specs/` history beyond one amendment line.

## Risks

- **Claude workers and Bash. — REALIZED on the first real run of this path, fixed in v3.5.1.**
  Job `job-2026-09-08T12-08-24-56820-transcript-impl` took 11 denials: `curl`, the repo's own
  `check-skill-repo.sh`, a node test, a browser check. It wrote every file correctly, exited 0, and
  reported "All gates pass" — including two checks it had been refused. Three things this got wrong.
  It was filed as a bounded ceiling when it was the default failing in the ordinary case, not the
  exotic one. Naming an escape hatch is not mitigation when nothing tells the user to reach for it.
  And the worst of it was not the denial but the silence: a denied tool call does not move the exit
  code, so the only reason anyone noticed was that a human read the log. The safety consequence was
  concrete — the plan's hash-check gate on the vendored `marked` copy never ran, so a gate that was
  supposed to be satisfied was merely skipped, and the bytes happened to be right.
- **Receipt v5 invalidates v4 receipts.** Intended: `validate-receipt.py` failing on an old
  receipt is the signal to regenerate it. Existing saved receipts under a user's `.handoff/` will
  fail validation.
- **Prose is the real fix.** The mechanism alone will not hold if the cost framing survives in
  `fable5-principles.md` and `SKILL.md`. Task 3 is not optional polish.
- **Fake-CLI tests prove wiring, not behaviour.** The parity suite asserts argv, job state, and
  worktree lifecycle against stub binaries. That a real Claude worker completes a real task is
  covered only by the live smoke run above, which needs a human to trigger it.
  *(v3.5.1: sharper than it reads. The suite asserted the argv it was told to expect and never
  asked whether that argv let the worker work — `--permission-mode` appeared in no assertion at
  all, so the defect was invisible to a green suite. Parity was defined as "same flags, same job
  shape" when the property that mattered was "same ability to run its own checks". The permission
  assertions added in v3.5.1 close the specific hole; the general lesson is that a parity claim
  needs a test for the capability, not only for the plumbing.)*
