# Delegation flow

The run behind `/agent-handoff` and `/agent-handoff resume`. Claude Code is the driver: it plans, splits, delegates each row to an identity, monitors the background jobs, and reviews every diff before accepting it. Helper scripts are in `$HANDOFF_DIR/scripts/`, and job state is under `<repo>/.handoff/jobs/`.

## On every entry, fresh or resumed

1. Resolve the config: `python3 "$HANDOFF_DIR/scripts/handoff-config.py" --repo "$REPO" resolve`. It gives each identity's backend, model, effort and `permission_mode`, plus the two review caps. With no config, run the setup in `references/setup-wizard.md` first. Phase 2 has the one exception, for a single job.
2. Read `references/backends/<backend>.md` for each distinct backend the config uses, and no others. Each file holds that CLI's preflight check, what its permission modes mean, and how its failures look. Read each whole file: a search for one line misses the rules that apply later in the run. On a resume, also read the file for the `backend=` recorded in the `meta` of each job the goal file still refers to. It can differ from today's config, and a fix round runs on the recorded backend. A job whose `meta` has no `backend=` line predates backend dispatch and is codex.
3. Run each preflight check. If a CLI is missing, stop and tell the user which identities cannot run until it is installed and signed in.
4. On a resume, when a row's acceptance names `.handoff/evidence/` or the goal file's `## Spend` block is not `none`, read `references/artefact-rows.md`.
5. If the user asked for the full protocol ("full protocol", "PR delivery", "goal mode"), read `references/pr-delivery.md` now. Its plan stage comes before the task table is written.

## Phase 0: Preflight

A fresh run:

- Stamp the start: `python3 "$HANDOFF_DIR/scripts/make-receipt.py" --start --repo "$REPO"`. The Phase 5 receipt measures from this marker and is refused without it.
- Run `git status --short` and note what is already dirty, so each worker's diff can be isolated later.

`/agent-handoff resume` skips this phase and keeps the start the run already has. Read `.handoff/goal.md` and continue from its statuses:

- Open your report with the rows marked `rejected`, before any other row, each with its `## Arbitration` line, the handover in Notes, and the saved patch or kept worktree. Never reopen one yourself. Reopening is the user's call, and it is a new run: stamp a new start.
- With a row, or the plan, in `rejected` or `arbitration`, read `references/arbitration.md` before you act on it. An arbiter job that is still running, or still owed its one retry, is an open chain: finish it. One that ended in `no verdict` stays in `arbitration` until the user moves it.
- A `## Spec Review` status that is not terminal means a review chain is open. Finish that chain. Do not start another.

## Phase 1: Plan and split

- Refine the request into a concrete plan and write `.handoff/goal.md` from `references/goal-file-template.md`: the goal as one why-forward sentence with its `done_when`, the checkpoint rule, and the task table. The monitor loop writes statuses into the same file, so read and write it only through `goal-sync.py` (`read`, then `write --expect-sha256 <hash>`). It aborts instead of overwriting a concurrent update.
- Split by making one judgment per row: which capability does this work need?
  - `fast_worker`: mechanical, well-scoped, specification-complete work that the acceptance criteria alone can verify. Scaffolding, implementation, long-context edits, refactors, tests, build fixes, wide read-only scans, batch migrations, doc generation, and the scripts behind a build, a render or a media pipeline. Most delegable work.
  - `deep_reasoner`: ambiguous work where a wrong premise in step one is expensive to discover late. A hard diagnosis, a root cause, a config change whose wrong variant fails quietly.
  - `operator` (optional): work whose result comes from running tools and is not a text diff. A long build, a headless render, a media or model API call, an app launched for a visual check.
  - `-` for what the driver keeps inline: architecture, the split decision itself, cross-task integration, security- and correctness-critical paths, difficult product tradeoffs, final acceptance.

  Never route a quality-critical step to a cheaper identity to save money, and never spend the driver's seat on mechanical work. A request to save quota does not move a `-` row: say why it stays, and delegate the rest. Save on execution by delegating, not by cutting reasoning: a planner that thinks less takes more turns. Bound the planner's execution surface instead: keep its configured effort, and hand the planner a compact evidence packet so it does not rediscover the repo.
