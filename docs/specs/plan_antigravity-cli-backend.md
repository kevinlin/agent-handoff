# Antigravity CLI as the Fifth Backend Implementation Plan

> In this repository the plan runs under `/agent-handoff` as the rows in *Execution order*. Workers do not commit: each row's commit is the driver's, after its review.

**Goal:** an antigravity-backed identity runs `agy -p --output-format stream-json` as the same durable Handoff job that codex, claude, copilot, and cursor get, with receipt schema v8 and version 3.10.0.

**Architecture:** approach A of the design. Every place that branches on backend gains one explicit antigravity branch, and every table keyed by backend gains a row. There is no shared parsing module; that is a follow-up.

**Tech stack:** Bash (`scripts/delegate-codex.sh`), Python 3 stdlib (`unittest`, no third-party packages), vanilla JavaScript in `assets/*.html`, `node --test` for the viewer.

**Spec:** `requirements_antigravity-cli-backend.md` (R1.1 to R9.3) and `design_antigravity-cli-backend.md` (decisions 1 to 14). Both have since been folded into [design_cli-backends.md](design_cli-backends.md); the originals this plan cites are at `git show bcd54de:docs/specs/<file>`. Facts: [../research/antigravity-cli-specification.md](../research/antigravity-cli-specification.md), with raw evidence under `.handoff/probes/agy/<probe-id>/` on the maintainer's machine. A worktree holds tracked files only and has no `.handoff/`, so delegation packets give that directory, and R2's inventory beside it (`R2-inventory.md`), as absolute paths in the main checkout. Every worker reads the requirements and the design before starting. The design's fall-through table (decision 8) and component table list every place to change, with `file:line` at `1486e87`.

This plan does not repeat the specs. It fixes what they leave open: the order of work, the file boundaries between rows, the names of new functions and tests, the fixtures, and the commands that prove each row.

## Global constraints

