# Agent Handoff Flow

Use this flow when the user asks Claude to split work with Codex ("Handoff", "delegate this to codex", "let codex do it", "run codex in the background"). Claude Code is the driver: it plans, delegates quota-pressure work to Codex (subscription billing), monitors the background jobs, and quality-gates everything before accepting it. The goal is saving Claude API spend without lowering quality — the full-review gate in Phase 4 is what makes that claim honest.

All helper scripts live in `$HANDOFF_DIR` (see Tool Location in `SKILL.md`). Job state lives under `<repo>/.handoff/jobs/`.

## Phase 0 — Preflight

- Stamp the start: `python3 "$HANDOFF_DIR/scripts/make-receipt.py" --start --repo "$REPO"` writes `<repo>/.handoff/session-start`, the clock the Phase 5 receipt measures against. `/agent-handoff resume` re-enters mid-flow and skips this phase, so a resumed run keeps the start it already had.
- Confirm the Codex CLI: `codex --version`. If missing, stop and tell the user this flow needs the Codex CLI installed and authenticated.
- Check the target repo's `AGENTS.md` for the line `DO NOT send optional commentary`. If absent, ask the user once whether to append it (it reduces Codex filler output and keeps its replies dense). Never edit the user's repo files silently.
- Run `git status --short` and note pre-existing dirt so Codex's diff can be isolated later.

## Phase 1 — Plan and Split (goal file)

- Refine the user's request into a concrete plan, then write `<repo>/.handoff/goal.md` (template: `references/goal-template.md`): the overall goal as one why-forward sentence, a task table, and the checkpoint rule from `references/fable5-principles.md`. Because the Phase 3 monitor loop writes task statuses into the same file, use `scripts/goal-sync.py read`/`write --expect-sha256 <hash>` instead of editing the file directly — it aborts instead of silently clobbering a concurrent update.
- Split tasks by making one judgment per row — which capability does this work need (see the identity definitions in `references/goal-template.md`):
  - `fast_worker` for mechanical, spec-complete work (refactors, test writing, wide read-only scans, doc generation, boilerplate, batch migrations) — the bulk of delegable work.
  - `deep_reasoner` for ambiguous, wrong-premise-is-expensive work (a hard diagnosis, a config change whose wrong variant silently breaks things).
  - identity `-` for what the driver keeps inline: architecture, the split decision itself, cross-task integration, security/correctness-critical paths, final acceptance. Never route these to a cheaper identity to save money, and never burn the driver's seat on mechanical work.
  Which CLI executes and which meter bills follows from the identity's configured `backend` (`/agent-handoff config`), not from a separate per-task choice. Two escape hatches remain for edge cases: a one-shot Codex subagent (e.g. a rescue agent) for a stuck step needing a second diagnosis with no durable state, and a raw Task-tool subagent when no Handoff identity fits — billing notes for both in `references/fable5-principles.md`.
- Adversarial gate: attack your own split before acting on it. Answer three questions in writing: does each delegated task really not need the expensive tier, does the integration cost of the split boundary eat the savings, and does each row's identity match the work's actual stakes (with a reason it is not a more expensive one). A row that survives all three gets delegated; anything else gets its identity corrected, merged into a neighbour, or kept inline. Fix the split first, then delegate.
- Acceptance coverage: add `e2e_specifier` and `e2e_verifier` rows when the change **alters user-observable behaviour at a real interface** (UI, API surface, mobile screen). Skip them for internal refactors, docs, config, and pure library work. When the criterion fires but the identities are unconfigured, say so once — "this change is user-observable; `/agent-handoff config --with-e2e` would add acceptance coverage" — then continue without them. Full protocol, both packets, and the worktree rules: `references/e2e-gauntlet.md`.
- Spec review (optional, once): when `deep_reasoner` carries `auto_review_spec = true` in its config, or the user asks for a second pair of eyes, give the plan one independent read before the user sees it. Build the Spec Review Packet from `references/handoff-template.md` and send it through the identity's configured backend:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label spec-review \
  --role deep_reasoner --read-only
```

  A `backend = claude` identity is refused by that tool by design: spawn `handoff-deep-reasoner` instead (Sub Agent Routing below). Then assess the findings yourself, fold in the ones that hold, and record the outcome in the goal file's `## Spec Review` block before the plan (or the Goal Packet) goes to the user.

  Three rules make this safe to leave on. It fires **at most once per run**: a non-empty `## Spec Review` block means the automatic review is spent, so an adjusted plan, a thin review, and a resumed session all fail to re-trigger it, and only an explicit user request produces another. The reviewer is **read-only** — it returns prioritized findings and never edits the spec, the goal file, or product code, and its findings are input to your judgment rather than a verdict you apply unread. And it is **not blind**: the plan under review is your own answer, so the arbiter's contamination rule does not apply here. When `deep_reasoner` resolves to the driver's own vendor the review still runs, and the receipt notes `same-vendor` exactly as the arbiter protocol does.

## Sub Agent Routing

This lookup applies **only to identities whose configured `backend` is `claude`** — an identity with `backend = codex` never enters it: that work goes through `delegate-codex.sh` (Phase 2), and spawning a Task subagent for it would silently swap in the wrong vendor and meter. For a claude-backend identity, resolve *which* agent definition to spawn with this three-level lookup, in order:

1. **`handoff-*` namespaced agent** — if `/agent-handoff config` has generated `handoff-deep-reasoner` / `handoff-fast-worker` / `handoff-arbiter` (project or global scope; check `python3 "$HANDOFF_DIR/scripts/handoff-config.py" resolve` for the configured identity, or just try spawning the namespaced agent), use it. Its model/effort came from the user's own setup choice.
2. **The user's own similarly-named agent** — if no `handoff-*` agent exists but the user has their own `deep-reasoner.md` / `fast-worker.md` (or an agent whose description clearly matches the identity), use it as-is. Never rename, edit, or treat it as if it were handoff-managed.
3. **Generic `Task` tool** — no matching agent either way: spawn a plain Task-tool subagent with the identity described in the prompt. This is the fallback, not a signal that setup is missing something the task needs.

A repo with no `/agent-handoff config` run yet simply falls through to level 3 every time; that is normal, not broken.

## Arbiter Protocol — Blind Arbitration

For contentious or high-stakes calls — the driver judges the answer disputable, or the user says "arbitrate" / "this is contested" / "second opinion" — do not settle for one solver's answer:

1. Send the **same problem, verbatim** to both `deep_reasoner` and `arbiter`, each through its own configured backend (subagent spawn or `delegate-codex.sh --role arbiter`).
2. **Contamination rule**: neither packet may contain the other solver's answer, conclusion, or any leaning hint ("X thinks A, verify it" is already contaminated). Blind means blind — a contaminated run silently produces fake agreement and must be rerun, not patched.
3. Compare the two answers. Agreement → adopt, note dual-verified. Disagreement → the driver rules, and records the point of divergence plus the ruling's reasoning in the receipt (both solvers appear in `roles_used`; the divergence goes in the report/Notes).
4. The blind check is strongest when arbiter and deep_reasoner run on different vendors (the wizard's default presets guarantee this); if the config has them same-vendor, the protocol still runs but the receipt notes `same-vendor` so the weaker independence is visible.

This is distinct from the Phase 1 gate: that gate attacks a plan you already have; the arbiter independently *solves the same problem* with no knowledge of the first answer.

## Phase 2 — Delegate

- Build each Codex prompt from the "Claude → Codex Delegation Packet" in `references/handoff-template.md`: why-forward context, one-sentence task, verifiable acceptance criteria, scope constraints, and the fixed output rules (no optional commentary; lessons learned at the end).
- Submit as a background job, passing the row's identity so backend, model, and effort resolve from `/agent-handoff config`'s config (explicit `--model`/`--effort` still wins per field if a specific task genuinely needs an override):

```bash
prompt=$(mktemp)
# ... write the delegation packet into "$prompt" ...
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label <task-id> \
  --role <identity>
```

The tool fail-closes on both misconfigurations: no Handoff config yet → clear error (run `/agent-handoff config` first, or fall back to an explicit `--effort` for this one job and note it in `Notes`); identity configured with `backend = claude` → refusal with a pointer to spawn the `handoff-<identity>` subagent instead. That is the guard against silently running a claude-backend identity on the wrong vendor and the wrong meter.

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
- A job stuck with no new JSONL events for two consecutive ticks, or a `status` of FAILED, is a monitoring anomaly: cancel it, read `stderr.log`, and either resubmit with a corrected prompt or take the task back into Claude. Record the anomaly for the receipt.

## Phase 4 — Full Review Gate

- Collect each finished job: `bash "$HANDOFF_DIR/scripts/delegate-codex.sh" result <jobId> --repo "$REPO"`.
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
- Findings? Send one bounded fix round back to the same Codex session:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" resume <jobId> \
  --repo "$REPO" --prompt-file <fix-notes>
```

- Maximum two fix rounds per task. Still failing after that: take the task back and finish it in Claude; note the takeback in the goal file and receipt. Optionally run the gstack `/codex` review on the final combined diff as an independent third-party gate.

## Phase 4.5 — Delivery (opt-in, `references/goal-to-pr.md`)

Only runs when the user asked for the full "full protocol / PR delivery / goal mode" protocol (see Trigger Grading in `references/goal-to-pr.md`) and has authorized it via a Goal Packet (`references/handoff-template.md`). Skip this phase entirely on the default lightweight flow above.

- Follow Stage 3 (PR) and Stage 4 (Verify Ladder) in `references/goal-to-pr.md`: branch/worktree, implement, verify, push, open/update PR, wait for and fix CI, verify preview.
- Stop at merge-ready + preview verified. Merge, production, tags, force-push, deletion, destructive migration, and external publish are each an independent hard stop — each needs its own fresh imperative sentence, recorded in the goal file's `Delivery.authorization` field.
- Update `.handoff/goal.md`'s `## Delivery` section as each field becomes known (branch/worktree, pr, ci, preview, live).

## Phase 5 — Wrap Up

- Mark tasks done in `.handoff/goal.md`; stop any remaining `/loop`.
- Emit the Handoff Session Receipt with `codex_jobs: <count>` (fix rounds included); `claude_session` is the current session. Pass `--repo "$REPO"` so `duration` and `codex_job_durations` are measured from the start marker and the job directories; neither is ever typed from recall. Set `scope` and `config_source` from `handoff-config.py resolve` (or `handoff-setup.py --status`), and build `roles_used` from the roles this run actually invoked: each `delegate-codex.sh` job's `meta` file has `role`/`model`/`effort`/`model_source`/`effort_source`, and `handoff-config.py resolve` has each role's `verified`/`verified_at`. List a role even when `verified` is `false` — never guess it true.
- E2E roles and a spec-review job appear in `roles_used` like any other role when the run used them; `receipt_schema_version` stays `4`.
- Run the memory protocol in `references/memory-protocol.md`: what got delegated, how Codex performed per task type, rework rounds, and effort fit — so the next split decision starts smarter.
