# Changelog

## v3.7.1 (2026-09-10)

- Add per-identity `permission_mode` (`default` or `allow-all`) across config, delegation, terminal setup and browser setup.
- Claude default now uses dontAsk with the worker tool set; Codex and Copilot default flags stay unchanged. Allow-all selects each provider's native unrestricted mode.
- Resume preserves parent read-only state and posture; absent legacy posture means default.
- Record requested posture/source and effective mode in job meta. Warnings follow effective mode.
- Document research deviations, the lack of Claude OS-level sandboxing, uncounted Codex denials, and advisory denial reporting. Receipt schema stays v6.

## v3.7.0 (2026-09-09)

### A third execution backend: GitHub Copilot CLI

- feat: `backend = "copilot"` runs a delegated job as `copilot -p --output-format json`, with the same jobId, `.handoff/jobs/` state, monitoring loop, bounded `resume` fix round, worktree lifecycle, and receipt evidence the other two backends already had. `delegate-codex.sh` branches in four places — binary discovery, the exec line, the effort enum, and the event parser — rather than gaining a parallel implementation, which is the test of whether v3.5.0's backend seam was an abstraction or two special cases wearing one name
- feat: the copilot binary is identified by its `--version` output, not by its name. AWS Copilot CLI is also called `copilot`, and PATH order decides which one `command -v` finds; both were present on the probing machine. Submit, resume, the setup availability check, and the wizard all apply the same precedence and the same identity check, so setup cannot reject an install that the worker would have resolved. `HANDOFF_COPILOT_BIN` overrides, and is identity-checked too
- feat: a copilot job's session id is assigned at submit rather than extracted at the end. Copilot emits `sessionId` only in its terminal event, so a job that died mid-run would have had nothing to resume; Handoff generates the uuid, writes `$JOB/session_id` before launch, records `session_id_source=assigned` in `meta`, and a job killed before its terminal event resumes with its context intact. An assigned id is a claim on a session and not a guarantee that one exists, so `resume` on a job that failed before session creation reports that plainly instead of presenting it as a missing-session bug
- fix: `--mode plan` and `--allow-all-tools` are never generated together, and that is a correctness requirement rather than a preference. Probed together, plan mode still won on disk but the agent emitted **zero** denial events, exited 0, and its final answer claimed a file write and a shell mutation that never happened. Nothing in the event stream marked the failure. `--read-only` therefore swaps `--allow-all-tools` for `--mode plan`, and a test asserts the exclusivity
- feat: a delegated copilot job otherwise runs with `--allow-all-tools`, for the reason the v3.5.1 retro gives: a background job has no approval surface, so any rule that would prompt denies instead, and a worker that cannot run its own checks reports gates it was refused. The safety net is Copilot's typed `error.code: "denied"` event, counted in the parsed pass rather than by grep, and the flow prose treats any non-zero count as a failed job whatever the exit code says. The worktree and the packet are scope controls, not enforced containment: a plain `submit` runs in the main repo
- feat: every generated copilot `run.sh` passes `--no-remote --no-remote-export`, because session export to GitHub web and mobile is on by default, and never `--share`, `--share-gist`, `--yolo`, `--allow-all`, `--worktree`, or `--enable-memory`. The argv tests assert the absence, not only the presence
- feat: the effort enum is Copilot's own, `none|minimal|low|medium|high|xhigh|max`. Which values a given model accepts is decided per model by the API, and Handoff passes the configured effort and surfaces the rejection rather than downgrading silently. `model = "auto"` is refused for a configured identity on conceptual grounds — an identity is a deliberate `backend + model + effort` choice, and `auto` would make the receipt record what the vendor picked rather than what the repo configured
- feat: the setup wizard renders Copilot's model as a text field, not a selector. Copilot publishes no catalog: `copilot model` is not a command, and `availableModels` inside a run's JSONL is `auto`'s routing candidate set, which moved on the probing account inside a day while the configured model kept working. Opening the wizard makes no copilot subprocess call and costs nothing. Apply and `--smoke` validate the model-and-effort pair against the real CLI and surface its wording verbatim; that check is free in one direction only, since a rejected pair is refused before any session while a valid one starts one and costs a premium request
- **breaking**: receipt schema v6 adds `copilot_jobs` and `copilot_job_durations`, and `roles_used[].host` accepts `copilot`. The three counts partition the run's jobs by each `meta`'s own `backend=` line; a copilot job is never folded into either existing count. A v5 receipt fails `validate-receipt.py`, which is the intended signal to regenerate it. The flat receipt grammar stays scannable, but the Python internals become backend-keyed tables, so a fourth backend is a data row rather than a fourth branch in five files
- feat: the cost receipt reads Copilot's `usage.json` and reports its meter as what it is — premium requests and nano-AIU, AI credits and never a currency figure. The two scopes were measured and the fold respects them: `tokenDetails` counters are per invocation and sum across a parent and its fix round, while the top-level credit totals are cumulative for the session and would have double-counted every resume. A rejected run writes a zeroed `usage.json` and a killed one writes no file at all, so a measured zero stays distinct from unknown
- feat: the transcript viewer parses copilot logs, gated on the payload's backend rather than on the event shape. Copilot's stream ends with `type: "result"`, the same type name Claude's `stream-json` uses and a different payload, so a backend-blind parser mis-reads a copilot log without erroring. Ephemeral events are dropped — one small job produced 82 of them — and `tool.execution_start`/`tool.execution_complete` fold by `toolCallId`. The metadata-free drag-and-drop path infers the backend from an unambiguous event, never from `result`
- test: the backend-agnostic lifecycle mixin gains a copilot subclass, and binary discovery is tested rather than asserted in prose: two fake CLIs, both PATH orderings, an explicit `HANDOFF_COPILOT_BIN`, an AWS-only install failing closed, and a resume whose recorded parent path now resolves to a different tool