- Version `3.10.0` everywhere it appears. `receipt_schema_version` `8`. Config `schema_version` stays `2`.
- Backend value `antigravity`. Binary `agy`, from `HANDOFF_ANTIGRAVITY_BIN` first, then PATH. Never `antigravity`, which is the IDE launcher's name.
- The identity check: a candidate passes when `<candidate> --help`, stdout and stderr together, contains both `--conversation` and `--dangerously-skip-permissions`, within 10 seconds. Bash runs it through `python3` with `subprocess.run(..., timeout=10)`, because macOS ships no `timeout`. Python calls the same way.
- The antigravity effort enum is exactly `("model",)`. An empty model and `model = "auto"` are refused on antigravity at setup and at submit, and each refusal names `handoff-config.py set`.
- Postures: `default` and `allow-all` both pass `--dangerously-skip-permissions`; read-only passes `--mode plan` and never the skip flag. Effective `permission_mode` in `meta`: `skip-permissions` or `plan`.
- Every antigravity command line carries `--print-timeout 0s` and never carries a flag from R2.4's list.
- Handoff writes nothing under `~/.gemini/`. There is no environment scrubbing for the antigravity branch.
- No antigravity preset and no `CLI_RELEASES` entry. The USD figure for an antigravity job is `None`.
- Codex, Claude, Copilot, and Cursor parsing does not change. No existing test is deleted or weakened; a test that pins four backends or v7 is updated to five and v8.
- The skill surface (`SKILL.md`, `references/`, `scripts/`, `assets/`) never links into `docs/specs/`.
- Keep every existing comment and docstring, and every clause of prose that is still true. Add a short "why" comment to each new function and to each antigravity branch whose reason is not obvious. Name the probe id in the comment when the reason is a probe (for example, `# RSAME: result.usage is cumulative over the conversation`).
- In `delegate-codex.sh`'s `STATUS_SCAN_PY` heredoc, comments must not contain an apostrophe: one broke `bash -n` under bash 3.2 in v3.9.0.
- Never run `git stash`. Workers do not commit.
- No new risky command text. `check-skill-repo.sh` counts one warning for a whole group, so `warn=1` staying `warn=1` proves nothing. Instead, after staging the row's work, this must print nothing:
  ```bash
  PATTERN='git[[:space:]]+reset[[:space:]]+--hard|[Hh]ard [Gg]it [Rr]eset|rm -rf|force push|--force'  # risk-ok: the scan's own pattern
  git add -A && git diff --cached <base-sha> -- . ':!test-prompts.json' | grep -E '^\+' \
    | grep -E "$PATTERN" | grep -vE 'risk-ok|[Dd]o not|not `' || true
  ```
- Prose follows the repo style: plain words, no decorative emoji, no invented numbers.

## Review focus

These are inputs the specs imply but their acceptance checks do not exercise. Each has a named test in the task listed.

1. **A role-less `submit --backend antigravity` with no `--effort`.** Expected: effort `model`, as on cursor. With no `--model` as well, it is refused. Task 1, `test_a_role_less_antigravity_job_gets_the_only_effort`.
2. **A pre-session error log** (one `result` line, empty `conversation_id`, no `init`). Expected: `status` shows `last_event: result`, no session id, usage known and zero, `errors` holds one `result_error`. Task 1, `test_a_launch_rejection_has_no_session_and_one_error`.
3. **Unexpected event shapes:** `step_update` that is `null` or a string, `usage` that is not an object, a `result` with no `denied_actions` key, `denied_actions` that is not a list. Expected: nothing crashes, and nothing is counted. Tasks 1 and 3, `test_an_unexpected_antigravity_shape_is_skipped_not_fatal`.
4. **A resume of a resume.** Expected: the second round's `--model` is the model recorded in the first round's `meta`, never `inherit`, and its `resumed_session` is the first round's own `init` id. Task 1, `test_a_resume_of_a_resume_passes_the_recorded_model`.
5. **`HANDOFF_ANTIGRAVITY_BIN` set in the developer's shell.** The setup suites copy `os.environ`. Expected: they never reach a real `agy`. Task 2 pops the variable in each `setUp`; Task 1's `make_env` overwrites it.
6. **A help text on stderr only.** The real CLI prints its help to stderr (HELP). Expected: the identity check passes it. Task 1's fake prints its help to stderr for that reason.

## Execution order

Rows in a wave touch disjoint files, so they run in parallel as `--worktree` jobs cut from the same base commit. The driver reviews each row's diff, commits it on its worktree branch, and merges it into `feat/antigravity-backend` before the next wave.

| Wave | Row | Tasks | Identity | Why this order |
|---|---|---|---|---|
| 1 | A | 1 | fast_worker | The delegation primitive; Task 2's smoke runs its `submit --dry-run` |
| 1 | B | 3 | fast_worker | Independent files: receipt, cost receipt, session view |
| 1 | C | 4 | fast_worker | Independent files: transcript viewer |
| 2 | D | 2 | fast_worker | Needs `BACKENDS` and `submit --dry-run` from row A |
| 3 | E | 5, 6 | fast_worker | Prose describes what shipped, so it waits for the code rows |
| 4 | driver | 7 | - | Live end-to-end run on the user's Antigravity account |

Row files:
- **A:** `scripts/delegate-codex.sh`, `scripts/handoff-config.py`, `tests/test_delegate_role.py`, `tests/test_handoff_config.py`.
- **B:** `docs/receipt-schema.json`, `scripts/make-receipt.py`, `scripts/validate-receipt.py`, `scripts/check-skill-repo.sh`, `scripts/render-cost-receipt.py`, `assets/cost-receipt.html`, `scripts/handoff-session-ui.py`, `examples/session-receipt.md`, `tests/test_receipt.py`, `tests/test_cost_receipt.py`, `tests/test_session_ui.py`, `tests/fixtures/session-view/`.
- **C:** `assets/transcript-viewer.html`, `tests/test_transcript_viewer.mjs`, `tests/fixtures/transcript/`.
- **D:** `scripts/handoff-setup.py`, `scripts/handoff-setup-ui.py`, `assets/setup-ui.html`, `tests/test_handoff_setup.py`, `tests/test_handoff_setup_ui.py`.
- **E:** everything in the prose inventory (R2's section 7, at `.handoff/probes/agy/R2-inventory.md`), the four design-of-record docs, `CHANGELOG.md`, `docs/specs/index.md`.

Row B moves the receipt to v8 while row A still writes no receipt; nothing in row A reads a receipt. Row E is the only row that edits `test-prompts.json` and `evals/`.

---

### Task 1: The delegation primitive (row A)

Requirements: R1.1 (engine), R1.2 to R1.4 (submit), R2.1 to R2.7, R3.1 to R3.4, R4.1 to R4.4, R5.1 to R5.3, R9.2 (delegate tests and shared helpers). Design: decisions 1 to 8, 13, 14.

**Files:** `scripts/handoff-config.py` (`BACKENDS`, the "three CLIs" comment at `:77`); `scripts/delegate-codex.sh`; `tests/test_delegate_role.py`; `tests/test_handoff_config.py`.

**Changes in `delegate-codex.sh`:**
1. Header comment and `usage()`: five backends, `--backend ...|antigravity`, `HANDOFF_ANTIGRAVITY_BIN`, the antigravity effort and posture lines, in the style of the cursor lines.
2. The submit guard (`:462`) accepts `antigravity`.
3. `resolve_worker_bin`: an `antigravity)` case with `env_var="HANDOFF_ANTIGRAVITY_BIN"` and `bin_name="agy"`. New `is_antigravity_bin` (the `--help` check from the global constraints) and `discover_antigravity_bin` (walks `type -a -p agy`, takes the first match that passes, and otherwise prints which path failed and how to fix it). Mirror `is_github_copilot_bin` and `discover_copilot_bin` (`:244-275`). The override check sits beside copilot's at `:212`.
4. `validate_effort`: `antigravity) ... model` beside cursor's (`:290`).
5. The role-less effort default at `:506` gives `model` on antigravity too. The `auto` refusal at `:511-513` covers antigravity. An empty model on antigravity is refused with the same `handoff-config.py set` fix, for a role-less job too: unlike cursor, a role-less antigravity job cannot run on the CLI's default model, because its resume must pass the model again (decision 4).
6. New `resolve_antigravity_permission_mode`: `plan` when read-only, else `skip-permissions`.
7. `warn_permission_bypass`: `antigravity:skip-permissions` joins the whitelist. Its remedy line: "Antigravity has no narrower posture Handoff can set; use --read-only for a job that must not write."
8. New `write_antigravity_exec_line`, called from `write_run_script` beside cursor's (`:716`). It writes the fresh and resume lines from design decision 6. The prompt, model, and session id are quoted exactly as `write_cursor_exec_line` quotes them.
9. `cmd_resume` on antigravity:
   - re-checks the parent's binary with `is_antigravity_bin` (beside copilot's at `:665`);
   - writes `model=<parent's model>` in `meta`, not `model=inherit`, and adds `resumed_session=<id>`;
   - does **not** write `$JOB/session_id` before launch (`:678`);
   - passes the parent's model and the round's posture flag to `write_antigravity_exec_line`.

   The parent's model comes from `meta_value model` on the parent. That is never `inherit` for an antigravity parent, because this step writes the real one.