- Attack your own split before acting on it. Answer three questions in writing: does each delegated row really not need the expensive tier; does the integration cost of the split boundary eat the saving; and does each row's identity match the work's stakes, with a reason it is not a more expensive one. A row that survives all three is delegated. Any other row gets its identity corrected, is merged into a neighbour, or stays inline. Fix the split first, then delegate.
- Acceptance coverage. When the change alters user-observable behaviour at a real interface (a UI, an API surface, a mobile screen), add two `e2e_tester` rows, one that writes the gate and one that runs it, and read `references/e2e-acceptance.md`. Skip them for internal refactors, docs, config and pure library work. When the criterion fires and `e2e_tester` is not configured, say so once ("this change is user-observable; `/agent-handoff config --with-e2e` would add acceptance coverage") and continue without them.
- Artefact rows. A row whose result is not a text diff, with a command that outlasts a monitor tick, or that calls a paid service, follows `references/artefact-rows.md` whatever its identity. Read it before you write the row.
- Spec review. Every plan gets one independent read from `deep_reasoner` before it reaches the user. There is no toggle. Build the Spec Review Packet from `references/packet-templates.md` and submit it:

  ```bash
  bash "$HANDOFF_DIR/scripts/delegate-job.sh" submit \
    --repo "$REPO" --prompt-file "$prompt" --label spec-review \
    --role deep_reasoner --read-only
  ```

  Keep the label exactly `spec-review`: `resume` reads it to pick the cap.

  - The reviewer tags each finding `blocking` or `advisory`. Accept or decline each one and write down why. The findings are input to your judgment, never a verdict you apply unread.
  - Consensus means no blocking finding was declined. A declined advisory finding is recorded and goes no further.
  - Under the cap (`[review] spec_max_rounds`), `resume` the same job with the revised plan, the declined blocking findings and your reasons. The reviewer withdraws or keeps each one.
  - At the cap with a blocking finding still declined, escalate (`references/arbitration.md`).
  - A review job that fails, stalls, or returns neither findings nor a one-line "sound" verdict is not a round and is never consensus. Retry it once as a fresh job with the same packet. A second failure sets the status to `failed`, and Phase 2 waits for the user.
  - Track it in the goal file's `## Spec Review` block: the `status:` line, and one line per round with its jobId and your disposition of each blocking finding. Any status other than `not run` means the automatic review is spent for this run. An adjusted plan, a thin review and a resumed session never start a new one. Only an explicit user request does.
  - The review is informed, not blind: the plan is your own answer, so nothing is withheld. When `deep_reasoner` runs on the driver's own vendor it still runs, and the receipt notes `same-vendor`.

## Phase 2: Delegate

- Write each worker prompt from the Delegation Packet in `references/packet-templates.md`. Put the evidence the worker needs in the packet, so it does not spend the job rediscovering the repo.
- Tell the user the effective permission posture before the first delegation.
- Submit the row with its identity. Backend, model and effort then come from the config:

  ```bash
  prompt=$(mktemp)
  # ... write the delegation packet into "$prompt" ...
  bash "$HANDOFF_DIR/scripts/delegate-job.sh" submit \
    --repo "$REPO" --prompt-file "$prompt" --label <task-id> \
    --role <identity>
  ```

- Record the returned jobId in the row and set its status. Independent rows can be submitted in parallel.
- Add `--read-only` for a scan or review job that must not modify the repo. It wins over the identity's `permission_mode`.
- A second read. For a risky diff, or for a row's artefact evidence, add a `deep_reasoner` row submitted `--read-only` that depends on the row it reads. Its findings feed your Phase 4 review, and the review stays yours.
- Effort. The identity's configured effort is the default. Pass `--effort` or `--model` only when one task really needs an override, and write the effort in the row's `effort` cell. A job with no role and no `--effort` runs at `high`, or at `model` on cursor and antigravity. Efforts are per CLI, so one backend's value is refused on another, and `/agent-handoff config` shows what each accepts. Keep `xhigh` for the hardest quality-critical jobs and expect long runtimes, `max` for where correctness outranks runtime, and `medium` for trivial mechanical work only. On a subscription plan, do not save on effort at the price of rework.
- Permissions. Each identity runs under `permission_mode` `default` or `allow-all`, and the backend file says what each means on that CLI. A fix round inherits its parent's posture and read-only state, and a parent with no recorded posture counts as `default`, never `allow-all`. Do not read any of this as containment. A worktree or a packet's scope line limits scope by instruction only, and the backend file says what the CLI itself enforces. Verify edits and checks on disk.
- `submit` refuses in four cases, and the answer to each is a config change the user can see, never a workaround:
  - No Handoff config: run `/agent-handoff config` first. For a single job you may instead submit without `--role` and with an explicit `--effort`, and note it in the goal file's Notes.
  - A `--backend` that contradicts the named role.
  - An effort the backend does not accept.
  - A model the backend refuses (its backend file says which).
- E2E rows run in a worktree cut from a commit SHA (`references/e2e-acceptance.md`).

## Phase 3: Monitor

Monitor from evidence, never from chat text: the job's `status`, the tail of its `log.jsonl`, its `stderr.log`, and the repo (`git status --short`, `git diff --stat`, the fastest relevant check).

