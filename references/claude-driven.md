# Agent Handoff Flow

Use this flow when the user asks Claude to split work with another agent ("Handoff", "delegate this to codex", "let codex do it", "run codex in the background"). Claude Code is the driver: it plans, delegates the work its split assigns to an identity, monitors the background jobs, and quality-gates everything before accepting it. Delegation buys two things — execution on a subscription meter instead of the driver's, and a driver context window kept clear of execution history — and the full-review gate in Phase 4 is what keeps either claim honest.

Which CLI runs a delegated job is decided by the identity's configured `backend`, always. A claude-backed or copilot-backed identity is a real background job with the same jobId, monitoring loop, fix-round `resume`, worktree lifecycle, and receipt evidence a codex-backed one gets. Never send a task to a different identity to move it onto another vendor or meter; that is a config change, not a routing decision (see `references/fable5-principles.md`).

All helper scripts live in `$HANDOFF_DIR` (see Tool Location in `SKILL.md`). Job state lives under `<repo>/.handoff/jobs/`.

## Phase 0 — Preflight

- Stamp the start: `python3 "$HANDOFF_DIR/scripts/make-receipt.py" --start --repo "$REPO"` writes `<repo>/.handoff/session-start`, the clock the Phase 5 receipt measures against. `/agent-handoff resume` re-enters mid-flow and skips this phase, so a resumed run keeps the start it already had. Reopening a row the arbiter rejected is the exception: that run already emitted its receipt, so reopening it is a new run and stamps a new start. On `/agent-handoff resume`, present rows marked `rejected` first, with their `## Arbitration` line, the handover in Notes, and the saved patch or kept worktree, and never reopen one yourself.
- Confirm the worker CLIs the config actually uses: `codex --version` for any codex-backed identity, `claude --version` for any claude-backed one, `copilot --version` for any copilot-backed one. That last check reads the output rather than the exit code: AWS Copilot CLI shares the binary name, and only the string `GitHub Copilot CLI` identifies the right one. If a CLI is missing, stop and tell the user which identities cannot run until it is installed and authenticated.
- Check the target repo's `AGENTS.md` for the line `DO NOT send optional commentary`. If absent, ask the user once whether to append it (it reduces Codex filler output and keeps its replies dense). Never edit the user's repo files silently.
- Run `git status --short` and note pre-existing dirt so Codex's diff can be isolated later.

## Phase 1 — Plan and Split (goal file)

- Refine the user's request into a concrete plan, then write `<repo>/.handoff/goal.md` (template: `references/goal-template.md`): the overall goal as one why-forward sentence, a task table, and the checkpoint rule from `references/fable5-principles.md`. Because the Phase 3 monitor loop writes task statuses into the same file, use `scripts/goal-sync.py read`/`write --expect-sha256 <hash>` instead of editing the file directly — it aborts instead of silently clobbering a concurrent update.
- Split tasks by making one judgment per row — which capability does this work need (see the identity definitions in `references/goal-template.md`):
  - `fast_worker` for mechanical, spec-complete work (refactors, test writing, wide read-only scans, doc generation, boilerplate, batch migrations) — the bulk of delegable work.
  - `deep_reasoner` for ambiguous, wrong-premise-is-expensive work (a hard diagnosis, a config change whose wrong variant silently breaks things).
  - identity `-` for what the driver keeps inline: architecture, the split decision itself, cross-task integration, security/correctness-critical paths, final acceptance. Never route these to a cheaper identity to save money, and never burn the driver's seat on mechanical work.
  Which CLI executes and which meter bills follows from the identity's configured `backend` (`/agent-handoff config`), not from a separate per-task choice, and not from a preference about cost formed while delegating. Two escape hatches remain for edge cases: a one-shot Codex subagent (e.g. a rescue agent) for a stuck step needing a second diagnosis with no durable state, and a raw Task-tool subagent when no Handoff identity fits — billing notes for both in `references/fable5-principles.md`.