10. `extract_session_id`, for every backend, treats a cache file holding only whitespace as absent and scans the log again. Today an early read with no id yet prints an empty line, `tee` caches the newline, and the `-s` test at `:405` then returns that empty value for good. On antigravity, the id is taken only from the top-level `conversation_id` of an `event: "init"` line, not from the generic key loop at `:425`. Pass the backend in, as `cmd_status` does, and keep a comment saying why: a pre-session `result` carries an empty nested id, and a resumed round must report its own conversation (decision 4).
11. `STATUS_SCAN_PY`, antigravity branch:
    - the discriminator is `event`;
    - `last` is the `event` value, or `step_update:<step_type>` for a step;
    - denials are `len(result.denied_actions)` from the last `result` event, with type checks.

    The scan prints a third line: `unknown` when the backend is antigravity and no `result` event was seen, else `known`.
12. `cmd_status`:
    - antigravity in a terminal state with `unknown` prints `permission_denied: unknown (no result event; agy reports denials only when a run ends)`;
    - an antigravity resume whose log `init` id differs from `meta` `resumed_session` prints `context_lost: true (round ran without its parent's context; resubmit with the full packet)`.

    The numeric test at `:922` must not see `unknown`. Parse the third line first. The scan runs only when `log.jsonl` has bytes (`:910`), so initialize the antigravity denial state to `unknown` outside that guard. A terminal job with an empty or missing log then reports unknown too. Report `context_lost: true` only when the log's `init` names a non-empty id that differs from `resumed_session`. With no observed id, as on a pre-session rejection of a resume round, the loss is unmeasured and nothing is printed.
13. `cmd_result`, antigravity branch, per R5.2 and decision 14:
    - agent message: the last `agent_response` step, deltas joined by `step_index`;
    - commands: `ACTIVE` `run_command` tool steps' `CommandLine`;
    - usage: the step `usage` sum, keys `input_tokens`, `output_tokens`, `thinking_tokens`, `cache_read_tokens`, `total_tokens`. It is known only when a `result` event exists, and `{}` otherwise;
    - `denied` from `denied_actions[].display_name`, and the count `None` with no `result`;
    - `errors`: `result_error` from a `result` with status `ERROR`, and `print_timeout` from a `stderr.log` line containing `print timeout after`. `cmd_result` passes `$JOB/stderr.log` to the Python block as a new argument;
    - `context_lost`: `True` or `False` on an antigravity resume whose log has a non-empty `init` id, compared with `meta` `resumed_session`; `None` otherwise, including a resume round rejected before `init`. Pass both values through new arguments.

    `--json` adds `"context_lost"` for every backend, and `permission_denied` may be `null`. Text output prints `permission_denied: unknown (…)` when the count is `None`, and a `context_lost` warning line when it is `True`.

**Tests in `tests/test_delegate_role.py`:**
- `make_env`:
  - `HANDOFF_ANTIGRAVITY_BIN` points at a fake `agy`. Its `--help` prints a help text containing both flags **on stderr**, and any other invocation behaves like the codex fake;
  - a decoy `ide/agy` prints IDE-launcher-style help without those flags and exits 0;
  - a fake `agy` later on PATH.
- `BackendLifecycle` is a mixin; reuse what it already has. `EFFORT` already exists (`:413`) and is already used by `submit_raw`, `configure_posture`, and `test_multiline_model_and_invalid_resume_effort_are_refused`. The changes:
  - a class attribute `MODEL = None`. When set, `submit_raw` passes `--model`. The antigravity subclass sets a catalogue id, because its empty-model guard would refuse the shared submissions;
  - an explicit antigravity branch in `assert_posture_argv`, before the final `self.fail`:
    ```python
    elif self.BACKEND == "antigravity":
        # RO2: plan mode with the skip flag wrote files, so read-only never carries it.
        self.assertEqual(read_only, "--mode plan" in run_sh)
        self.assertEqual(not read_only, "--dangerously-skip-permissions" in run_sh)
        self.assertIn("--print-timeout 0s", run_sh)
        self.assertEqual("plan" if read_only else "skip-permissions", meta["permission_mode"])
    ```
  - the version-only shim in `test_model_and_resume_id_are_literal_arguments` (`:495`) also answers `--help` with both flags, on stderr, when the backend is antigravity;
  - the shared denial test (`:870-878`) builds its log through a new overridable method, `denied_log_lines()`. Its default returns exactly today's stream, `DENIED_LINE`, `LOG_LINES`, `DENIED_LINE`, because Copilot depends on that order: its first denial precedes the tool start and is named `unknown` (`:987-989`). The antigravity subclass returns `LOG_LINES` with the clean terminal `result` replaced by one that carries two `denied_actions`;
  - the exact `result --json` key set (`:842`) gains `context_lost`, and the test asserts its value (`None` on these fresh jobs). Do not loosen the key-set check.
- New `class AntigravityWorktreeTests(BackendLifecycle, unittest.TestCase)` with:
  - `BACKEND = "antigravity"`, `EFFORT = "model"`, `MODEL = "gemini-3.8-flash-low"`;
  - `WORKDIR_MARKER = 'cd "$WORKDIR"'` and `PERMISSION_MODE = "skip-permissions"`;
  - `LOG_LINES` built from P3's shapes (`init`, `step_update` agent and tool steps with `usage`, `result`);
  - `DENIED_TOOLS` matching its two denied actions' `display_name`s, sorted as `result` sorts them.