`schema_version` for the config file stays `2`: a new backend enum value is not a document-shape change. A `backend = "copilot"` config fails closed on a pre-3.7 engine, which is the correct direction for version skew.

## v3.6.1 (2026-09-09)

### Read what a finished run consumed

- feat: `/agent-handoff cost-receipt [<receipt-file>]` reads a saved Handoff Session Receipt together with the job logs it indexes and writes a markdown and an HTML page to `.handoff/cost-receipts/`. Until now the receipt counted jobs and timed them while the token counters sat unread in each job's `log.jsonl`. The selector is optional; omitted or `last` means the newest saved receipt, picked by the stamp in its filename rather than by mtime, so copying a receipt does not reorder the set
- feat: every number is measured. Codex jobs contribute token counters and no cost figure, because that work runs on a subscription and the Codex CLI emits none; claude jobs contribute the CLI's own `total_cost_usd`, quoted unrounded and labelled CLI-reported. No price table is applied to anything
- feat: two summary figures — what ran on a Codex subscription, and what ran outside the driver session. Codex jobs are in both populations by design, the page says so, and no line adds them. No saving, avoided cost, or context-saved figure appears anywhere; the tests refuse the words
- feat: a missing measurement prints `unknown` and a measured zero prints `0`, because those are different facts. A summary column with an unmeasured job prints a floor and the count of jobs that were measured, rather than a total that reads as complete
- feat: the driver row is scoped to the run's interval, derived from the receipt's stamp and its `duration`. A session that hosted two runs yields two different rows, and a transcript that keeps growing does not change an already-rendered receipt. Input with no derivable end is labelled unscoped instead of silently totalling the whole session
- fix: a job with no `exit_code` is FAILED, never RUNNING — a worker that died without writing one leaves no exit code behind. `handoff_runtime.job_state()` is now the one implementation, shared with `render-transcript.py` along with the payload-injection helpers
- feat: the receipt file is parsed fail-closed — one block, no duplicate keys, schema version 5, job ids confined to the jobs directory, and per-backend counts agreeing with both the entries and each job's own recorded backend. The HTML export is enumerated, so a denial contributes a count and never its command string

## v3.6.0 (2026-09-08)

### Read a delegated job instead of grepping its log

