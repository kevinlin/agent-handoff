# Agent Handoff — the four-phase delegation flow

## Context

Handoff is one flow with one driver. Claude Code plans, splits, delegates, monitors, and quality-gates; a worker CLI executes whatever the split assigned to an identity, on that identity's configured backend — `codex`, a second `claude`, or `copilot`. Delegation buys exactly two things: execution on a subscription meter instead of the driver's, and a driver context window kept clear of execution history it will never read again.

Both claims are cheap to make and easy to fake. A driver that accepts a worker's summary has bought neither: it has bought a story about a diff it never read. The four phases below exist to keep the claim honest, and Phase 4 is where that happens.

This document is the design of record for those four phases as implemented. The prose contract users and agents actually load is `references/claude-driven.md`; this doc records *why* each phase is shaped the way it is, and which of its guards are enforced by a script versus asserted by a prompt.

### Scope

Covered: **Plan and Split**, **Delegate**, **Monitor**, **Review Gate**: the four phases where the split is decided, executed, watched, and judged. Also covered: the two review gates that run through them (spec review in Phase 1, implementation review in Phase 4), their round caps, and the arbiter ruling each gate escalates to when its cap runs out.

Not covered, deliberately, each having its own design of record or none:

| Out of scope | Where it lives |
| --- | --- |
| Job state tracking — the `.handoff/jobs/<jobId>/` layout as a subsystem | `scripts/delegate-codex.sh` header comment |
| Session evidence, transcripts, session receipt, cost receipt | `docs/specs/design_agent-handoff-evidence.md` |
| Identity configuration engine and setup wizard | `docs/config-schema.md` |

Phase 0 (preflight) and Phase 5 (wrap up) fall outside these four by construction: preflight is CLI probing and a start marker, and wrap up is the receipt. Job state is *read* by Phases 3 and 4 and its layout is taken as given here.

### Three invariants the four phases all rest on

**The identity's configured `backend` decides which CLI runs a job — always.** The task row names a capability tier; config turns that tier into a backend, model, and effort. Nothing in a prompt, a packet, or a routing decision made mid-run can move a job onto another vendor or meter. A per-job `--backend` contradicting a named role is refused rather than honoured, because changing where work bills is a config change the user should see (`/agent-handoff config`), not a side effect of delegating.

**All three backends are the same job.** A claude-backed or copilot-backed row gets the same jobId, the same job directory, the same monitor loop, the same bounded `resume` fix round, the same worktree lifecycle, and the same receipt evidence a codex-backed one gets. There is no second-class backend and no per-backend flow branch. Where the CLIs genuinely differ (effort enums, permission flags, session-id timing, binary identification) the difference is contained inside `delegate-codex.sh`.

**`.handoff/goal.md` is the only shared state, and it is the reference every gate judges against.** Not the stage before it, and not the maker's self-report. The file is written before any implementation exists, which is the only moment acceptance criteria are authorable: a criterion written after seeing the diff is a description of the diff, not a test of it.

### The five identities

Three core, two optional. Each is a `backend + model + effort` triple and nothing more: a capability tier, never a task assignment.

| Identity | Tier | What it takes |
| --- | --- | --- |
| `deep_reasoner` | core | ambiguous work where a wrong premise in step one is expensive to discover late; also reviews every plan before it reaches the user |
| `fast_worker` | core | mechanical, specification-complete work the acceptance criteria alone can verify |
| `arbiter` | core | the blind second solver for contested calls, and the judge a review gate escalates to when its round cap runs out; not assigned routine rows |
| `e2e_specifier` | optional | writes the acceptance gate: Gherkin with stable scenario IDs plus repo-native executable tests |
| `e2e_verifier` | optional | runs the reviewed gate against a pinned commit and produces a validated verdict |

A config carrying only the three core identities is complete. The optional pair is written only when setup runs `--with-e2e`, and `--status` prints all five with `<unset>` for the unconfigured ones; that listing is how a user discovers the add-on exists.

Two costs of making these full identities rather than task labels are real and accepted: `role` carries two meanings (capability routing in config, pipeline responsibility in the flow), and a user who wants one model for both e2e stages configures it twice.

Rows the driver keeps take identity `-`: architecture, the split decision itself, cross-task integration, security- and correctness-critical paths, final acceptance.

---

## Phase 1 — Plan and Split

The phase produces one artifact: `.handoff/goal.md`, holding a why-forward goal line with its `done_when`, the checkpoint rule, and a task table of `id · identity · task · acceptance · depends · effort · status · jobId`. No task row has been delegated when it ends, and every row's `jobId` is still empty. The spec review, and any escalation ruling on the plan, are real jobs, but they belong to the plan rather than to a row.

### One judgment per row

Splitting a task means answering one question per row, and nothing else: *which capability does this work need?* There is deliberately no separate "owner", "backend", or "meter" decision at the row level, because each of those would be a second place to get routing wrong and a lever for a driver to talk itself onto a cheaper model mid-run. Picking the identity picks the execution channel automatically.

The two failure directions are named so they can be checked: never route a quality-critical step to a cheaper identity to save money, and never spend the driver's seat on mechanical work.

Two escape hatches remain for edge cases, both in-process and neither a delegated job: a one-shot Codex subagent for a stuck step wanting a second diagnosis with no durable state, and a raw Task-tool subagent when no Handoff identity fits the work at all. Both lose the jobId, the durable state, the monitor loop, the bounded fix round, and the receipt evidence, which is the cost that keeps them edge cases.

### The adversarial gate

Before anything is delegated, the driver attacks its own split and answers three questions in writing:

1. Does each delegated row really not need the expensive tier?
2. Does the integration cost of the split boundary eat the saving?
3. Does each row's identity match the work's actual stakes, with a reason it is not a more expensive one?

A row surviving all three gets delegated. Anything else gets its identity corrected, merged into a neighbour, or kept inline. Fix the split first, then delegate.

The gate is **advisory**: it is answered in writing, and nothing blocks a submit that skipped it. That is an honest limitation, not an oversight: no script can judge whether a split is well-reasoned, and a gate that pretended to block would just be a prompt claiming to be a guard.

It used to be its own stage. Folding it into Phase 1 (v3.0.0, when the bundled `idea-king` companion skill was removed) was the right shape: it operates on a plan that does not exist until Phase 1 has drafted one, and it produces a corrected plan rather than an artifact of its own. A separate stage implied an independence it never had; the driver is both the maker and the judge here.

### Acceptance coverage

One criterion decides whether the optional e2e rows join the table: **add them when the change alters user-observable behaviour at a real interface**: a UI, an API surface, a mobile screen. Skip them for internal refactors, docs, config, and pure library work.

The criterion sits with the driver, not with setup state. When it fires but the identities are unconfigured, the driver says so once —

> this change is user-observable; `/agent-handoff config --with-e2e` would add acceptance coverage

— and then continues without them. Silent skipping would let a configuration choice quietly overrule the orchestrator's judgment about what the change needs. It does not block the run either.

An `e2e_verifier` row carries both the specifier row and the implementation row in its `depends` column. It needs the tests in its tree, not just the feature.

### Spec review — every plan, capped, and last

The gate above has the driver judging its own plan. `references/darwin-ratchet.md` names that failure directly: no agent should be sole maker and sole judge on high-risk work, and every later phase is judged against the plan this one produces.

So `deep_reasoner` takes a second responsibility alongside its task-row tier: an informed read of the plan, on its own configured backend, fired at the moment the workflow hands the plan to the user. It runs on every plan. There is no toggle, and it is not a sixth identity: the review wants exactly the tier `deep_reasoner` already names, and a sixth identity would mean configuring another triple for it.

Until v3.8.0 it was a boolean on the identity, `auto_review_spec`, default off. Default off meant that on most runs the plan every later gate judges against was read only by its author, which is the failure the review exists to close. The key is retired: the parser drops it on read, so an old config still loads and the next write removes it. An `auto_review_spec = false` no longer opts anything out, and that is the intended change.

**One round is a read and a response.** The reviewer tags each finding `blocking` or `advisory`. The driver accepts or declines each one and writes down why. The two sides have reached consensus when no blocking finding was declined. A declined advisory finding is recorded and goes no further: drivers decline nits routinely, and escalating on every one would put an arbiter job on nearly every run and teach the driver to accept findings just to avoid one.

**The cap is `review.spec_max_rounds`, default 1.** Under the cap, the declined blocking findings go back to the same reviewer session through `resume`, carrying the revised plan and the driver's reasons, and the reviewer withdraws or keeps each one. At the cap with a blocking finding still declined, the dispute goes to the arbiter as an escalation ruling (see *The two review gates, and where they escalate*). An approval sends the plan to the user as the driver revised it. A rejection is final: no task row is delegated, the run stops before Phase 2, and the user receives the plan with the ruling. Wrap up still emits a receipt, with phase `planning`, counting the review and arbiter jobs. The default is one because a second pass by the same informed reader mostly re-argues inside the framing it already accepted; once a blocking finding has been declined, a different reader adds more than another round with the same one.

**A failed review is not a round.** A spec-review job that fails, stalls, or returns neither findings nor a one-line "sound" verdict proves nothing, so it cannot reach consensus by having no declined blocking finding. The driver retries once as a fresh job with the same packet. The retry starts a new chain, which the round log in `## Spec Review` makes visible. A second failure sets the status to `failed`, and Phase 2 waits for the user.

Three rules hold across the rounds:

- **Once per run, and finished once started.** The `## Spec Review` block in `.handoff/goal.md` carries a `status:` line (`not run`, `round <n> running`, `awaiting arbitration`, or one of the terminal `consensus`, `approved`, `rejected`, `failed`) and one line per round naming its jobId and the driver's disposition of each blocking finding. Any status other than `not run` means the automatic review is spent: an adjusted plan, a review the driver found thin, and a resumed session all fail to start a new one. A resumed session that finds a non-terminal status finishes that chain. Only an explicit user request produces a new chain, with its own round count. The goal file is the right home for the marker because it is rewritten per run: the marker resets on its own, where a marker file elsewhere under `.handoff/` would survive into the next feature's run and suppress a review that should have happened.
- **Read-only in both directions.** The reviewer returns prioritized findings and nothing else: no edit to the spec, the goal file, or product code. Its findings are input to the driver's judgment, never a verdict applied unread. The cap bounds how long the argument over the driver's ruling can run.
- **Sequential, not parallel.** The user reads the adjusted plan, not a plan plus a review they have to reconcile themselves.

It runs as a real job (`submit --role deep_reasoner --read-only --label spec-review`), so it has a jobId and receipt evidence on any of the three backends. `--read-only` becomes `-s read-only` on codex, `--permission-mode plan` on claude, `--mode plan` on copilot. The label is load-bearing: `resume` reads it off the root job to decide which cap applies.

### Three instruments, and they are not variants of each other

| Instrument | Acts on | Independence |
| --- | --- | --- |
| adversarial gate | a split the driver just wrote | none — the driver attacks itself |
| spec review | that same plan, read by another agent | informed, not blind |
| blind arbitration | the problem, solved again from scratch | blind; a hint contaminates the run |
| escalation ruling | a review dispute that ran out of rounds | informed by both sides; nothing is withheld |