- Adversarial gate: attack your own split before acting on it. Answer three questions in writing: does each delegated task really not need the expensive tier, does the integration cost of the split boundary eat the savings, and does each row's identity match the work's actual stakes (with a reason it is not a more expensive one). A row that survives all three gets delegated; anything else gets its identity corrected, merged into a neighbour, or kept inline. Fix the split first, then delegate.
- Acceptance coverage: add `e2e_specifier` and `e2e_verifier` rows when the change **alters user-observable behaviour at a real interface** (UI, API surface, mobile screen). Skip them for internal refactors, docs, config, and pure library work. When the criterion fires but the identities are unconfigured, say so once — "this change is user-observable; `/agent-handoff config --with-e2e` would add acceptance coverage" — then continue without them. Full protocol, both packets, and the worktree rules: `references/e2e-gauntlet.md`.
- Spec review (every plan): before the plan reaches the user, give it one read from `deep_reasoner`. There is no toggle. Build the Spec Review Packet from `references/handoff-template.md` and send it through the identity's configured backend:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label spec-review \
  --role deep_reasoner --read-only
```

  `--read-only` becomes `-s read-only` on codex, `--permission-mode plan` on claude, and `--mode plan` on copilot, so the review is a real job with a jobId on any of the three backends. Keep the label exactly `spec-review`: `resume` reads it off the chain's first job to pick the cap.

  The reviewer tags each finding `blocking` or `advisory`. Accept or decline each one and write down why. Consensus means no blocking finding was declined; a declined advisory finding is recorded and goes no further. Under the cap (`[review] spec_max_rounds`, default 1, from `handoff-config.py resolve`), `resume` the same spec-review job with the revised plan, the declined blocking findings, and your reasons; the reviewer withdraws or keeps each one. At the cap with a blocking finding still declined, escalate (Arbiter Protocol — Escalation Ruling). An approval sends the plan to the user as you revised it. A rejection is final: no task row is delegated, the run stops before Phase 2, the user gets the plan with the ruling, and wrap up still emits a receipt with phase `planning`.

  A spec-review job that fails, stalls, or returns neither findings nor a one-line "sound" verdict is not a round and never counts as consensus. Retry it once as a fresh job with the same packet. A second failure sets the status to `failed`, and Phase 2 waits for the user.

  Track the review in the goal file's `## Spec Review` block: a `status:` line (`not run`, `round <n> running`, `awaiting arbitration`, or one of the terminal `consensus`, `approved`, `rejected`, `failed`) and one line per round with its jobId and your disposition of each blocking finding. Any status other than `not run` means the automatic review is spent for this run: an adjusted plan, a thin review, and a resumed session never start a new one, and a resumed session that finds a non-terminal status finishes that chain. Only an explicit user request starts a new chain. The reviewer is read-only, and its findings are input to your judgment, never a verdict you apply unread. It is not blind: the plan under review is your own answer, so the contamination rule does not apply. When `deep_reasoner` resolves to the driver's own vendor the review still runs, and the receipt notes `same-vendor`.

## Sub Agent Routing

This lookup applies **only when you are spawning an in-session subagent** — one of the two escape hatches in `references/fable5-principles.md`, or a step the user asked to run inline. It is not the delegated-job path: a task row with an identity goes through `delegate-codex.sh --role <identity>` (Phase 2) on whichever backend that identity is configured for, claude and copilot included. Spawning a subagent instead of delegating loses the jobId, the durable job state, the monitoring loop, the bounded fix round, and the receipt evidence.

When you do spawn one, resolve *which* agent definition with this three-level lookup, in order:

1. **`handoff-*` namespaced agent** — if `/agent-handoff config` has generated `handoff-deep-reasoner` / `handoff-fast-worker` / `handoff-arbiter` (project or global scope; check `python3 "$HANDOFF_DIR/scripts/handoff-config.py" resolve` for the configured identity, or just try spawning the namespaced agent), use it. Its model/effort came from the user's own setup choice.
2. **The user's own similarly-named agent** — if no `handoff-*` agent exists but the user has their own `deep-reasoner.md` / `fast-worker.md` (or an agent whose description clearly matches the identity), use it as-is. Never rename, edit, or treat it as if it were handoff-managed.
3. **Generic `Task` tool** — no matching agent either way: spawn a plain Task-tool subagent with the identity described in the prompt. This is the fallback, not a signal that setup is missing something the task needs.