- One short job, expected under about five minutes: block on it with `delegate-job.sh status <jobId> --repo "$REPO" --wait --timeout 300`.
- Long or parallel jobs: start the built-in `/loop` skill at a 5-minute interval with a prompt like: read `.handoff/goal.md`, run `delegate-job.sh status` for every running jobId, tail each job's `log.jsonl` for its last event, update the statuses in the goal file, and when no job is left running, stop the loop and continue with Phase 4.
- The loop reads each row's `depends` and does not submit a row until every dependency is `done`.
- An anomaly is a `FAILED` status, or no new JSONL event across two consecutive ticks. Cancel the job, read `stderr.log` and the tail of `log.jsonl`, then resubmit with a corrected prompt or take the task back into the driver. Record it under the goal file's `## Anomalies`. The backend file lists how that CLI reports a failure. An arbiter job is the exception: resubmit it once and never take the dispute back.
- `permission_denied` in `status` and `result` counts the tool calls a worker was refused. A denied call does not change the exit code, so the worker ran to completion believing it had run checks it never ran. Treat any count above zero as a failed job whatever the exit code says: re-run the blocked commands yourself and record the anomaly. Zero does not prove the checks ran. The count is advisory, and the backend file says whether that CLI's denials are counted at all.

## Phase 4: Full review

- Collect each finished job: `bash "$HANDOFF_DIR/scripts/delegate-job.sh" result <jobId> --repo "$REPO"`.
- Read `permission_denied` first. A worker that was blocked could not run its own acceptance checks, so its report of them is worthless. Run them yourself.
- Review the complete diff yourself: `git diff` scoped to the files the job touched, plus the fastest relevant check. The whole diff, never a sample. Do not accept work you have not read. For an artefact row, `references/artefact-rows.md` says what you review in place of a diff.
- Review against the acceptance criteria in `.handoff/goal.md`, not against the diff alone. A reviewer given only a diff judges whether it is self-consistent and misses tasks that were never done: in controlled runs, diff-only reviewers caught 0 of 5 missing task briefs. Re-read each row's brief, then ask whether the diff satisfies it.
- With e2e rows in the run, do the Phase 4 sequence in `references/e2e-acceptance.md` before any fix round.
- Findings go back to the same worker session as one bounded fix round. `resume` lands on the parent job's CLI, in the same worktree:

  ```bash
  bash "$HANDOFF_DIR/scripts/delegate-job.sh" resume <jobId> \
    --repo "$REPO" --prompt-file <fix-notes>
  ```

- The cap is `[review] implementation_max_rounds` review passes. The original job is pass one and each `resume` adds one, so the default of 3 allows two fix rounds. It applies to every delegated implementation row, whatever its identity. `resume` refuses a round past the cap. Never get around it with a fresh `submit`.
- With findings still open after the last allowed pass, escalate (`references/arbitration.md`). Do not take the task back.
- When the arbiter rejects a row, set its diff aside so that independent rows do not run on top of it:
  - In the main repo, save the row's scoped changes, new files included, to `.handoff/rejected/<task-id>.patch`, and return those paths to their state before the job.
  - A worktree row keeps its worktree. Skip `cleanup`.
  - A row already integrated onto the feature branch is backed out with a revert commit, never a history rewrite.

  Rows that depend on it are held, and independent rows continue.
- Under the full protocol, delivery follows here: Stage 3 of `references/pr-delivery.md`. Skip it on every other run.

## Phase 5: Wrap up

- Mark accepted rows `done` in `.handoff/goal.md`. Leave rows in `rejected` or `arbitration` as they are: dependency holds and the user's decision on resume read them. Stop any `/loop` still running.
- Generate the Handoff Session Receipt (`references/session-receipt.md`).
- Run the wrap-up memory step (`references/wrap-up-memory.md`).
- Report to the user. The "Reporting back" list in `references/session-receipt.md` says what the report holds.

## In-session subagents

A row with an identity always goes through `delegate-job.sh`. Two in-session channels remain for edge cases, and neither is a delegated job: a one-shot Codex subagent (a rescue agent) for a stuck step that wants a second diagnosis, and a plain Task-tool subagent when no Handoff identity fits the work at all. A subagent has no jobId, no durable state, no monitor loop, no bounded fix round and no receipt evidence. Pick by whether the work needs that lifecycle, never by which meter you would prefer.

When you do spawn one, or the user asks for a step to run inline, pick the agent definition in this order:

1. A `handoff-*` agent, if setup generated one (`handoff-deep-reasoner`, `handoff-fast-worker`, `handoff-arbiter`, `handoff-e2e-tester`, `handoff-operator`). Its model and effort are the user's own setup choice.
2. The user's own similarly named agent (`deep-reasoner.md`, `fast-worker.md`, or one whose description clearly matches the identity). Use it as it is. Never rename or edit it.
3. A plain Task-tool subagent with the identity described in the prompt. A repo with no setup yet lands here every time, and that is normal.