The arbiter's contamination rule deliberately does not apply to the spec review. The plan under review *is* the driver's answer, so withholding it would leave nothing to review. That makes it a second pair of eyes, not a second opinion: it inherits the driver's framing and will catch a wrong premise less often than an independent solve would, and the flow prose says so rather than letting a reader read arbitration into it. Same-vendor config gets the arbiter's treatment. The review still runs, and the weaker independence is recorded rather than claimed away.

The contamination rule does not apply to an escalation ruling either, which judges a dispute and cannot do that without hearing both sides. The `arbiter` identity serves both of the last two rows. They are different protocols, and the flow names them separately so that the independence of one is never read into the other.

### Concurrent writes: compare-and-set, not a lock

The Phase 3 monitor writes task statuses into the same file the driver is editing. `goal.md` has no lock, because write frequency is low and the driver usually writes alone. So `scripts/goal-sync.py` closes the race the cheap way:

- `read` prints `sha256=<hash>` and then the content.
- `write --expect-sha256 <hash>` replaces the file only if it still hashes to that value, and aborts telling the caller to re-read otherwise.
- Omitting the hash when the file already exists is refused outright, so "I forgot to read first" cannot present itself as a clean create.
- The replacement is atomic, so a partial file is never observable.

Compare-and-set beats a lock nobody would release correctly across a `/loop` tick boundary and a crashed session.

### Why this order

Acceptance criteria are authorable only before the diff exists. So the goal file is written first, the gate attacks the plan, and the outside read comes last — with the plan reaching the user already adjusted.

---

## Phase 2 — Delegate

One task row becomes one durable background job. The phase has two paths, and the design's whole job is keeping them apart.

### The prompt path carries the work

The driver composes the delegation packet in its own context from `references/handoff-template.md`: why-forward context, one-sentence task, acceptance written as commands, scope paths, checkpoint rule, no-commentary output discipline, at most three lines of lessons learned. It writes that to `$(mktemp)` and passes the path.

`delegate-codex.sh` does not generate, template, or validate this prompt. Its only check is that the file exists. That is authorship discipline rather than validation, and it is the deliberate choice: a packet the script assembled would be a packet the driver did not have to think about.

The packet never mentions backend, model, or effort. There is nothing to mention — routing is resolved on the other path.

### The config path carries the routing

`submit --role <identity>` resolves the triple through `handoff-config.py resolve`, whose precedence is session override → project → global → built-in defaults, merged per field. Every resolved value is recorded in the job's `meta` alongside the *source* it came from (`config:project`, `explicit`, `parent`, `default`), so a later question about why a job ran on a given model has a recorded answer instead of a reconstruction.

An explicit `--model` or `--effort` still wins per field for a task that genuinely needs an override. `--backend` does not.

### Fail-closed, in six places

| Condition | Behaviour |
| --- | --- |
| No Handoff config at all | hard error pointing at `/agent-handoff config` — never a default model |
| Identity missing backend, model, or effort | same hard error, named per identity |
| `--backend` contradicting a named role | refused, with the `handoff-config.py set` command that would make it legitimate |
| Effort not in that backend's enum | refused. The enums are per CLI, never one shared set: `ultra` is codex-only, `none` is copilot-only, and claude takes `low…max`. Copilot's enum is the CLI's superset; which efforts a given Copilot model takes is narrower still, and that narrowing lives in the wizard rather than here |
| `model = "auto"` on copilot | refused. An identity is a deliberate choice, and `auto` hands it back to the vendor per request, so the job's record would name what Copilot picked rather than what the repo configured. From v3.8.1 the wizard cannot offer it either: its Copilot model list is the account's entitlement catalogue, and `auto` is not a member of it |
| Invalid `permission_mode` | refused by config validation; only `default` and `allow-all` are accepted |

No wording in a prompt can move a job onto a different vendor or meter: routing resolves from config into `run.sh` flags and `meta`, and the prompt never carries those fields at all.

### The launch

`submit` creates the job directory, copies the prompt in once (never rewritten — it is the record of what was asked), writes `run.sh` (the command actually executed, flags included), then `nohup`s the worker detached with `stdin </dev/null` so a long prompt cannot leave it waiting on a terminal that is not there. The returned jobId goes into the row with `status=delegated`; `status` is the deduplication key across ticks and sessions, and a stale row causes duplicate delegation. Independent rows submit in parallel.

One backend needed a fix here: Copilot emits `sessionId` only on its terminal event, so a job that died mid-run would have no id to resume from. `--session-id` sets the UUID for a new session, so Handoff generates one at submit and persists it before launch. A crashed copilot job then has the resume parity the other two backends get for free.

### Worktree rows

E2E rows, and any row that must not share a tree, run in a worktree cut from an immutable SHA:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" submit \
  --repo "$REPO" --prompt-file "$prompt" --label <task-id> \
  --role e2e_specifier --worktree "e2e/<task-id>" --base "$SHA0"