- feat: `/agent-handoff transcript [<pid|jobId|folder>]` renders a job's `log.jsonl` as one self-contained HTML page and opens it. Until now the only way to see what a delegated worker actually did was to read raw JSONL — the monitoring loop tails it, `result` summarizes it, and neither shows the conversation. The selector is optional; omitted or `last` means the newest job, and a folder path, an exact jobId, a pid, or a remembered label all resolve. When more than one job matches — a label and its `-r2` resume round is the common case — the script prints the candidates and exits rather than guessing, because silently opening the wrong round is worse than a second command
- feat: `assets/transcript-viewer.html` is the whole viewer: vendored marked v15.0.12, the normalizer, the escaping boundary, and the renderer in one file with no network access. It reads both log formats the skill produces — Codex `item.started`/`item.completed` envelopes and Claude `stream-json` assistant/user events — folding each started/completed pair and each `tool_use`/`tool_result` pair into one row at the first event's position. A failed edit renders as an error rather than a completed file change, and an unrecognized event keeps a row carrying its source object instead of vanishing
- feat: dropping a `log.jsonl` onto the same page renders it, so a log from any repo is readable without running the script. All parsing lives in the page's JavaScript for exactly that reason: one parser, not a second one in Python that drifts
- feat: `scripts/render-transcript.py` stays thin — resolve, read, inject, write to `<repo>/.handoff/transcripts/<jobId>.html`, open. A malformed interior line shows up as an `unparsed` row; a truncated final line from a live job is reported as a partial log rather than as a broken row
- fix: log content is untrusted input. Every string reaches the DOM through `textContent` except agent and reasoning text, which goes through marked — and marked passes raw HTML through by design, so three renderer overrides escape emitted HTML and reject any link or image scheme outside `https`, `http`, `mailto`, and `#`. `tests/test_transcript_viewer.mjs` asserts the emitted markup against a tag and attribute allowlist rather than scanning for `onerror`, which an escaped payload would trip falsely
- feat: the JS tests extract the shipped page's own script blocks and run them in node, so there is no second copy to drift. They run in CI beside the Python suite

## v3.5.1 (2026-09-08)

### A delegated Claude worker can run its own checks

- fix: a claude-backed job runs with `--permission-mode bypassPermissions`. v3.5.0 shipped `acceptEdits` alongside `--permission-prompts none`, and that pair denies nearly everything: `--permission-prompts none` means anything that would prompt is refused automatically, and `acceptEdits` pre-approves file edits and nothing else. So every Bash call outside the user's `settings.json` allowlist reached a prompt with nobody there to answer it, and so did every MCP tool and every Skill. The first real run of the path took 11 denials, among them `curl`, the repo's own `check-skill-repo.sh`, and a node test. It wrote all its files correctly, exited 0, and reported that all gates had passed
- fix: the two backends were never held by the same kind of thing, which is what the v3.5.0 parity claim missed. `-s read-only` is a sandbox: it grants a codex worker the right to run commands and bounds what they may write. A permission mode grants nothing on its own, and a background `--print` job has no approval surface, so any mode that prompts denies instead. What holds a delegated claude worker is its worktree and the scope constraints in its packet
- feat: `status` and `result` report `permission_denied`, counted from the worker's own event stream, and `result --json` carries the count with the tools involved. A denied tool call leaves the exit code alone, so a blocked acceptance check used to be indistinguishable from a passed one. The flow prose now treats any non-zero count as a failed job whatever the exit code says, and re-runs the blocked commands in the driving session
- feat: `submit` warns on stderr when a claude worker starts with its checks bypassed, and `meta` records `permission_mode` for the audit trail; stdout stays exactly the jobId. `HANDOFF_CLAUDE_PERMISSION_MODE` takes any mode the claude CLI accepts and restores prompting for anyone who would rather trade the worker's self-verification for it. `--read-only` still pins `plan`
- fix: `HANDOFF_CLAUDE_PERMISSION_MODE` is validated before it reaches the generated `run.sh`. It was read unquoted and spliced into the exec line, so a value carrying a shell metacharacter would have run — every other input in that script is `%q`-quoted or `case`-checked
- test: the claude parity suite asserted argv, job state and the worktree lifecycle, and never asserted a permission flag, so a green suite said nothing about whether the worker could work. It now covers the default mode, the override, the read-only pin, a refused junk override, the mode in `meta` and on a fix round, and denial reporting on both backends