- New tests in that class (names fixed):
  - `test_a_role_less_antigravity_job_gets_the_only_effort`: `--backend antigravity --model <id>` with no `--effort`; and the same with no `--model` is refused
  - `test_submit_refuses_a_wrong_effort_and_an_empty_or_auto_model`: `effort=high`, `model=""`, and `model=auto`, each naming `handoff-config.py set`
  - `test_a_backend_that_contradicts_the_role_is_refused_both_ways`: an antigravity role with `--backend cursor`, and a cursor role with `--backend antigravity`
  - `test_the_ide_launcher_named_agy_is_never_resolved`: decoy first on PATH and the real fake later, so `meta` records the later path. With only the decoy, submit fails naming `HANDOFF_ANTIGRAVITY_BIN`
  - `test_an_antigravity_override_that_fails_the_identity_check_is_refused`: one help text without the flags, and one with both flags but a non-zero exit
  - `test_resume_rechecks_a_replaced_parent_binary`: the parent's recorded path is swapped for the decoy before `resume`, which then resolves afresh
  - `test_antigravity_command_lines_never_carry_refused_flags`: the R2.4 list, over fresh and resume, both postures, and read-only
  - `test_the_bypass_warning_prints_under_default_and_allow_all`: and never under read-only; the remedy line names `--read-only`
  - `test_handoff_writes_nothing_under_gemini`: with `HOME` pointed at the test root, no `.gemini` path exists after submit and resume
  - `test_a_launch_rejection_has_no_session_and_one_error`: MUNKNOWN's shape, exit 1, for a fresh job and for a resume round. The resume round's `context_lost` is `None`, and `status` prints no context line
  - `test_a_session_read_before_init_is_not_cached_empty`: `status` and `result` on an empty log, then `init` appended, then `result` reports the id and `resume` uses it
  - `test_a_resume_of_a_resume_passes_the_recorded_model`
  - `test_an_antigravity_resume_does_not_prewrite_the_session_cache`
  - `test_a_lost_session_is_reported_in_status_text_and_json`: RUNKNOWN's shape; `context_lost: true`, and the new id as `session_id`
  - `test_a_cut_off_antigravity_log_reports_unknown_denials`: no `result` event, in `status`, `result`, and `result --json` (`null`), and the same for a terminal job with an empty log
  - `test_a_killed_job_resumes_after_init_and_is_refused_before_any_event`: the antigravity counterpart of the cursor killed-job test in `CursorWorktreeTests` (`:1490`), which the new subclass does not inherit
  - `test_the_agent_message_is_the_last_agent_response_step`: P3's shape, where `result.response` differs from the last step
  - `test_resume_usage_is_the_round_s_own_steps`: RSAME's shape, where the step sum is 12143 and `result.usage` is 23989
  - `test_a_print_timeout_line_becomes_an_error`: TIMEOUTR's stderr line with a clean `SUCCESS` result
  - `test_an_unexpected_antigravity_shape_is_skipped_not_fatal`
  - `test_context_lost_is_null_on_other_backends`: one codex and one cursor job.

The 10-second identity timeout gets no test: one would add ten seconds to every run of the suite. The driver checks the timeout argument in review.

**Tests in `tests/test_handoff_config.py`:** `antigravity` joins the tuple in `test_every_supported_backend_validates()`. The `gemini` unknown-backend placeholder stays.

**Acceptance:**
```bash
bash -n scripts/delegate-codex.sh
python3 -m unittest tests.test_delegate_role tests.test_handoff_config
python3 -m unittest discover -s tests        # whole suite, no regressions
bash scripts/check-skill-repo.sh .           # fail=0, warn count unchanged
```

---

### Task 2: Setup engine, terminal wizard, smoke, and web wizard (row D)

Requirements: R1.1 (setup), R1.2, R1.3, R1.5, R6.1 to R6.7, R9.2 (setup suites). Design: decisions 1, 2, 11, 12.

**Files:** `scripts/handoff-setup.py`, `scripts/handoff-setup-ui.py`, `assets/setup-ui.html`, `tests/test_handoff_setup.py`, `tests/test_handoff_setup_ui.py`.

**Changes in `handoff-setup.py`:**
1. `ANTIGRAVITY_EFFORTS = ("model",)` and a `BACKEND_EFFORTS` row. The effort refusal suffix in `validate_backend_efforts` (`:115`) names Antigravity as well.
2. `validate_backend_models` covers `("copilot", "cursor", "antigravity")`. Its empty-model message keeps its current text and gains the fix it lacks today, `handoff-config.py set --role <identity> --backend <backend> --model <model>`, for all three backends (R1.3). `parse_identity_values` (`:324`) still refuses an empty `--role-model` value first, with its own message. The test drives the real entry point: a preset identity switched to antigravity with `--role-backend` and no `--role-model`.
3. New `is_antigravity_bin(path, env)` and `antigravity_bin(env)`: the identity check, run with `HANDOFF_ANTIGRAVITY_BIN` first, then each `agy` on PATH in order. Mirror `is_github_copilot_bin` and `copilot_bin` (`:151-200`).
4. `cli_available` gains the antigravity case.
5. New `antigravity_catalogue(binary, env)`: runs `[binary, "--output-format", "json", "models"]` with `timeout=15`, parses stdout as one JSON object, and returns `(id, label)` pairs from `command.data.models`, plus an error string ending with the fix. `auto` and an empty id are dropped. Non-JSON stdout is an error, not an empty list.
6. New `validate_antigravity_model(binary, model, env, *, cwd)`. It checks membership first, then runs `[binary, "-p", <sentinel prompt>, "--output-format", "stream-json", "--model", model]` with no posture flag and `timeout=120`. Pass: exit 0, a `result` with status `SUCCESS`, and `HANDOFF_SMOKE_OK` in the last `agent_response` step's joined text. A refusal returns stderr verbatim. Add a helper, `_antigravity_last_agent_text`.
7. The smoke branch for antigravity sits beside cursor's. On PASS it prints `PASS (id in agy models, run accepted by Antigravity)`.
8. The terminal wizard's backend prompt (`:1391`) names five backends, and the effort fill (`:1394`, `:502`) gives `model` on antigravity. Key both by a set, `MODEL_EFFORT_BACKENDS = ("cursor", "antigravity")`, and use it wherever the code says `backend == "cursor"` only to mean "the effort is the model".