```

Four protocol rules, driver-owned, identical on all three backends:

1. **Routing resolves against the main repo.** `--repo` is always the main repo. A worktree is a derived working directory, never a `--repo` value; passing one falls through to the global config and runs the wrong model.
2. **Cut from an immutable commit SHA**, never a branch name. `--base` is resolved through `git rev-parse --verify <base>^{commit}` *before the job directory exists*, so an invalid base fails clean. A branch name could advance between cutting the tree and reading the verdict, and then the verdict names a commit nobody tested.
3. **The worker commits on its worktree branch.** It does not merge, push, rebase, or remove the tree.
4. **The driver reviews, integrates, and cleans up.**

The tree lands at `<repo>/.handoff/worktrees/<jobId>` and `meta` records `worktree=`, `branch=`, `base_commit=`. A fix round inherits the tree: `resume` reads `worktree=` and `backend=` from the parent's `meta`, because none of `codex exec resume`, `claude --resume`, and `copilot --resume` accepts a `-C` — all three take cwd from the generated `run.sh`.

`cleanup <jobId>` removes the tree, is idempotent, and **refuses a worktree holding uncommitted changes**, reporting it rather than discarding work. `cancel` calls it and keeps the tree for inspection when it refuses.

One code path serves all three backends. The worktree protocol is pure Git and gains nothing from a per-backend implementation, so there is none — `tests/test_delegate_role.py` runs the whole lifecycle from one base class against codex, claude, and copilot. (Backend parity here was an open risk in v3.2.0, when the Claude path was driver-executed prose; v3.5.0 closed it by dispatching on the identity's backend inside the script.) The Task tool's `isolation: "worktree"` is still not used: no base pinning, no metadata, no cleanup contract.

### Packets are self-contained

A Git worktree receives **tracked content only**, and `.handoff/` is gitignored, so `.handoff/goal.md` and `.handoff/config.toml` do not exist inside a fresh worktree. Verified empirically rather than assumed.

Binding consequences:

- **No worktree job reads `.handoff/`.** The frozen goal excerpt, the spec, the acceptance criteria, and the artifact paths go into the prompt packet, exactly as the ordinary delegation packet already works. There is no shared-file channel to invent.
- **No job writes `.handoff/goal.md`.** Workers return artifact paths in their result; the driver writes them through `goal-sync.py`.
- An uncommitted user-supplied spec must be inlined into the packet or committed before the tree is cut. The driver picks; it cannot be left implicit.

The e2e packets are the only ones that **replace** the shared template's "Do not commit, push, deploy, publish, or touch secrets" line, with a commit-on-this-worktree-branch rule. Appending a contradicting permission would leave the worker choosing between two rules, so the line is swapped, not extended.

### The permission posture, stated plainly

Each identity selects `permission_mode=default|allow-all`. Default uses Claude `dontAsk` with `Read Glob Grep Edit Write Bash`, inherited Codex config without sandbox flags, or Copilot `--allow-all-tools` retaining path/URL checks. Allow-all means *use the provider's native unrestricted mode*, not force three CLIs into one security posture. Only claude changes behaviour under default; Codex and Copilot retain their previous flags. Read-only wins over the retained Claude env override and configured posture; the env value is still validated first. Read-only drops Claude's worker allowlist and Copilot's allow-all flags. Resume inherits posture and read_only; missing parent posture means default, never allow-all. Warnings follow the effective concrete mode. No OS-level sandbox is added for Claude default. Codex denials are not counted, and permission_denied is advisory, not enforced. Verify edits and checks on disk.

| Backend | Default | Allow-all |
| --- | --- | --- |
| claude | `--permission-mode dontAsk --allowed-tools Read Glob Grep Edit Write Bash` | `--permission-mode bypassPermissions` |
| codex, fresh and resume | No sandbox flag or override; inherit the user's config | `--dangerously-bypass-approvals-and-sandbox` |
| copilot | `--allow-all-tools` | `--allow-all-tools --allow-all-paths --allow-all-urls` |

Under `dontAsk` a call that is not already approved is denied rather than run; under `bypassPermissions` it runs. That is the only difference, and how much it buys is the user's configuration to decide rather than Handoff's: `deny` and `ask` rules were measured producing hard denials under **both** postures, and a tool those same settings already approve was reached under both, `WebFetch` included. This changes the permission-rule layer only -- no OS-level sandbox, no workspace-only IO, no denied network -- so `default` is a narrower posture, not containment.

The deliberate research deviations are documented in `docs/config-schema.md`: codex default inherits config instead of forcing workspace-write, preserving the worker-commit contract; exec has no ask-for-approval flag and never prompts; copilot default retains allow-all-tools because Handoff cannot enumerate each repo's test/build/lint commands. Copilot path and URL verification remain on until allow-all.

Read-only uses codex read-only sandbox overrides, Claude plan without the worker allowlist, or Copilot plan without allow-all flags. The Copilot combination is deliberately avoided: probing found zero denials and claims of writes that never happened when plan and allow-all-tools were combined.

Job meta records requested permission_posture and permission_posture_source separately from effective permission_mode. Receipt schema remains v6; posture evidence stays in job meta. Worktrees and packets are scope controls, not enforced containment.

---

## Phase 3 — Monitor

Progress is read off disk, never held in context. The phase has one decision, and two cycles.

### The decision

*How long is this job expected to run?* Everything else follows.

**Under about five minutes** — block on it: `status <jobId> --wait --timeout 300`. Note the exit-code shape: with `--wait`, a timeout while still RUNNING returns 2; otherwise DONE and RUNNING both exit 0 and only FAILED and CANCELLED exit non-zero. The printed `state:` line is the real signal a caller must parse, not the exit code.

**Long or parallel** — arm the built-in `/loop` skill at a five-minute interval. The tick prompt is the whole monitoring program.

### The tick, in order

1. Read `.handoff/goal.md` first — the split and the statuses.
2. Then the job directories, for the jobs that file says are running.
3. `status` each running jobId; tail its `log.jsonl` for the last event.
4. Write the updated statuses back through `goal-sync.py`.
5. No job left RUNNING → stop the loop, continue to Phase 4.

Context carries nothing between ticks, by design. That is what lets a tick fired an hour later, or a session resumed after a crash, pick the run up without rebuilding anything, and what keeps monitoring off the metered path.

`job_state` derives the four states from files rather than from memory: `cancelled` marker → CANCELLED; `exit_code` present → DONE or FAILED by its value; live `pid` → RUNNING; and a dead pid with no `exit_code` → FAILED, which is how a crash or a reboot reports itself instead of hanging as RUNNING forever.

`status` makes one parsed pass over the log for both the last event type and the denial count. Counting denials by `grep` would miss valid JSON with whitespace after a colon and would match an unrelated `code` field anywhere on the line; the denial shapes also differ per backend, which a pattern cannot tell apart. The backend is read from `meta` and passed in as an *input*, never sniffed from event shape — Copilot's terminal event is `type: "result"`, the same type name Claude's stream-json uses with a completely different payload, so a backend-blind parser mis-reads a copilot log without erroring.

### `depends` is the release valve

The loop reads each row's `depends` column and does not submit a row whose dependencies have not reached `done`, however idle the tick looks. This is the only reason the column exists: the monitor decides what to submit next and has nowhere else to learn that an `e2e_verifier` row waits on two rows rather than one.

### The anomaly rule

A `FAILED` status, or two consecutive ticks with no new JSONL event, is an anomaly. Cancel the job, read `stderr.log` and the tail of `log.jsonl`, then either resubmit with a corrected prompt or take the task back into the driver. Record it either way. An arbiter job is the exception: it is resubmitted once and never taken back (see *The escalation ruling*).

Reading the log is not optional on copilot: an API failure can arrive with `stderr` empty and the detail carried only by a `session.error` event in `log.jsonl`, which is also where `result --json` reads it from. And an idle or API timeout arriving after model events is not a login error. The distinction matters because the wrong diagnosis produces a resubmit that fails the same way.

### `permission_denied` is a failed job whatever the exit code says

`status` and `result` count Claude and Copilot denials. **Codex denials are not counted**: its sandbox refusals match neither event shape and result extraction discards command outcomes. The shared Claude-shaped test event proves nothing about Codex denial detection. **`permission_denied` is advisory, not enforced**: job state derives DONE from exit code, and status warns but still succeeds. Zero does not establish that checks ran; the prompt-shaped guard stays prompt-shaped.

A denied tool call does not move the exit code. So a worker that was blocked ran to completion believing it had run checks it never ran, and its self-report of those checks is worthless. Treat any non-zero count as a failed job, re-run the blocked commands yourself, and record it as an anomaly.

---

## Phase 4 — Review Gate

This is the phase that makes the cost claim honest. The whole diff is read and judged against the written brief; rework is a `resume`, and it is bounded.

### Collect, then check the denial count first

`result <jobId>` reduces the event stream to what a review needs: session id, usage, command count, any `session.error`, the denial count, and the last agent message. The full stream stays on disk for when it is needed.

The denial count is read before anything else, for the reason above.

### Judged against the brief, not against the diff

Re-read the acceptance column and each task brief from `.handoff/goal.md`, *then* read the complete diff — `git diff` scoped to the files the job touched, plus the fastest relevant check. The whole diff, never a sample. Do not accept work you have not read.

The question is "does this diff satisfy that brief", never "is this diff self-consistent". The distinction is load-bearing and measured: in Superpowers 6, diff-only reviewers caught **0 of 5** missing task briefs. A reviewer holding only a diff confidently redefines the spec as "consistent with whatever changed" and misses tasks that were never started at all.

### The e2e sequence, before any fix round

When the run carries e2e rows, six steps run in this order:

1. **Review the specifier's scenarios against the acceptance column** in `.handoff/goal.md`. This step is the load-bearing one: a generated gate weaker than the goal's criteria will green-light a broken feature, and the verifier's verdict inherits that weakness.
2. **Record the hash at that moment** — sha256 over the reviewed `.feature` files, path-ordered.
3. **Integrate specifier and implementation** onto the feature branch, producing one combined commit.
4. **Submit the verifier pinned to that commit**, then validate its verdict before acting on it: `validate-verdict.py <verdict> --expect-scenarios-sha256 "$REVIEWED_SHA"`.
5. **PASS** goes to the merge decision. **FAIL** is a product finding routed to the original implementer, never to the verifier. **BLOCKED** proved nothing: resolve the prerequisite and rerun, never read it as PASS.
6. **`cleanup <jobId>`** once the worktree is merged or abandoned.

Nothing merges to `main` mid-flight. Acceptance artifacts and implementation meet on the feature branch, because merging tests to `main` ahead of the implementation would leave `main` red. `main` is reached only after a PASS plus the driver's final review, which keeps merge inside the existing hard-stop rule.

This ordering is also what keeps the pair off the maker-is-also-judge failure: the specifier writes the gate, the verifier runs it, the driver judges the gate itself, and product fixes go back to the original implementer.

### Why the verdict is a validated artifact

The verifier writes `.handoff/e2e/<jobId>/verdict.json` and the driver validates it before acting. This follows the repo's established pattern (generated, then re-checked, never hand-typed) because the verdict is the input to a merge decision, and a prose block lets an agent type `result: PASS` after a non-zero exit.

The validator's value is the cross-field agreement, not the shape. Shape failures short-circuit, so the rule checks can index freely:

| Result | Requires |
| --- | --- |
| any | `scenarios.total == len(ids)`, `passed <= total`, and `scenarios_sha256` equal to the hash the driver recorded at review time |
| `PASS` | `exit_code == 0`, `passed == total`, `total > 0`, empty `findings` |
| `FAIL` | at least one finding, and either a non-zero `exit_code` or `passed < total` |
| `BLOCKED` | `passed == 0` and a finding naming the blocker; `command` and `exit_code` may be null — that is the point of the state |

The validator compares the verdict's reported `scenarios_sha256` against the hash the driver recorded; it does not recompute the hash from the files the run executed. A weakened scenario is caught when the verifier reports the changed hash truthfully, or when the driver recomputes the hash over the pinned commit, which is the check that does not depend on the verifier's honesty.

### What the verifier may and may not touch

**Frozen:** Gherkin scenario text and IDs, expected values in assertions, and the identity of the thing an assertion is about. **Repairable:** launch and runner scaffolding — start commands, base URL, waits, fixtures, selector resolution that does not change what is being asserted. **Forbidden:** product code.

A semantic edit does not get disclosed and accepted; it **invalidates the run**. The verifier reports it and stops, the driver re-reviews the changed acceptance semantics, records a new hash, and the run restarts clean.

The hash lock covers the `.feature` files only. Executable spec files legitimately contain repairable scaffolding, so they cannot be hashed whole; for those the verifier lists every changed file in `harness_edits` and the driver diffs exactly those. The validator checks only that `harness_edits` is a list; comparing it against the commit's actual changes is the driver's job. A reported lock on the behavioural contract, bounded human review on the rest — stated plainly rather than implying the hash covers everything.

The specifier keeps scaffolding separate from specs wherever the stack allows, which is what keeps the verifier's permitted repairs outside the files carrying behavioural meaning and keeps that re-review small.

The verifier's **target** is named in its packet — an approved local or ephemeral test target. A remote, production, destructive, or billable target requires explicit user authorization recorded like any other hard stop. The verifier does not discover its target by guessing.

### Rework is a resume, and it is bounded

Findings go back to the same worker session:

```bash
bash "$HANDOFF_DIR/scripts/delegate-codex.sh" resume <jobId> \
  --repo "$REPO" --prompt-file <fix-notes>