Worth naming: the denied `curl` meant the transcript viewer's hash check on its vendored `marked` copy never ran. The bytes were correct, so the gate was skipped rather than failed — which is what a silent denial produces, and why the count now travels with the job.

## v3.5.0 (2026-09-08)

### Breaking: a claude-backed identity is a real delegated job

- feat: `delegate-codex.sh` dispatches on the identity's configured `backend` instead of fail-closing on `claude`. A claude-backed row now becomes a background job with the same jobId, `.handoff/jobs/` state, monitoring loop, fix-round `resume`, worktree lifecycle, and receipt evidence a codex-backed row already had. The name stays: one script, one job shape, one code path — which is what makes parity real rather than asserted. The script wraps `claude --print --output-format stream-json --verbose --permission-prompts none --permission-mode <acceptEdits|plan> --effort <e>`, and `result` parses the Claude event stream alongside the Codex one
- fix: the real defect was a flow that re-decided the vendor per run. In a live run the driver hit a claude-backed `fast_worker`, was refused, and re-routed the work to `deep_reasoner` purely because that identity was Codex-backed — the split decision's capability judgment thrown away to buy a cheaper meter, with the swap hidden in a jobId. The mechanism was only half of it: `references/fable5-principles.md` had ranked the Claude subagent last and framed delegation as a cost move, which is the reasoning the driver quoted back
- feat: a second reason to delegate is now stated everywhere the first one was. Handoff moves execution off the driver's meter **and** off its context window; a claude-backed job earns the second even where it moves no meter. The three-channel table becomes one delegation channel that runs on either backend per config, plus two in-process escape hatches, and the rule is written plainly: never swap an identity to move a job onto another vendor — change the config and say so
- feat: `submit --backend codex|claude` for role-less ad-hoc jobs. One that contradicts a named role is refused; that guard replaces the old backend-is-claude fail-close. Efforts are validated per CLI rather than against one shared enum: codex keeps `minimal`-`ultra`, claude takes `low`-`max`
- fix: `resume` reads the parent job's `backend` from its `meta` and lands on the same CLI, and carries the parent's `role` forward. A fix round used to lose its identity provenance, so a run whose work happened mostly in rework had thinner `roles_used` than a clean first pass
- fix: a generated claude `run.sh` strips `ANTHROPIC_*` and `CLAUDE_CODE_*` with Bash prefix expansion before exec, mirroring `handoff_runtime.clean_claude_env()`, so a delegated worker authenticates like a fresh terminal instead of inheriting the host's injected credentials. A first prototype used a `sed` alternation and failed silently on macOS — BSD sed has no `\|` in a basic regular expression
- feat: `tests/test_delegate_role.py` runs the whole worktree lifecycle against both backends from one base class: create, record `worktree`/`branch`/`base_commit`, immutable SHA pinning, invalid base refused before the job dir exists, worktree as cwd, idempotent `cleanup`, dirty-tree refusal, `resume` landing in the parent worktree. Monitoring parity (`status`/`result` returning the same shape) is asserted too, so the Phase 3 `/loop` claim is tested rather than assumed
- **breaking**: receipt schema v5 adds `cc_jobs` and `cc_job_durations`; `codex_jobs` and `codex_job_durations` narrow to codex-backed jobs. `make-receipt.py` partitions the job directories by each `meta`'s `backend=` line (absent means codex, for job dirs written before this change), and `phase` gains `delegated implementation` — `codex implementation` is a false phase name for a run whose work ran on Claude. An existing v4 receipt fails `validate-receipt.py`; that is the intended signal to regenerate it

`schema_version` stays `2`: no configuration change was needed, because `backend = "claude"` was always a valid value — it just had nowhere to go.

## v3.4.0 (2026-09-07)

### A second pair of eyes on the plan