**Changes in `handoff-setup-ui.py`:**
- `build_state` resolves `agy` once, through `antigravity_bin`, which runs the `--help` check. It then runs `agy --version` for the tile and passes that same binary to `_antigravity_model_options(binary, env)`, which calls `antigravity_catalogue`. Cursor's code resolves its binary twice, once for the version and again inside `_cursor_model_options`. Copying that would run `--help` twice and break the three-command page-load test. Add `model_options` and `model_discovery` entries.
- `_ensure_model_option` (`:590-622`) marks a retained antigravity id with an antigravity label ("Not in `agy models`"), as it does for cursor at `:613`. A discovery failure stays visible beside the retained value.
- A direct `clis["antigravity"]` entry beside Claude's, outside the `CLI_RELEASES` comprehension (`:722-732`).
- The `BACKEND_EFFORTS` lookups (`:608`, `:811`) work for antigravity, and `normalize_payload`'s catalogue guard (`:827`) covers it.

**Changes in `assets/setup-ui.html`:**
- `BACKEND_LABELS` (`"Antigravity"`) and `BACKEND_LABELS_ORDER`.
- `BACKEND_ICONS.antigravity`: a path from a permissively licensed icon set, credited in the attribution comment at `:439-440`. If no such icon exists, use a neutral glyph and say so in the comment.
- `PERMISSION_HINTS.antigravity`: the posture is a no-op, and read-only is plan mode.
- `syncPermissionWarning` gains an antigravity sentence, as cursor has, naming every antigravity identity whatever its posture.
- The `models()` source label becomes a map by backend, not the copilot/cursor ternary (`:858`).
- The effort lock and retained-id marking key on `["cursor", "antigravity"]`.
- The detection grid grows to six tiles (`:68`, `:279-299`), with the responsive rules adjusted so no tile wraps alone at the widths the page supports today.
- The subtitle (`:355`) names five backends.

**Tests:**
- `SetupTests` and the UI suites pop `HANDOFF_ANTIGRAVITY_BIN` in `setUp`.
- New `AntigravitySetupTests` beside `CursorSetupTests` (`tests/test_handoff_setup.py:1112`), covering:
  - effort `high` refused;
  - `auto` and an empty model refused;
  - catalogue parse: MJSONGLOBAL's real stdout, copied into the test;
  - non-JSON catalogue output gives an error naming the fix;
  - smoke pass on DRV_SMOKE's shape;
  - smoke fail on MUNKNOWN's shape, with stderr quoted;
  - smoke membership fail with no paid run (assert the fake was never called with `-p`);
  - smoke does not verify on exit 0 with a `result` whose status is `ERROR`, or when the sentinel appears only in an earlier `agent_response` step or only in `result.response`;
  - the decoy `agy` is never used by `cli_available`;
  - the terminal wizard: the exact backend prompt at `tests/test_handoff_setup.py:1264` names all five backends, and the cursor assertion beside it stays. A new test selects antigravity and asserts that the effort question is skipped (R6.3, R6.6).
- In `tests/test_handoff_setup_ui.py`:
  - an `AGY_UI_FAKE` beside `CURSOR_UI_FAKE`;
  - page load runs exactly `agy --version`, `agy --help`, and the catalogue command, and nothing else from agy;
  - normalize refuses an antigravity model outside the list;
  - a retained id stays marked;
  - update an assertion only when it pins the set of **backends**. These pin something else and stay unchanged: `:1182-1195` pins the three `CLI_RELEASES` entries, `:1232` pins Claude's four probe field names (add an antigravity probe-field assertion beside it), `[True, True, True]` at `:1343` counts release lookups, and `:861` checks Cursor's typed model picker;
  - `:1032` asserts the cursor effort lock; it follows the set-keyed lock without losing what it checks;
  - the exact effort dictionary at `:235` gains `"antigravity": ["model"]`;
  - check each remaining backend-set assertion (`:703`, `:818`, `:1298`) by reading it before changing it.

**Acceptance:**
```bash
python3 -m unittest tests.test_handoff_setup tests.test_handoff_setup_ui
python3 -m unittest discover -s tests
bash scripts/check-skill-repo.sh .
```
Then the driver opens the wizard (`python3 scripts/handoff-setup-ui.py --repo <scratch> --no-open`) at 1440, 1024, and 390 pixel widths and checks the six tiles and the antigravity card. Memory notes say to restart the server after each row before browser review.

---

### Task 3: Evidence: receipt v8, cost receipt, session view (row B)

Requirements: R7.1 to R7.5, R7.7, R9.2 (receipt fixtures). Design: decisions 9, 10.

**Files:** as listed for row B.

**Changes:**
- `docs/receipt-schema.json`: the `antigravity_jobs` and `antigravity_job_durations` pair, `antigravity` in the `host` enum, `const: 8`, and the `$id` and description if they name the version.
- `scripts/make-receipt.py`:
  - `antigravity` joins `BACKENDS`;
  - `--antigravity-jobs` (default `0`);
  - the pair is emitted in field order after cursor's;
  - the version written becomes `8`;
  - the docstring lines naming backends are updated.