```

`resume` creates the chain's next round, `<root>-r<n>`, with its own `prompt.md`, and inherits the parent's `backend`, `role`, `effort`, and `worktree` from `meta` — a fix round is never a re-decided route or an anonymous receipt entry. It requires a session id (no id in the parent's log means no resume) and refuses while the parent is still RUNNING.

The fix packet carries only the prioritized findings and the acceptance criteria that failed, plus "continue end-to-end from here". Not the whole packet again: the resumed session still holds the original.

**The cap is `review.implementation_max_rounds` review passes, default 3.** A review pass is one driver read of the diff against the brief: the original job is pass one and each `resume` adds one, so the default allows two fix rounds, the bound the flow carried before v3.8.0. The cap applies to every delegated implementation row whatever its identity; `fast_worker` is the common case, not the only one. An e2e verifier FAIL is a round on the implementation row's chain, because that is where the fix goes. With e2e rows in the run, an implementation row's `done` is provisional until the verifier's PASS: a FAIL moves it back to `rework-<n>`, sends the verifier row back to `pending`, and holds every other row that depends on it. A dependent already running may finish, and its result is not accepted until the row is `done` again. A rerun after BLOCKED is not a round, because nothing was reviewed: it is a fresh verifier `submit` pinned to the same commit, never a `resume`, so the cap never counts it.

When the last pass still has findings open, the argument no longer ends in a takeback. It goes to the arbiter as an escalation ruling (see *The two review gates, and where they escalate*). An approval accepts the diff, overruling the driver's open findings, and the row goes to `done`. An approval never stands in for a PASS. When a validated e2e FAIL is among the open findings, approval means the arbiter judged the scenario wrong and the product right: the scenario goes back to the driver's review in step 1 of the e2e sequence, a new hash is recorded, and the verifier reruns. `main` still needs its PASS.

A rejection is final: the row becomes `rejected`, rows depending on it are held, independent rows continue, and the run ends with its receipt. The rejected diff does not stay in the tree the other rows run against. In the main repo the driver saves the row's scoped diff as `.handoff/rejected/<task-id>.patch` and restores those paths. A worktree row keeps its worktree, uncleaned, which is also what lets a later `resume` find it. A row already integrated onto the feature branch for e2e is backed out with a revert commit, never a history rewrite. The user picks the rejected row up on `/agent-handoff resume`.

The takeback this replaces had the driver finish a task it had just failed to get accepted, with nobody checking whether its own findings were right. Takeback survives only for Phase 3 anomalies, where a job failed or stalled and there is no dispute to rule on. Escalations and their outcomes go to the memory protocol, which is how it learns that a task type does not delegate well.

### Hard stops

Merge, production, tags, force-push, deletion, destructive migration, and external publish are each an independent hard stop needing its own fresh imperative sentence from the user. None of them is implied by "finish it", by an earlier authorization for a different action, or by a question about whether to proceed. Phase 4.5 delivery runs only on explicit authorization.

---

## The two review gates, and where they escalate

Both gates are a maker and a reviewer arguing over an artifact against the goal file, bounded by a cap, with a third reader for the argument that does not settle. They differ in who plays which part:

| | Spec review (Phase 1) | Implementation review (Phase 4) |
| --- | --- | --- |
| Artifact | the plan in `.handoff/goal.md` | a delegated row's diff |
| Maker | driver | the row's worker |
| Reviewer | `deep_reasoner` | driver |
| Consensus | no declined blocking finding | the driver accepts the diff |
| Cap | `review.spec_max_rounds`, default 1 | `review.implementation_max_rounds`, default 3 |
| On rejection | the run stops before Phase 2 | the row is `rejected` and its diff set aside; independent rows continue |

### One unit for both caps

A round is a review pass, in both gates. The first review is round one, on the original job; every `resume` adds one. Counting fix rounds instead would have given the two thresholds different units, with spec `1` meaning one read and implementation `3` meaning four.

### The caps are config, and `resume` enforces them

```toml
[review]
spec_max_rounds = 1
implementation_max_rounds = 3
```

Both are integers of at least 1, resolved per field from project → global → built-in defaults, so a config with no `[review]` section runs on 1 and 3. There is no session override. `resume` runs in its own process and reads the config itself, so a per-call override would let the driver believe in a cap the enforcing process never read; a user who wants more rounds for one run raises the project value. `schema_version` stays `2`, by the same rule that let identities be added without a bump.

The engine's write ownership extends from the identity sections to `[review]`; `[routing]`, comments, and unknown sections still round-trip byte-for-byte. `handoff-config.py set-review --spec-max-rounds N --implementation-max-rounds N` writes the section, because `set` takes an identity. The setup wizard and terminal setup set both, where they used to carry the spec-review checkbox.

`resume` finds the chain's root by walking each job's `meta` `parent=` line back to the job whose `mode` is `fresh`, and counts the new round as the chain's length plus one. It reads the root's `label` and applies `spec_max_rounds` to a `spec-review` chain and `implementation_max_rounds` to anything else. The `-r<n>` suffix is not trusted for either: a fresh job whose own label ends in `-r2` would read as a resume. A round past the cap is refused before its job directory exists, naming the gate and telling the driver to escalate. No flag bypasses it: more rounds means raising the cap in config, where the change is visible. An invalid config now blocks a fix round, where `resume` never read config before; failing closed is the correct direction.

Of the guards on the argument, the cap is the one a script can hold, so it leaves the prompt. Two stay behind: a fresh `submit` in place of `resume` starts a chain the cap never counts, and nothing mechanical makes the driver escalate rather than quietly finish the task itself.

### The escalation ruling

Blind arbitration sends the same problem to two solvers, and neither may see the other's answer. An escalation ruling is the opposite on purpose. It judges a dispute, so it gets the whole dispute: the brief or plan, the acceptance criteria, the evidence (the diff with the driver's check results, or the revised plan), and every round's findings with the other side's response. Withholding either side would leave it ruling on half an argument.

It runs as a real read-only job, `submit --role arbiter --read-only --label arbitrate-<task-id>` (`arbitrate-spec` for the plan), built from the Arbitration Packet in `references/handoff-template.md`. The packet asks for one thing: a first line of `verdict: approve` or `verdict: reject`, then one reason per disputed point, citing the evidence. It does not ask for a better solution, and it keeps closed the points both sides already agreed on.

Three rules follow:

- **The ruling binds.** The driver does not re-argue it. An approval of a diff overrules the driver's open findings, which changes who has the last word, and that is why every ruling is recorded where the overruled findings stay visible.
- **No verdict is not approval.** A verdict is well formed only when the output's first line is exactly `verdict: approve` or `verdict: reject` and the reasons do not contradict it. An arbiter job that fails, stalls, is cancelled, or returns anything else is recorded as an anomaly, and the one permitted recovery is a single fresh arbiter job with the same packet. A dispute is never taken back, because a takeback settles it for the side that raised it. A second failure, or a `submit` refused before any job exists (an invalid config, a missing arbiter identity), is recorded as `no verdict`, with the job ids or the refusal message in place of a jobId. The row, or the plan, stays in `arbitration` and the run wraps up; only a well-formed verdict or the user's decision on resume moves it. It is the rule e2e BLOCKED follows, for the same reason.
- **Same-vendor is allowed and recorded.** When the arbiter's backend matches either party's, the ruling carries `same-vendor`, as blind arbitration already does.

### Every ruling is written down

- **`.handoff/goal.md`**: a `## Arbitration` block, one line per escalation whatever the outcome, giving the gate, the task (`plan` for the spec gate), the arbiter's jobId, the verdict (`approve`, `reject`, or `no verdict`), a one-line reason, and `same-vendor` where it applies. Task status gains `rework-<n>`, `arbitration`, and `rejected`; `taken-back` stays, for anomalies.
- **Notes**: a rejection adds a handover covering what the reviewer found, what the arbiter ruled, and what continuing would take (raise the cap, rewrite the brief, or take the task over). This is what the user reads when they come back.
- **The receipt**: `anomalies` carries `arbitration: <task> approve|reject (<jobId>)`. The arbiter job already sits in the job counts and `roles_used`. The schema stays at v6; `docs/specs/design_agent-handoff-evidence.md` records why.