A repo with no `/agent-handoff config` run yet simply falls through to level 3 every time; that is normal, not broken.

## Arbiter Protocol — Blind Arbitration

For contentious or high-stakes calls — the driver judges the answer disputable, or the user says "arbitrate" / "this is contested" / "second opinion" — do not settle for one solver's answer:

1. Send the **same problem, verbatim** to both `deep_reasoner` and `arbiter`, each as a real job on its own configured backend: `delegate-codex.sh submit --role deep_reasoner` and `--role arbiter`. Both solvers get a jobId and appear in the receipt, whichever CLI executes them.
2. **Contamination rule**: neither packet may contain the other solver's answer, conclusion, or any leaning hint ("X thinks A, verify it" is already contaminated). Blind means blind — a contaminated run silently produces fake agreement and must be rerun, not patched.
3. Compare the two answers. Agreement → adopt, note dual-verified. Disagreement → the driver rules, and records the point of divergence plus the ruling's reasoning in the receipt (both solvers appear in `roles_used`; the divergence goes in the report/Notes).
4. The blind check is strongest when arbiter and deep_reasoner run on different vendors (the wizard's default presets guarantee this); if the config has them same-vendor, the protocol still runs but the receipt notes `same-vendor` so the weaker independence is visible.

This is distinct from the Phase 1 gate: that gate attacks a plan you already have; the arbiter independently *solves the same problem* with no knowledge of the first answer. It is also distinct from the escalation ruling below, which sees both sides of a dispute on purpose.

## Arbiter Protocol — Escalation Ruling

When a review gate reaches its cap without consensus (the spec gate with a blocking finding still declined, or the implementation gate with findings still open after the last allowed pass), escalate to `arbiter`. Always: a disputed task is never taken back instead.

1. Build the Arbitration Packet from `references/handoff-template.md` with the whole dispute: the plan or the task brief, the acceptance criteria, the evidence (the revised plan, or the full scoped diff with the results of the checks you ran), and every round's findings with the other side's response. Nothing is withheld. This is not blind arbitration, and the contamination rule does not apply.
2. Submit it as a read-only job: `delegate-codex.sh submit --role arbiter --read-only --label arbitrate-<task-id>` (`arbitrate-spec` for the plan).
3. A verdict is well formed only when the output's first line is exactly `verdict: approve` or `verdict: reject` and the reasons do not contradict it. The ruling binds; do not re-argue it.
   - Spec gate: approve sends the plan to the user as revised; reject stops the run before Phase 2.
   - Implementation gate: approve accepts the diff, overruling your open findings, and the row goes to `done`. An approval never stands in for an e2e PASS: with a validated FAIL among the open findings, approval means the scenario was judged wrong, so the scenario goes back to step 1 of the e2e sequence, a new hash is recorded, and the verifier reruns. Reject is final: the row becomes `rejected` and its diff is set aside (Phase 4).
4. An arbiter job that fails, stalls, is cancelled, or returns anything else is an anomaly. Resubmit one fresh arbiter job with the same packet, and never take the dispute back: a takeback settles it for the side that raised it. A second failure, or a `submit` refused before any job exists, is recorded as `no verdict`. The row, or the plan, stays in `arbitration`, the run wraps up, and only the user moves it.
5. Record every ruling, approve included, as one line in the goal file's `## Arbitration` block: gate, task (`plan` for the spec gate), the arbiter's jobId or the refusal message, the verdict (`approve`, `reject`, or `no verdict`), a one-line reason, and `same-vendor` when the arbiter's backend matches either party's. A rejection also writes a handover into Notes: what the reviewer found, what the arbiter ruled, and what continuing would take (raise the cap, rewrite the brief, or take the task over).

## Phase 2 — Delegate

- Build each worker prompt from the "Handoff Delegation Packet" in `references/handoff-template.md`: why-forward context, one-sentence task, verifiable acceptance criteria, scope constraints, and the fixed output rules (no optional commentary; lessons learned at the end).
- Submit as a background job, passing the row's identity so backend, model, and effort resolve from `/agent-handoff config`'s config (explicit `--model`/`--effort` still wins per field if a specific task genuinely needs an override):

```bash
prompt=$(mktemp)
# ... write the delegation packet into "$prompt" ...
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label <task-id> \
  --role <identity>
```

The identity's `backend` picks the CLI: `codex` runs `codex exec --json`, `claude` runs `claude --print --output-format stream-json`, `copilot` runs `copilot -p --output-format json`. Everything downstream — jobId, `.handoff/jobs/<jobId>/` state, the Phase 3 loop, the Phase 4 `resume` fix round, worktrees, receipt evidence — is the same on all three.

Each identity selects `permission_mode=default|allow-all`. Default uses Claude `dontAsk` with `Read Glob Grep Edit Write Bash`, inherited Codex config without sandbox flags, or Copilot `--allow-all-tools` retaining path/URL checks. Allow-all means *use the provider's native unrestricted mode*, not force three CLIs into one security posture. Only claude changes behaviour under default; Codex and Copilot retain their previous flags. Read-only wins over the retained Claude env override and configured posture; the env value is still validated first. Read-only drops Claude's worker allowlist and Copilot's allow-all flags. Resume inherits posture and read_only; missing parent posture means default, never allow-all. Warnings follow the effective concrete mode. No OS-level sandbox is added for Claude default. Codex denials are not counted, and permission_denied is advisory, not enforced. Verify edits and checks on disk. Tell the user the effective posture before the first delegation. Worktrees and packets are scope controls, not enforced containment.

On copilot, `--read-only` swaps `--allow-all-tools` for `--mode plan` and the two are never generated together. That pairing was probed: plan mode still wins on disk, but the worker emits zero denial events, exits 0, and reports writes that never happened — a silent-failure channel with nothing for the monitor to catch.

Two guards stay closed. No Handoff config yet → a clear error (run `/agent-handoff config` first, or fall back to an explicit `--effort` for this one job and note it in `Notes`). And a `--backend` that contradicts a named role → refused, because moving a job onto another vendor is a config change the user should see, not a per-job override. Efforts are per CLI, so an effort valid for codex (`ultra`) is refused on claude and on copilot, and one valid for copilot (`none`) is refused on the other two. A copilot identity must also name a concrete model: `model = "auto"` is refused, because an identity is a deliberate choice and `auto` hands it back to the vendor per request.

- Use `--read-only` for scan/review jobs that must not modify the repo.
- Record the returned jobId in the goal file's task row. Independent tasks can be submitted in parallel.
- E2E rows run in a dedicated worktree cut from an immutable SHA:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label <task-id> \
  --role e2e_specifier --worktree "e2e/<task-id>" --base "$SHA0"
```

  `--repo` stays the main repo in every invocation; a worktree is a derived working directory, never a `--repo` value. The packet carries the frozen goal excerpt and spec inline, because a worktree holds tracked content only and has no `.handoff/`.

## Phase 3 — Monitor (loop)

- Short single job (expected under ~5 minutes): block on it — `bash "$HANDOFF_DIR/scripts/delegate-codex.sh" status <jobId> --repo "$REPO" --wait --timeout 300`.
- Long or multiple jobs: set up the built-in `/loop` skill at a 5-minute interval with a prompt like: read `.handoff/goal.md`, run `delegate-codex.sh status` for every running jobId (tail the job's `log.jsonl` for the last event), update task statuses in the goal file, and when no job is left running, stop the loop and continue with Phase 4.
- The loop reads each row's `depends` column and does not submit a row whose dependencies have not reached `done`. An `e2e_verifier` row waits on both the specifier row and the implementation row: it needs the tests in its tree, not just the feature.
- `status` and `result` count Claude and Copilot denials; **Codex denials are not counted**. **`permission_denied` is advisory, not enforced**: DONE derives from exit code and status warns but still succeeds. Zero is not evidence that checks ran. Treat any non-zero count as a failed job whatever its exit code says: a denied tool call does not move the exit code, so the worker ran to completion having skipped checks it believed it had run. Re-run the blocked commands yourself before accepting anything, and record it as an anomaly.
- A job stuck with no new JSONL events for two consecutive ticks, or a `status` of FAILED, is a monitoring anomaly: cancel it, read `stderr.log` and the tail of `log.jsonl`, and either resubmit with a corrected prompt or take the task back into the driver. On copilot the log is not optional: an API failure can arrive with stderr empty and the detail carried only by a `session.error` event in `log.jsonl`, which is also where `result --json` reads it from. Record the anomaly for the receipt. An arbiter job is the exception: resubmit it once and never take the dispute back (Escalation Ruling). `status` and `result` read the same job state on any of the three backends, so nothing here changes with the CLI.

## Phase 4 — Full Review Gate

- Collect each finished job: `bash "$HANDOFF_DIR/scripts/delegate-codex.sh" result <jobId> --repo "$REPO"`.
- Check `permission_denied` in the `result` output first, remembering that Codex denials are not counted and the guard is advisory. A worker that was blocked could not run its own acceptance checks, so its report of them is worthless — run them yourself and judge the diff on that, never on the worker's summary.
- Claude reviews the complete diff itself — `git diff` (scoped to the files the job touched), plus the fastest relevant check. This is a full review by default, not a sample. Do not accept work you have not read.
- Review against the acceptance criteria in `.handoff/goal.md`, not against the diff alone. A reviewer given only the diff confidently redefines the spec as "internally consistent with what changed" and misses tasks that were never done — in Superpowers 6, diff-only reviewers caught 0 of 5 missing task briefs. Re-read each task's brief, then ask "does this diff satisfy that brief," not just "is this diff self-consistent."
- When the run carries e2e rows, sequence them before the fix-round step:
  1. Review the specifier's scenarios against the acceptance column in `.handoff/goal.md`. A generated test weaker than the goal's criteria will green-light a broken feature, and the verdict inherits that weakness.
  2. Record the reviewed hash at that moment: `REVIEWED_SHA=$(find features -name '*.feature' | sort | xargs cat | shasum -a 256 | cut -d' ' -f1)`.
  3. Integrate the specifier and implementation branches onto the feature branch, producing one combined commit.
  4. Submit the verifier pinned to that commit, then validate its verdict before acting on it: `python3 "$HANDOFF_DIR/scripts/validate-verdict.py" "$REPO/.handoff/e2e/$JOB_ID/verdict.json" --expect-scenarios-sha256 "$REVIEWED_SHA"`.
  5. PASS goes to the merge decision. FAIL is a product finding routed to the original implementer, never to the verifier. BLOCKED means nothing was proved: resolve the prerequisite and rerun, never read it as PASS.
  6. Once a worktree is merged or abandoned, `delegate-codex.sh cleanup <jobId> --repo "$REPO"`.

  `main` is reached only after a PASS plus the driver's final review, which keeps merge inside the existing hard-stop rule.
- Findings? Send one bounded fix round back to the same worker session — `resume` reads the parent job's backend from its `meta` and lands on the same CLI, in the same worktree:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" resume <jobId> \
  --repo "$REPO" --prompt-file <fix-notes>
```

- The cap is `[review] implementation_max_rounds` review passes (default 3): the original job is pass one and each `resume` adds one, so the default allows two fix rounds. It applies to every delegated implementation row, whatever its identity. `resume` refuses a round past the cap before creating the job; never get around it with a fresh `submit`. An e2e FAIL is a round on the implementation row's chain. A rerun after BLOCKED is a fresh verifier `submit` pinned to the same commit, never a `resume`, so it is not a round.
- With e2e rows, an implementation row's `done` is provisional until the verifier's PASS: a FAIL moves it back to `rework-<n>`, sends the verifier row back to `pending`, and holds every other row that depends on it. A dependent already running may finish; its result is not accepted until the row is `done` again.
- Findings still open after the last allowed pass: escalate (Escalation Ruling). Do not take the task back. On a rejection, set the diff aside so independent rows do not run on top of it: in the main repo, save the row's scoped changes, new files included, to `.handoff/rejected/<task-id>.patch` and return those paths to their state before the job; a worktree row keeps its worktree (skip `cleanup`); a row already integrated onto the feature branch is backed out with a revert commit, never a history rewrite. Rows that depend on it are held; independent rows continue.
- Optionally run the gstack `/codex` review on the final combined diff as an independent third-party gate.

## Phase 4.5 — Delivery (opt-in, `references/goal-to-pr.md`)

Only runs when the user asked for the full "full protocol / PR delivery / goal mode" protocol (see Trigger Grading in `references/goal-to-pr.md`) and has authorized it via a Goal Packet (`references/handoff-template.md`). Skip this phase entirely on the default lightweight flow above.

- Follow Stage 3 (PR) and Stage 4 (Verify Ladder) in `references/goal-to-pr.md`: branch/worktree, implement, verify, push, open/update PR, wait for and fix CI, verify preview.
- Stop at merge-ready + preview verified. Merge, production, tags, force-push, deletion, destructive migration, and external publish are each an independent hard stop — each needs its own fresh imperative sentence, recorded in the goal file's `Delivery.authorization` field.
- Update `.handoff/goal.md`'s `## Delivery` section as each field becomes known (branch/worktree, pr, ci, preview, live).

## Phase 5 — Wrap Up

- Mark accepted tasks done in `.handoff/goal.md`; leave rows in `rejected` or `arbitration` as they are, because dependency holds and the user's decision on resume read them; stop any remaining `/loop`.
- Write the Handoff Session Receipt with the delegated jobs split by the backend that executed them: `--codex-jobs <count>`, `--cc-jobs <count>`, and `--copilot-jobs <count>`, fix rounds included in each. Count them from each job's `meta` `backend=` line, not from recall; a job directory with no `backend=` line predates backend dispatch and is codex. Never fold a copilot job into one of the other two counts. `claude_session` is the current session. Pass `--repo "$REPO"` so `duration`, `codex_job_durations`, `cc_job_durations`, and `copilot_job_durations` are measured from the start marker and the job directories; none is ever typed from recall. Set `scope` and `config_source` from `handoff-config.py resolve` (or `handoff-setup.py --status`), and build `roles_used` from the roles this run actually invoked: each job's `meta` has `role`/`backend`/`model`/`effort`/`model_source`/`effort_source`, and `handoff-config.py resolve` has each role's `verified`/`verified_at`. A resumed job carries its parent's `role` and `backend`, so a fix round is not an anonymous entry. List a role even when `verified` is `false` — never guess it true. Put every `## Arbitration` line into `--anomalies` as `arbitration: <task> approve|reject|no verdict (<jobId>)`, joined with any other anomalies by `; ` on one line; the receipt takes one line per field. The generator saves what it validated to `<repo>/.handoff/receipts/receipt-<stamp>.md` on its own — that file is what `/agent-handoff cost-receipt` and `/agent-handoff visualise` read back, so a run that only printed the block leaves both with nothing. `--no-save` suppresses the write, and the only reason to reach for it is a receipt regenerated with `--ended-at` that should not deposit a second file.
- E2E roles, the spec-review job, and any arbiter ruling appear in `roles_used` like any other role when the run used them; `receipt_schema_version` is `6`. Use `phase: delegated implementation` when the run's delegated work was not all codex-backed.
- Run the memory protocol in `references/memory-protocol.md`: what got delegated, how each identity performed per task type, rework rounds, escalations and their verdicts, and effort fit — so the next split decision starts smarter.