- feat: `deep_reasoner` gains one responsibility toggle, `auto_review_spec`, written by `handoff-setup.py --spec-review` (default off). With it on, the last step of Phase 1 hands the plan to `deep_reasoner` on its own configured backend for one independent read, and the driver folds in the findings before the plan reaches the user. Until now the adversarial gate was the only check on a plan, and the driver ran it against itself — the agent that wrote the plan was the only agent that judged it
- feat: the review fires at most once per run. The goal file gains a `## Spec Review` block, and a non-empty block means the automatic review is spent: an adjusted plan, a thin review, and a resumed session all fail to re-trigger it, and another one takes an explicit request. The goal file is rewritten per run, so the marker resets by itself
- feat: `handoff-config.py set --spec-review` / `--no-spec-review`, `--override deep_reasoner.auto_review_spec=true`, and the field in `--status`. It is a responsibility switch rather than a routing value, so toggling it leaves `verified` and `verified_at` alone, and it is refused on any identity but `deep_reasoner` rather than silently ignored
- feat: the setup UI carries the toggle as one checkbox, seeded from the resolved config so re-running setup cannot silently clear it
- docs: `references/handoff-template.md` gains the Spec Review Packet — read-only, findings only, the spec inline. It is the one packet that narrows the template's write permission instead of widening it, which is the opposite direction from the e2e pair. The flow prose states plainly that this review is informed rather than blind: the plan under review is the driver's own answer, so the arbiter's contamination rule does not apply, and same-vendor config is noted the way the arbiter protocol notes it

`schema_version` stays `2` and the receipt schema stays `4`. Absent means off, so every existing config keeps its current behaviour, and an older engine ignores the field rather than refusing the file — `validate_config` fails closed on an unknown identity, not on an unknown field inside a known one.

## v3.3.0 (2026-09-07)

### The Codex effort dial reaches the top of the GPT-5.6 range

- fix: `max` and `ultra` are configurable Codex efforts. `codex model/list` reports both on the GPT-5.6 models, but the setup engine intersected that list against a five-value tuple, so the wizard silently dropped the top two levels and `delegate-codex.sh` refused a config that carried one — `ERROR: invalid --effort: max` on an identity the wizard itself could not have offered. Both values are verified against the live CLI rather than assumed: `model_reasoning_effort` accepts them, and an unknown level still fails closed with the API naming what it supports
- docs: `references/fable5-principles.md` says what the two new levels are for, and that `ultra` subdivides the job through automatic task delegation rather than only thinking longer

No change was needed for the newly released models themselves. Model names have never been hardcoded. Claude Code resolves `opus` and `fable` as rolling aliases; verified locally, `opus` and `claude-opus-5` smoke-pass and `fable` resolves to Fable 5.1. Codex models come from the account-aware `model/list`, so a model this account cannot list yet still routes end to end once it does.

## v3.2.0 (2026-08-24)

### Optional e2e acceptance roles

- feat: two optional identities, `e2e_specifier` and `e2e_verifier`, behind `handoff-setup.py --with-e2e` (default off). The specifier writes Gherkin scenarios with stable IDs plus repo-native executable tests; the verifier runs them and reports a verdict. A three-identity config stays complete: the smoke gate and the "identities are not configured" error compute over the core three, and `--status` lists all five with `<unset>` for the unconfigured ones
- feat: `delegate-codex.sh submit --worktree <branch> [--base <commit-ish>]` runs a job in its own Git worktree under `.handoff/worktrees/<jobId>`, pinned to a base resolved to an immutable SHA before the job directory exists. `resume` inherits the parent's worktree, and the new idempotent `cleanup <jobId>` removes it, refusing one that holds uncommitted changes
- feat: `scripts/validate-verdict.py` and `docs/verdict-schema.json`. A verdict is the input to a merge decision, so PASS is checked against the exit code, the scenario counts, an empty findings list, and the hash of the scenarios the driver reviewed, rather than trusted as written
- feat: the goal file's task table gains a `depends` column, so the `/loop` monitor can hold a verifier row until both the specifier and the implementation are done
- feat: `roles_used` accepts the two e2e roles. `receipt_schema_version` stays `4` and `$id` stays `handoff.receipt.v4` — widening the enum is additive, so every existing v4 receipt still validates
- docs: `references/e2e-gauntlet.md` carries the worktree protocol, both delegation packets, the frozen/repairable/forbidden split, the verdict contract, and the review-the-tests-first gate. It states two limits rather than implying otherwise: the Codex and Claude worktree paths are not equally hardened, and the hash lock covers `.feature` files only