`/agent-handoff resume` presents rejected rows first and never reopens one on its own. A rejection is the user's to act on. Reopening one is a new run, because the rejected run already emitted its receipt: Phase 0 stamps a new start, so the new receipt counts only the reopened work. The user picks the route. Raising the cap and resuming the existing chain needs the rejected work back in place, either the saved patch applied or the kept worktree; rewriting the brief starts a new chain with a fresh count; or the driver takes the task over.

---

## Where the flow fails closed, and where it does not

The honest summary, because a design that claims uniform enforcement is lying about the prompt-shaped parts:

| Guard | Enforced by | Failure mode when skipped |
| --- | --- | --- |
| Identity → backend/model/effort routing | script (`submit`, hard error) | none available — no config means no job |
| `--backend` contradicting a role | script (refused) | none available |
| Effort enum per backend | script (refused) | none available |
| Copilot model and effort pair | script + catalogue (the wizard refuses a model its served catalogue does not list, and an effort that model does not accept) | a config written by hand or by the terminal `--role-model` path reaches its first job unchecked; the smoke test and `/agent-handoff tryout` are where it surfaces |
| Worktree base pinning | script (`rev-parse` before the job dir exists) | none available |
| Dirty worktree cleanup | script (refuses, reports) | none available |
| Concurrent `goal.md` writes | script (compare-and-set) | none available |
| Verdict cross-field agreement | script (`validate-verdict.py`) | none available if run |
| Scenario hash lock | script compares the reported hash; recomputing it over the pinned commit is the driver's check | a verifier reporting the reviewed hash over changed files; behavioural meaning moved into executable spec files |
| Review-round cap, both gates | script (`resume` refuses a round past the cap, before the job dir exists) | a fresh `submit` in place of `resume` starts a chain the cap never counts |
| Review cap values | script (config validation, integers of at least 1) | none available |
| Adversarial gate | prompt | a split nobody attacked gets delegated |
| Spec review on every plan | prompt | a plan only its author read |
| Review against the brief | prompt | the diff-only review, 0 of 5 |
| Escalation at the cap, not a takeback | prompt | a disputed task the driver finished itself with nobody ruling on it |
| Every ruling recorded | prompt | an `## Arbitration` block missing a ruling the arbiter's job log holds |
| Hard stops | prompt | an unauthorized merge |