- `scripts/validate-receipt.py`: `REQUIRED_FIELDS`, `ROLE_HOSTS`, the count and durations tuples, and the version gate (`"8"`, with the "must be 8" message).
- `scripts/check-skill-repo.sh:179`: the PASS message says v8.
- `examples/session-receipt.md`: v8, with `antigravity_jobs: 0` and `antigravity_job_durations: none`.
- `scripts/render-cost-receipt.py`:
  - `SCHEMA_VERSION = "8"`, plus `BACKEND_FIELDS` and `USAGE_FIELDS` rows;
  - the antigravity fold: step-usage sum, reasoning from `thinking_tokens`, cache writes unknown, USD `None`, denials from `denied_actions`, unknown usage without a `result` event;
  - the cost cell "n/a - no cost figure" (`:621-626`);
  - the Antigravity summary card, the outside-driver caveat, and the denials sentence.
- `assets/cost-receipt.html`: the same cost cell (`:359-362`), card, caveat, and denials sentence.
- `scripts/handoff-session-ui.py`: an antigravity `denials()` branch before the `!= 'copilot'` refusal (`:223`). It returns the `denied_actions` display names, and "not reported" with no `result` event.

**Tests:**
- `tests/test_receipt.py`:
  - `fields()` moves to v8 with the new pair;
  - `test_old_schema_version_fails` (`:110`) expects "must be 8" for a v7 receipt;
  - new `test_jobs_split_five_ways`: one job directory per backend, five correct counts;
  - new `test_an_antigravity_host_is_accepted_in_roles_used`;
  - the `gemini` placeholders stay.
- `tests/test_cost_receipt.py`:
  - `receipt_text()` and the mixed-backend `CliTests` fixture move to v8;
  - `FourBackendRoundTripTests` (`:317`) becomes a five-backend round trip: one job per backend through `make-receipt.py`, `validate-receipt.py`, and `render-cost-receipt.py`. Rename it to match;
  - exact sentences that name the backends change with the code, without dropping what they check: the double-count caveat at `:727` names Codex, Cursor, and Antigravity, and the denials sentences at `:733`, `:831`, `:846`, `:886`, and `:1090` name all four reporting backends;
  - new tests:
    - `test_an_antigravity_resume_counts_only_its_own_steps`: RSAME's shape; the chain total equals parent plus round;
    - `test_an_antigravity_job_without_result_has_unknown_usage`;
    - `test_an_antigravity_cost_cell_reads_no_cost_figure` (Markdown and HTML);
    - `test_antigravity_denials_come_from_the_result_list`: 2 denials;
    - `test_an_unexpected_antigravity_shape_is_skipped`.
- `tests/test_session_ui.py`: an antigravity denials test with 2 actions, and one with no `result` event. The receipt fixture `tests/fixtures/session-view/receipt-20260910T003000Z.md` moves to v8.

**Acceptance:**
```bash
python3 -m unittest tests.test_receipt tests.test_cost_receipt tests.test_session_ui
python3 -m unittest discover -s tests
python3 scripts/validate-receipt.py examples/session-receipt.md
python3 scripts/make-receipt.py --start --repo "$SCRATCH" && python3 scripts/make-receipt.py --repo "$SCRATCH" \
  --phase review --claude-session x --checks ci --codex-jobs 0 --cc-jobs 0 --copilot-jobs 0 \
  --scope project --config-source project --roles-used '[]' --no-save | python3 scripts/validate-receipt.py -
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown && git diff --exit-code -- examples/showcase-cost-ledger.json
bash scripts/check-skill-repo.sh .
```
`$SCRATCH` is a fresh `git init` directory from `mktemp -d`, never this repo, so the roundtrip leaves no `.handoff/` behind.

---

### Task 4: Transcript viewer (row C)

Requirements: R7.6. Design: decision 7.

**Files:** as listed for row C, without `tests/test_render_transcript.py`: `render-transcript.py` passes `meta` through unchanged, and its tests render no fixture per backend.

**Changes in `assets/transcript-viewer.html`:**
- `HV.inferBackend` returns `antigravity` for a parsed line with a top-level `event` key, checked before it returns `null` (`:385-397`).
- `HV.normalize` gains an antigravity branch, chosen by `meta.backend` as the others are:
  - `step_update` folds by `step_index`. `agent_response` becomes an agent row with its deltas joined. `tool` becomes one tool row with `CommandLine`, `TargetFile`, or `AbsolutePath`, its `output`, and a `DONE` or `ERROR` outcome with `tool_info.error.message`;
  - `user_input` is dropped;
  - `init` (model, cwd, permission mode), `system_message`, `error_message`, `command_result`, and `result` (status, error, denied actions) become lifecycle rows.

**Tests:**
- New fixture `tests/fixtures/transcript/antigravity-basic.jsonl`, trimmed from P3's real log: one `init`, two agent steps, one shell tool step, one write tool step, one read tool step (`AbsolutePath`), and the `result`. Shorten paths to `<repo>`. A second, small fixture carries the lifecycle shapes: a `system_message` step (RSAME), an `error_message` step (RO1), and a `command_result` (RMODEL).
- `tests/test_transcript_viewer.mjs`:
  - the fixture renders the expected rows;
  - `inferBackend` gives `antigravity` from line one;
  - a `user_input` step produces no row;
  - an `ERROR` tool step shows its message;
  - `system_message`, `error_message`, and `command_result` each become a lifecycle row;
  - the existing baseline test at `:361` reads its fixtures from `Object.entries(baseline-rows.json)` (`:366`), not a glob. Leave `baseline-rows.json` unchanged; the new fixtures need no exclusion code.

**Acceptance:**
```bash
node --test tests/test_transcript_viewer.mjs
python3 -m unittest discover -s tests
```

---

### Task 5: Skill prose, version, prompts, and evals (row E)

Requirements: R8.1, R8.2, R8.4, R8.5.