## v3.1.0 (2026-08-23)

### Breaking: receipts record how long the run took

- feat: `make-receipt.py --start --repo <repo>` stamps `.handoff/session-start` at Phase 0 preflight, and the receipt measures wall clock against it, so a run that sat waiting on a permission prompt carries that wait in `duration`. With no marker and no explicit `--started-at`, the tool refuses to emit rather than accept a remembered start time
- feat: `codex_job_durations` breaks the run down per delegated job, read from each job's `meta` `submitted_at` and the mtime of its `exit_code` under `.handoff/jobs/`. Jobs left over from an earlier run are excluded, one still running reads `running`, and `delegate-codex.sh` is unchanged
- refactor!: receipt schema v3 → v4. `duration` and `codex_job_durations` are required and `$id` is now `handoff.receipt.v4`; a v3 receipt fails `validate-receipt.py`, which is the signal to regenerate it with the current `make-receipt.py`
- docs: the two timing lines in `examples/session-receipt.md` are backfilled placeholders: that session predates the marker, and the next real run replaces them

## v3.0.0 (2026-08-22)

### Breaking: renamed to agent-handoff

The skill was called `partner-skill` ("Partner") through v2.x. Every name below changed at once, and there is no compatibility shim — a machine with the old install re-runs setup.

- refactor!: skill name `partner-skill` → `agent-handoff`; install destination `~/.claude/skills/agent-handoff`. Triggers are now the skill name or the word handoff: `/agent-handoff` (bare), `/agent-handoff config` (also `setup`/`init`), `/agent-handoff tryout`, `/agent-handoff resume`, plus "config agent handoff", "setup agent handoff", "tryout agent handoff", "hand this off to codex". The verb is `config`, never `configure` — `install.sh --configure`/`--configure-cli` became `--config`/`--config-cli`
- refactor!: per-repo state directory `.partner/` → `.handoff/` (config, goal, jobs, receipts, backups, generated manifest), and global config `~/.config/partner/config.toml` → `~/.config/handoff/config.toml`. Nothing reads the old locations
- refactor!: `scripts/partner-{config,setup,setup-ui}.py` and `scripts/partner_runtime.py` → `scripts/handoff-{config,setup,setup-ui}.py` and `scripts/handoff_runtime.py`; tests renamed to match
- refactor!: `$PARTNER_DIR` → `$HANDOFF_DIR`, `PARTNER_CODEX_BIN` → `HANDOFF_CODEX_BIN`, `PARTNER_AGENT_CMD` → `HANDOFF_AGENT_CMD`; generated subagents `partner-deep-reasoner`/`-fast-worker`/`-arbiter` → `handoff-*`; managed routing markers now say `HANDOFF MANAGED ROUTING`
- refactor!: receipt header `[Partner session receipt]` → `[Handoff session receipt]` and schema `$id` `partner.receipt.v3` → `handoff.receipt.v3`. Field set is unchanged, so `receipt_schema_version` stays `3`; a receipt written under the old header no longer validates
- refactor!: showcase ledger schema `handoff.showcase_cost_ledger.v1` with mode key `handoff` and comparisons `handoff_vs_pure_claude` / `handoff_vs_codex_only`
- docs: release notes under `docs/releases/`, the three archived receipts under `examples/`, and older changelog entries keep the Partner name — that was the name when they were written

### Breaking: one flow, one host