**The review gate is the weakest-enforced guard in the system, and the most load-bearing.** Config resolution, packet routing, verdict validation, worktree pinning, and the round cap all fail closed. Judging a diff against a written brief is a rule in a prompt, because no script can decide whether a diff satisfies a brief. Everything the flow claims about delegation being safe rests on that one prompt-shaped check actually being performed.

## Risks and known limits

- **The adversarial gate has no independence.** The driver attacks its own split. The spec review adds an outside reader on every run, but it is informed rather than blind and inherits the driver's framing.
- **Prompt-shaped guards degrade silently.** A skipped review, a takeback where an escalation belonged, an unrecorded ruling, an unattacked split. None of these produce an error. They produce a run that looks identical to a good one.
- **An approval overrules the driver.** Before v3.8.0 the driver had the last word on a contested diff. Now an arbiter approval accepts a diff with the driver's findings still open. The `## Arbitration` line keeps the overruled findings visible, and the ruling is informed rather than blind, so it inherits both parties' framing and is weaker evidence than a blind solve.
- **The cap trusts the label.** `resume` applies the spec cap only to a chain whose root job is labelled `spec-review`. A spec review submitted under another label is capped as an implementation chain.
- **No OS-level sandbox for claude `default`.** This release changes the permission-rule layer only; enforced sandboxing, workspace-only IO and denied network remain outside scope. Network denial interacts with installing and testing, and the Darwin ratchet permits one dimension per change.
- **Codex denials are not counted.** The current scanner recognizes Claude and Copilot events, not Codex sandbox refusals. Result extraction discards command outcomes; denial detection is out of scope.
- **`permission_denied` is advisory, not enforced.** DONE follows the exit code and status warns but still succeeds. The driver must verify checks independently.
- **The Copilot model catalogue is an undocumented endpoint.** The wizard reads the account's entitled models over HTTP with the token `gh auth token` returns. GitHub publishes no stable contract for that endpoint, and Enterprise Cloud data-residency tenants serve their catalogue from a per-tenant host where the request returns nothing usable. The wizard then offers no Copilot model and names the fix rather than guessing one, and a Copilot identity already in the config keeps its model. `docs/config-schema.md` holds the design.
- **Nothing checks a Copilot pair before the config is written.** Until v3.8.1, apply ran each configured pair past the real CLI and refused to write a config the CLI rejected. The catalogue answers that question for anything chosen in the wizard, and claude and codex never had such a check, so the three backends are now equal here. The wizard's own API refuses a model its served catalogue does not list, so the remaining hole is narrow: a config written by hand, or by the terminal `--role-model` path, reaches its first job unchecked, and the smoke test and `/agent-handoff tryout` are the proof that the pair runs.
- **The hash lock is partial.** Specifier discipline about separating scaffolding from specs is what keeps the bounded review small; if that discipline slips, the review grows and gets skipped.
- **Forward compatibility fails closed.** `validate_config` raises on an unknown identity, so a five-identity config errors on a pre-3.2 engine. Acceptable: the skill and its config version travel together, and failing closed is the correct direction.
- **A pre-3.8 engine ignores `[review]`.** The parser reads only schema metadata, `[routing]`, and the identity sections, and carries anything else through untouched, so an older engine runs its own gates: an optional spec review and a two-round cap. That is the one version skew here that fails open. It is accepted because the flow prose, `resume`, and the config reader ship together in one skill directory.
- **Cost of the acceptance pair.** Two extra jobs per user-observable change. The Phase 1 criterion is the control; if it fires too often the criterion tightens, not the identities.
- **Cost of the review gates.** One spec-review job on every run, unconditional since v3.8.0 where the default config used to spend nothing; one more per extra spec round; one arbiter job per escalation. The caps are the control. Raising a cap to avoid escalation only buys more rounds of the same argument.
- **This covers two stages of a longer acceptance pipeline, not a whole one.** There is no cleanup stage and no mutation-testing stage here, and Handoff's diff review and fastest-relevant-check are not substitutes for either.

## Changing this flow

`references/darwin-ratchet.md` is the gate. Change one dimension at a time (planning, the split decision, delegation, monitoring, review, permissions, reporting), validate against `test-prompts.json` or a real miniloop, and keep the change only when repo evidence improves. Do not let one agent be both sole maker and sole judge on a high-risk change to the flow itself; the flow's own instruments exist for exactly that reason.