- Version 3.10.0 in `SKILL.md:3`, the README badge (`README.md:8`), and `docs/user-guide/agent-handoff.html:186`, with that page's "updated" date set to the release date. Add a `CHANGELOG.md` entry, `## v3.10.0 (<date>)`, in the style of v3.9.0's Cursor entry: what Antigravity is, its posture no-op, read-only, the lost-session warning, receipt v8, and the limits.
- Sweep every location in R2's prose inventory, section 7 (`.handoff/probes/agy/R2-inventory.md`), from four backends to five:
  - `SKILL.md`, its description included;
  - `README.md`, `CLAUDE.md`, and `PRODUCT.md`. `AGENTS.md` is an ignored symlink to `CLAUDE.md` (`.gitignore:9`); do not edit it separately;
  - `references/*.md` and the user guide;
  - the four diagrams, which still show three backends; add both missing ones;
  - `assets/promo.html`'s receipt lines (add `antigravity_jobs: 0`, and v8), keeping its animation timings consistent;
  - `test-prompts.json` and `evals/`. Rename the eval case to `receipt-contract-splits-five-backends.yaml` and update `evals/eval.yaml:33`.

  In that case, fix the mis-indented line at `:47`, which makes the file unparseable today. Then add `antigravity_jobs` to the `all:` list. Add `'(?m)^\s*receipt_schema_version\s*:\s*7\b'` to the `failure` list beside 5 and 6. Make the version rule require 8: replace the bare `'receipt_schema_version'` alternative, which any version satisfies, with `'(?m)^\s*receipt_schema_version\s*:\s*8\b'`, and change `schema (version )?7` to 8.