- refactor!: remove the Codex-driven flow (Direction A). Partner is now a single flow — Claude Code plans and splits, Codex executes delegated background jobs, Claude full-reviews before accepting. `references/codex-driven.md`, `monitoring.md`, `scenarios.md`, `failure-playbook.md`, and `bounded-planning.md` are gone, along with `make-handoff.sh`, `check-claude-cli.sh`, `session-snapshot.sh`, and `run-claude-plan.py`
- refactor!: collapse the dual-host configuration layer. `--host` is gone from `partner-config.py`, `partner-setup.py`, `partner-setup-ui.py`, and `delegate-codex.sh`; `install.sh` installs only to `~/.claude/skills/partner-skill` and no longer writes a `host=` marker. Existing `.partner/config.toml` files keep working — the `[hosts.claude_code.identities.*]` shape is unchanged, and a stale `hosts.codex.*` block round-trips byte-for-byte
- refactor!: Partner Session Receipt schema v3 drops `direction`, `monitoring_level`, `claude_session_reused`, `new_claude_p_sessions`, `codex_passes`, and `host`. `roles_used[].host` is unchanged — it records the CLI that executed a role. `examples/session-receipt.md` now carries a real v3 receipt and is CI-validated again; the two v2.0.x conversation-cost receipts stay as schema v2 archives and are not
- refactor!: remove the bundled `idea-king` companion skill. Its adversarial gate survives inline as Phase 1 of `references/claude-driven.md`: the same three questions, answered in writing before anything is delegated
- fix: `read_legacy_v1` now reads v1 role values from any host namespace, so a schema-v1 config written by a Codex-side install still seeds the setup wizard
- test: 32 behavior prompts down to 17; `sandbox-matrix.sh` and `test_run_claude_plan.py` removed

## v2.0.1 (2026-07-29)

### Bounded Claude planning

- feat: add `scripts/run-claude-plan.py`, a config-only Claude planner with a validated 24,000-character evidence packet delivered over stdin, safe mode, zero tools/subagents, wall and no-event timeouts, and a Claude-CLI-enforced API budget
- feat: persist bounded sanitized event envelopes, visible-text checkpoints, exact configured/observed model/session, runner and packet hashes, cost metadata, atomically created plans, and same-session recovery instructions under `.partner/`
- fix: distinguish authentication, upstream idle, local idle timeout, wall timeout, budget stop, tool-use violation, and protocol error instead of treating every Fable failure as login trouble
- fix: invalidate verification when an identity changes; refuse unverified/non-Claude roles, malformed or secret-like packets, silent truncation, incomplete/conversational plan output, nonzero nominal success, observed model/session mismatches, output overwrite races, and all automatic model fallback
- fix: bound partial-line, visible-output, event-log, process-group termination, and expanded secret-redaction paths so watchdogs cannot be defeated by byte trickles, inherited pipes, or common bearer/password/GitLab/AWS/Google credentials
- refactor: share the nested Claude environment cleanup between setup smoke and the bounded planner
- test: add mocked stream/budget/timeout/config boundary coverage, repository behavior prompts, and the full unit suite to GitHub Actions
- docs: define who builds the bounded packet, its exact contract, the honest budget telemetry boundary, and the fixed recovery path

## v2.0.0 (2026-07-29)

### Setup, identity routing, and safety

- fix: strip host-injected Claude Code environment markers before spawning the real Claude CLI, preserving first-party OAuth instead of triggering a nested-session/login failure
- fix: restore delegate-codex.sh auto --skip-git-repo-check for non-git --repo targets, lost in the v1.5 rewrite (jobs against non-git dirs FAILed immediately)
- fix: align the model matrix rows and synchronize the matrix/settings header grid without removing the routing motion
- feat: rebuild the setup UI as a kinetic local Agent routing console with live role mapping, purposeful motion, responsive layout, and reduced-motion support
- feat: localhost single-page setup UI with a taste-skill guided decision rail, concrete model matrix, exact diff preview, preview-bound confirmation, and smoke test without repeated chat questions
- config: balanced preset fast_worker now uses the detected Codex model with high reasoning effort
- feat: identity matrix — three cross-vendor identities (deep_reasoner / fast_worker / arbiter), each with its own backend/model/effort; schema v2 with fail-closed v1 migration (`5a1f3d7`, `52ad950`, `da269ce`)
- feat: arbiter blind-solve protocol + "Partner, tryout" first-run tryout; goal.md task table drops owner in favor of identity (`f329daf`)
- feat: idea-king adds an Assignment section to Partner work-split reviews (`18dd247`)
- fix: wire per-task role decision into the split flow; clarify owner vs role (`983457f`, `493d561`, superseded by the identity matrix)