- The flow prose (`references/claude-driven.md`, and `SKILL.md` where Cursor's limits are stated) carries R8.4's four Antigravity limits and the retired-model recovery from R2.6.
- In-code prose that rows A and D did not own: none expected; report any found.

**Acceptance:**
```bash
ruby -ryaml -e 'ARGV.each { |f| YAML.load_file(f) }' evals/eval.yaml evals/cases/*.yaml   # run-test-prompts.py never reads evals/
git grep -n "3\.9\.5" -- . ':!CHANGELOG.md' ':!docs/specs/'        # prints nothing
git grep -n -i "four backends\|four worker\|four CLIs" -- . ':!CHANGELOG.md' ':!docs/specs/' ':!docs/research/'   # prints nothing
python3 scripts/run-test-prompts.py
bash scripts/check-skill-repo.sh .
python3 -m unittest discover -s tests
```

### Task 6: Design-of-record docs (row E)

Requirements: R8.3. Update `docs/specs/design_agent-handoff.md`, `design_agent-identities-and-config.md`, `design_agent-handoff-evidence.md`, and `design_session-visualisation.md` to describe what shipped, at the sections R8.3 lists. In `docs/specs/index.md`, mark the requirements and this plan `implemented`. Do not edit the Cursor or Antigravity requirements and design docs.

---

### Task 7: Live end-to-end verification (driver)

Requirements: R9.3.

1. Run everything `.github/workflows/checks.yml` runs, `bash install.sh --dry-run` included, with one change: run its receipt roundtrip against a scratch repo, as Task 3 does. Replaying `checks.yml:32` literally would overwrite this checkout's `.handoff/session-start`, which this run's receipt measures from. On the maintainer's machine `~/.claude/skills/agent-handoff` is a symlink to this checkout, so the flow already runs the branch's working tree. Record `readlink` of the skill path and `git rev-parse HEAD`, and do not run `install.sh` without `--dry-run`.
2. Build a scratch repo: `mktemp -d`, `git init`, a tracked file and a `check.sh` that prints `CHECK_OK`, and an **initial commit** of both, because `submit --worktree` needs a resolvable base commit (`delegate-codex.sh:469`). Stamp its receipt start with `make-receipt.py --start --repo "$SCRATCH"` before any job. Set `fast_worker` to `antigravity` / `gemini-3.8-flash-low` / `model` in its project config, and run `handoff-setup.py --smoke`.
3. Submit a job that edits the file and runs `bash check.sh`. Then `resume` it with a prompt that runs `pwd` and recalls a token from the first round. Then submit a `--worktree` job. Review its diff, commit it on the worktree branch (or discard it), and only then run `cleanup`, which refuses uncommitted edits. Finally, submit a `--read-only` job asked to write a file.
4. Check every bullet of R9.3 on disk and in `status`, `result --json`, the scratch receipt (`make-receipt.py --repo "$SCRATCH"` with `--antigravity-jobs`), and the cost receipt.
5. Record the results in a *Verification record* section of this plan: helper paths, installed revision, job ids, and anything that differed from the research doc. Record a difference in the research doc as a new section, as the Cursor run did.

## Verification record

Run by the driver on 2026-10-14 against a scratch repo. `fast_worker` was set to antigravity / `gemini-3.8-flash-low` / `model` with `permission_mode` unset, so the default posture applied. The other core identities were on codex, where smoke runs only a dry run. Job ids below are from that repo's `.handoff/jobs/`.

| R9.3 item | Evidence |
|---|---|
| Installed revision and helper paths | `~/.claude/skills/agent-handoff` is a symlink to this checkout, so `install.sh` was not run. Revision `2955304` on a clean tree. Helpers from this checkout's `scripts/`: `delegate-codex.sh`, `handoff-config.py`, `handoff-setup.py`, `make-receipt.py`, `validate-receipt.py`, `render-cost-receipt.py`, `render-transcript.py`. Worker: `/Users/keli/.local/bin/agy` 1.3.5, found on `PATH` (`codex_bin_source=path`). |
| Smoke (R6.5) | `fast_worker: PASS (id in agy models, run accepted by Antigravity)`. The whole smoke took 8.5 s, and it wrote `verified=true`. |
| One expected edit on disk | `job-2026-10-15T00-32-17-45949-e2e-edit`: `tracked.txt` went from `alpha` to `beta`, and nothing else changed. |
| A named repo check ran, output in `log.jsonl` | The same job's `run_command` step for `bash check.sh` ended `DONE` with `tool_info.output` `CHECK_OK`. `result --json` lists `commands: ["bash check.sh"]`. |
| `meta` records `backend=antigravity` | Edit job `meta`: `backend=antigravity`, `effort=model`, `permission_mode=skip-permissions`, `permission_posture=default`. `run.sh` carries `--model "$MODEL" --print-timeout 0s --dangerously-skip-permissions`. The submit printed the bypass warning with the Antigravity remedy line. |
| `resume` on the same conversation, usage counted once, `pwd` in the parent's directory | `...-e2e-edit-r2`: `meta` has `mode=resume` and `resumed_session=8a216b6b-3e68-4bf5-a250-db3a41c7f778`. `run.sh` re-passes `--conversation`, `--model` and the posture. Its `init` names the same conversation id, and `result --json` reports `context_lost: false`. The round's `pwd` printed the scratch repo, and it recalled the token `ZEBRA-4417` from the parent. Usage: 27,179 tokens summed from the round's steps against 77,907 cumulative in its `result`, so the round is counted once. |
| `--worktree` job pins its base and cleans up | `job-2026-10-15T00-33-39-48214-e2e-wt`: `base_commit=1fa1da0fc8e18d76de48a8115976725dc9917bce`, and `init.cwd` is the worktree. `notes.txt` was created only in the worktree, and the main tree stayed clean. The driver committed it on `e2e-wt` as `0d40bcd`, then `cleanup` removed the worktree. |
| `--read-only` job leaves the tree unchanged | `job-2026-10-15T00-34-14-49239-e2e-ro`: `meta` has `read_only=true` and `permission_mode=plan`. `run.sh` carries `--mode plan` and no skip flag, and no bypass warning was printed. `git status` was clean before and after, and `readonly-probe.txt` is absent. `agy` denied the job's first action, `ls -la`, and the turn ended with an empty response. `status` and `result` both report one denial (`RunCommand`) and the blocked-worker warning. |
| Receipt counts the jobs under `antigravity_jobs` | `receipt-20261014T163449Z.md`: `antigravity_jobs: 4` with four measured durations, `receipt_schema_version: 8`, `validate-receipt.py` PASS. |
| Cost receipt shows tokens, reasoning included | Four antigravity rows with input, cache read, output and reasoning (0 on this model), cache write `unknown`, and the cost cell "n/a - no cost figure". The Antigravity meter sentence appears, and the denial count is 1. The resume round's row shows its own 26,948 input tokens, not the cumulative figure. The meter's input total of 127,234 is the sum of the four rows. |
| Transcript viewer on real logs | The four `log.jsonl` files run through the shipped `viewer-normalize` block: `inferBackend` returns `antigravity`, with no unknown or unparsed rows. Each `run_command` folds into one command row with its output, and the denied `ls -la` row has outcome `ERROR`. `render-transcript.py` wrote the pages. |

Two results added to the research doc (section 12). First, plan mode refuses even a read-only shell command, so a read-only antigravity job ends at its first shell call. Second, a resumed round's shell runs in the parent's directory. One defect found and fixed: the bypass warning read "a antigravity worker". It now reads `WARN <jobId>: <backend> worker running with permission checks bypassed.`, which keeps the substring the tests assert.

## Not doing

Everything in the requirements' *Out of scope* list.

## Spec review

Reviewed once by `deep_reasoner` (codex / gpt-6.1-sol / xhigh, read-only) as job `job-2026-10-14T22-40-49-6645-spec-review`. The packet held the plan verbatim, the settled requirements and design, six check classes, and six points I was least sure of. Every finding was checked against the code before it was folded in, and none reopened a settled decision.

**Accepted, all nine.** Five were blocking:
1. `extract_session_id` caches an empty read as a newline, and the `-s` test then returns it for good. Task 1 step 10 now rescans on a whitespace-only cache and takes an antigravity id only from `init`, with a test that reads before `init`.
2. `BackendLifecycle` is a mixin that needs `unittest.TestCase`, a model for shared submissions, `WORKDIR_MARKER`, `PERMISSION_MODE`, and `DENIED_TOOLS`. `EFFORT` already existed. Task 1's test section is rewritten.
3. Named existing assertions that break: the `result --json` key set, the terminal wizard prompt, the UI effort dictionary, the cost receipt caveat, and five denial sentences.
4. Task 2 told the worker to change assertions that pin `CLI_RELEASES` entries, Claude's probe fields, and Cursor's model picker, not the backend set. Those now stay unchanged.
5. `cmd_status` skips the scan on an empty log, and a missing `init` id must not read as lost context. Task 1 step 12 handles both.

Four were advisory:
- coverage tests for R1.2 to R1.4, R2.1, R3.4, R3.5, R4.4, R6.3, R6.5, R6.6, R7.1, R7.2, and R7.6;
- `_ensure_model_option`, and a single `agy` resolution at page load;
- the empty-model message at the setup entry point;
- Task 7's scratch baseline commit, receipt start, worktree commit before cleanup, and the roundtrip kept out of this checkout. Also row E's eval, which accepted any schema version and did not parse, and a warning check that compared counts, not lines.

The reviewer found no conflict between same-wave rows. It also noted that `AGENTS.md` is an ignored symlink and that `tests/test_render_transcript.py` renders no per-backend fixture; both are corrected above.

**Declined:** none.

Driver change after the review: a role-less antigravity job must name a model, because its resume passes the model again (Task 1 step 5).