### Protocol, receipts, and verification

- test: make the dual-host sandbox matrix self-contained with deterministic fake CLI binaries so CI does not depend on installed Claude or Codex tools
- test: dual-host CI sandbox matrix — install order, idempotence, fail-closed (`6333e8c`)
- fix: redirect codex exec stdin from /dev/null to prevent hung background jobs (`0d673cc`)
- docs: README bilingual rewrite — setup wizard, host self-ID, receipt v2, opt-in full protocol (`54ce055`)
- feat: goal-sync.py hash-checked goal.md read/write, no silent lost update (`94349df`)
- test: test-prompts.json +4 goal-to-pr case incl. ordinary-pr-no-trigger negative (`d99b630`)
- feat: Plan→Goal→PR→Verification protocol, references/goal-to-pr.md (`f5b9232`)
- feat: extend Partner Session Receipt with host/scope/config_source/roles_used, schema v2 (`5246d45`)
- feat: activate Sub Agent three-level routing, partner-* > user agent > generic Task (`3d5e077`)
- test: test-prompts.json +9 case — 4 setup, 4 host-adapter, 1 idea-king (`c6020f5`)
- feat: partner-setup.py wizard engine + references/setup.md, "Partner, configure" first-run setup (`5e3744b`)
- feat: install.sh --configure forwards to the terminal setup wizard (`9e95036`)
- feat: idea-king absorbs Occam/Murphy/Coase laws, ported from installed copy (`51a6389`)
- feat: idea-king clarify-to-95% pre-verdict protocol, headless degrades to Open Questions (`d86ae57`)
- refactor: split SKILL.md into Partner Core + references/codex-driven.md with host detection (`4cc5ccd`)
- feat: partner-config.py TOML-subset config engine (schema v1) with tests and docs (`760d938`)
- feat: delegate-codex.sh --role injection from partner-config, with --dry-run and tests (`63b6807`)
- feat: install.sh writes host= marker into .install-meta (`d9eab44`)
- test: run-test-prompts.py supports should_trigger:false negative cases (`b21cbe3`)
- fix: remove deprecated --enable web_search_cached from delegate-codex.sh (`30999a2`)

## v1.4.2 (2026-07-05)

- feat: idea-king absorbs official adversarial-review, grilling, and packet hygiene (`a1ddbc9`)
- docs: add idea-king adversarial-review showcase GIF to both READMEs (`4ab807c`)
- docs: add Red Skill submission copy for Partner (`9ebc3e7`)

## v1.4.1 (2026-07-04)

- feat: execution-channel routing + evidence-backed prompting principles (`ab679e8`)
- fix: harden bidirectional delegation utilities (`f5539bb`)

## v1.4.0 (2026-07-03)

- feat: bidirectional Partner + idea-king + frontier prompting & memory protocol (`a9b5dde`)

## v1.3.0 (2026-07-02)

- Release partner skill v1.3.0 (`e7266e8`)
- feat: showcase redesign with green palette, GSAP animation, and GIF (`e67966c`)
- fix: replace curly quotes with straight ASCII quotes in img tags (`764f4bb`)

## Earlier (2026-06-24 – 2026-07-01)

- Showcase and release polish: reproducible cost ledger, README parity release gate, README language split (`ad19e6c`, `efed0d8`, `8ebb5cf`, `3418b60`, `a266e94`, `b984911`, `c35955b`, `2eac3c1`)
- Public readiness: Partner session receipt, same-session Claude strategy, release preparation (`258779f`, `f87a17e`, `0ae1d81`, `7fa24da`)
- Origin: initial Claude Codex relay skill, renamed to Partner (`1ba62e6`, `5979fab`)
