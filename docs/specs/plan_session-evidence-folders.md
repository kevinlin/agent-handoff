# Session Evidence Folders Implementation Plan

> In this repository the plan runs under `/agent-handoff` as the rows in *Execution order*. Workers do not commit: each row's commit is the driver's, after its review.

**Goal:** every saved receipt names the Claude Code session that drove it, and all of one session's evidence lives in `.handoff/sessions/<date>_<session_id>/`. A shipped script moves existing repos to the new layout. Version 3.11.0.

**Spec:** [design_agent-handoff-evidence.md](design_agent-handoff-evidence.md), *The session folder* and Flows 1–4, and [design_session-visualisation.md](design_session-visualisation.md). This plan fixes what they leave open: the helper names, the migration algorithm, the row boundaries, the tests, and the commands that prove each row.

## Why

User testing turned up two problems:

- **7 of 53 receipts** across 16 local repos read `claude_session: none`. All 7 came from Claude Code sessions. The driver passed `--claude-session none` because the runtime prose said "`claude_session` is the current session" and gave it no way to read the id. Drivers that got it right had scraped the id out of the scratchpad path in their system prompt. Claude Code sets `CLAUDE_CODE_SESSION_ID` in every Bash tool subprocess, so the generator can read the id itself.
- **Evidence for one run is in four places**: `receipts/`, `receipts/diagram/`, `cost-receipts/` and `transcripts/`. A session folder puts it in one.

Measured facts the design rests on:

- 4 sessions hold 2–3 receipts each, so a file named only by session id would overwrite. The run stamp stays in every name.
- `render-cost-receipt.py run_interval()` reads the run's end from the `receipt-<stamp>.md` name. That is the second reason the stamp stays.
- Stored receipts span schemas 3 to 8; this repo alone has v6, v7 and v8. Anything that reads a stored receipt for its metadata must not require v8.
- 41 of 51 transcripts are indexed by some receipt's `*_job_durations`.
- Diagram names in the wild: `receipt-<stamp>_handoff-session-timeline.svg` (35), the same with `-` for `_` (1), `…@2x.png` (5). No code writes the PNG and nothing reads it.
- For each of the 7 `none` receipts, matching the generator's own stderr line `saved: <abs receipt path>` in `~/.claude/projects/*/*.jsonl` finds exactly one session.

## Layout

```
.handoff/sessions/<date>_<claude_session>/
  receipt-<stamp>.md
  cost-receipt-<stamp>.md
  cost-receipt-<stamp>.html
  transcript-<jobId>.html
  diagram/
    session-timeline-<stamp>.svg
    session-timeline-<stamp>@2x.png      optional; nothing reads it
```

- A folder's name is `<date>_<claude_session>`, for example `20261008_1c9ced0c-a654-4734-a856-d98a6aa1f7de`. `<date>` is `YYYYMMDD` in UTC, so a plain listing sorts by day. The session id is the key: every writer looks the folder up by id first, and only a session with no folder takes a new date, which then never changes. One session in 40 spans two dates, and it stays in one folder.
- A `none` receipt goes to the one bare `sessions/none/` folder, and the stamps keep its runs apart. `none` is not a session, so no single date describes it.
- A name under `sessions/` that is neither `none` nor `<8 digits>_<session id>` is not a session folder. Readers skip it.
- `<stamp>` is the receipt's `YYYYMMDDTHHMMSSZ`. A receipt named any other way, for example `notes.md`, uses its file stem in its place, as today.
- `jobs/`, `goal.md`, `session-start`, `rejected/`, `e2e/`, `worktrees/`, `probes/` and `backups/` do not move.

## Global constraints

- Version `3.11.0`. Receipt schema stays `8`; config schema stays `2`.
- No runtime fallback to `receipts/`, `cost-receipts/` or `transcripts/`. Where a reader finds nothing under `sessions/` and a legacy folder exists, the error carries the migration command.
- A session id is `none` or matches `[A-Za-z0-9][A-Za-z0-9._-]*` in full. The leading alphanumeric rules out `.`, `..`, hidden names and a leading `-`. The raw value is checked, never a stripped copy.
- Every writer refuses a symlink at `<repo>/.handoff`, at `sessions`, at the session folder, at `diagram`, or at the output file itself. Only components at or below `<repo>/.handoff` are checked: on macOS `/var` is itself a symlink to `/private/var`, and a temp repo lives under it.
- The skill surface (`SKILL.md`, `references/`, `scripts/`, `assets/`) never links into `docs/specs/`.
- Keep every existing comment and docstring, and every clause of prose that is still true. Give each new function a short "why" comment where the reason is not obvious.
- Never run `git stash`. Compare against HEAD with `git diff HEAD` or `git show HEAD:<path>`. Workers do not commit.
- Every test that runs `make-receipt.py`, `render-transcript.py`, `handoff-session-ui.py` or `migrate-sessions.py` sets or removes `CLAUDE_CODE_SESSION_ID` explicitly: `env=` for a subprocess, `unittest.mock.patch.dict(os.environ, …)` in process. The suite must give the same result inside Claude Code, where the variable is set, and in CI, where it is not.
- Compare paths with `Path.samefile` or after `.resolve()` on both sides: `tempfile.mkdtemp()` returns `/var/…`, which resolves to `/private/var/…`.
- No new risky command text (see `CLAUDE.md`).

## Shared helpers: `scripts/handoff_runtime.py`

One module owns the folder rules so that no two callers derive a path differently.

| Name | Contract |
| --- | --- |
| `SESSION_ENV = "CLAUDE_CODE_SESSION_ID"` | the variable Claude Code sets in Bash, hook and MCP subprocesses |
| `SESSION_ID` | `re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*")`, used with `fullmatch` |
| `RECEIPT_STAMP` | moved here from `render-cost-receipt.py`, unchanged: `^receipt-(\d{8}T\d{6}Z)\.md$`. `render-cost-receipt.py` keeps the name as an alias so existing imports work |
| `class EvidencePathError(ValueError)` | raised for an unsafe id or a symlinked component |
| `folder_session(name) -> str \| None` | the session id a folder name under `sessions/` belongs to: `none` for `none`; the id for `<8 digits>_<id>` when the id fullmatches `SESSION_ID` and is not `none`; else `None`, meaning not a session folder |
| `run_date(receipt_path) -> str \| None` | the `YYYYMMDD` of the name's `RECEIPT_STAMP`, else `None` |
| `session_path(repo, session_id, *parts, date=None) -> Path` | `<repo.resolve()>/.handoff/sessions/<folder>/<parts…>`. `<folder>` is `none` for `none`. Otherwise it is the first name, sorted, under `sessions/` whose `folder_session` equals `session_id`, else `<date>_<session_id>`, where a `None` date means today in UTC. Raises when `session_id` is not `none` and does not fullmatch `SESSION_ID`, when `date` is given and is not eight digits, when a part is not a single safe segment, or when any existing component from `.handoff` down to the last part is a symlink, dangling or not. `.handoff` and `sessions` are checked before `sessions` is listed. Creates nothing |
| `run_stamp(receipt_path) -> str` | the `RECEIPT_STAMP` group, else `receipt_path.stem` |
| `receipt_job_ids(text) -> set[str]` | job ids from every field whose key ends in `_job_durations`, read with `validate-receipt.py`'s `extract_block` (load it with `importlib`, as `make-receipt.load_validator` does). Schema-tolerant: it reads v3 to v8 alike, requires no job directory, and returns an empty set for text with no block |
| `owner_key(stamp, session_id)` | `(stamp, session_id)`. The one ordering used to pick the newest of several receipts: a greater stamp wins, ties go to the greater session id, and a non-stamped receipt has stamp `""` and sorts first |
| `saved_receipts(repo) -> list` | every `sessions/*/receipt-*.md` with its `owner_key`, oldest first. The key uses the session id from `folder_session`, not the folder name, so the date never affects ranking. Added in row A: `job_owner` and the cost renderer's default input share this one listing, so they cannot rank receipts differently |
| `job_owner(repo, job_id) -> str \| None` | the session id, from `folder_session`, of the receipt under `sessions/*/receipt-*.md` that indexes `job_id` and has the greatest `owner_key`. A file that cannot be read raises (`OSError`); a readable file with no block indexes nothing. A folder `folder_session` rejects is skipped |
| `transcript_path(repo, job_id, env=os.environ) -> Path` | `session_path(repo, owner, f"transcript-{job_id}.html")`, where the owner is `job_owner`, else a non-empty `env[SESSION_ENV]`, else `"none"`. An unsafe env value raises `EvidencePathError` |
| `legacy_layout_note(repo) -> str \| None` | when any of `.handoff/receipts`, `.handoff/cost-receipts` or `.handoff/transcripts` is a directory, one line naming them and the command `python3 <abs path of this module's folder>/migrate-sessions.py --repo <repo> --apply`. The path is computed from `Path(__file__).resolve().parent`, so it names the installed copy |

## Execution order

| Row | Identity | Scope | Depends |
| --- | --- | --- | --- |
| T0 | driver | design docs, this plan, `index.md`; committed before any code | spec review |
| A | fast_worker | Task 1: runtime and its tests | T0 |
| C | fast_worker | Task 3: skill prose, docs, version, CHANGELOG | T0 |
| B | fast_worker | Task 2: migration script, its tests, the two gate files | A |
| D | fast_worker | Task 5: date prefix on session folders | A, B, C |
| T4 | driver | Task 4: review, migration rehearsal, real migration of this repo | A, B, C, D |

A and C touch disjoint files and run in parallel. B starts once A's helpers are reviewed, and may overlap C. The SkillSpector baseline is re-checked once, after every row has finished, because its fingerprints carry line numbers.

### Task 1: Runtime (row A)

Files: `scripts/handoff_runtime.py`, `scripts/validate-receipt.py`, `assets/receipt-schema.json`, `scripts/make-receipt.py`, `scripts/render-cost-receipt.py`, `scripts/render-transcript.py`, `scripts/handoff-session-ui.py`, and tests in `tests/test_receipt.py`, `tests/test_cost_receipt.py`, `tests/test_render_transcript.py`, `tests/test_session_ui.py`.

1. **Helpers.** Add everything in *Shared helpers* to `handoff_runtime.py`.
2. **Validator.** `validate()` fails a `claude_session` that is neither `none` nor a `SESSION_ID` fullmatch, with a message naming the value. Import `SESSION_ID` from `handoff_runtime`; add the same `sys.path` insert the renderers use. In `assets/receipt-schema.json`, `claude_session` gains `"pattern": "^(none|[A-Za-z0-9][A-Za-z0-9._-]*)$"` and its description says the value names the receipt's session folder. Every stored receipt already conforms, which is why the schema version does not move.
3. **Generator.** In `make-receipt.py`:
   - `--claude-session` is optional, and `--phase` and `--checks` stay required unless `--start`.
   - The session id resolves in this order:

     | Flag | `CLAUDE_CODE_SESSION_ID` | Result |
     | --- | --- | --- |
     | omitted | set, non-empty | the env value |
     | omitted | unset or empty | `FAIL no Claude Code session id: CLAUDE_CODE_SESSION_ID is not set in this shell. Pass --claude-session <id>, or --claude-session none for a run no Claude Code session drove.` |
     | `none` | set, non-empty | `FAIL --claude-session none, but this shell runs inside Claude Code session <id>. Omit --claude-session to record it. To regenerate a receipt for a run no Claude Code session drove, unset CLAUDE_CODE_SESSION_ID for that one command.` |
     | `none` | unset or empty | `none` |
     | any other value | either | the flag value, which is how a past run's receipt is regenerated with `--ended-at` |

   - Each refusal prints to stderr and exits 1, like the other `FAIL` lines.
   - Before printing anything, call `session_path(repo, session_id)`. That checks the raw value, so `" abc"` is refused even though `validate()` strips it. An `EvidencePathError` is a `FAIL`, exit 1.
   - The save goes to `session_path(repo, session_id, f"receipt-{stamp}.md")`; create the folder with `mkdir(parents=True, exist_ok=True)`. The `saved: <path>` stderr line keeps its format.
   - After a save, print `legacy_layout_note(repo)`, when it is not `None`, as `note: …` on stderr.
   - Update the module docstring and the `--no-save` help.
4. **Cost receipt.** In `render-cost-receipt.py`:
   - `resolve_receipt` returns `.resolve()` of both a named file and the default. This is what makes the standalone renderer and the session page name artifacts identically: the page already resolves its input, so a symlink named `notes.md` that points at `receipt-<stamp>.md` gets the stamp in both.
   - The default, or `last`, is the `sessions/*/receipt-*.md` file with the greatest `owner_key(stamp, folder name)`, using `run_stamp` and the stamp-or-`""` rule.
   - The not-found error reads `no saved receipt found under .handoff/sessions/. Wrap up a run, or generate one with make-receipt.py.`, followed by `legacy_layout_note(repo)` when there is one.
   - `main` writes `cost-receipt-<run_stamp>.{md,html}` into `session_path(repo, fields["claude_session"])`. An `EvidencePathError` prints `ERROR: …` and exits 2 before anything is written.
   - The fail-closed reader (`load_receipt`) and its v8 requirement do not change.
5. **Transcript.** In `render-transcript.py`, split the write out of `main` as `render(job_dir, out_path, template_text) -> bool`, returning whether Git ignores `out_path`.
   - `main` computes `transcript_path(args.repo, job_dir.name)` once; an `EvidencePathError` is `ERROR: …`, exit 2.
   - It creates the parent folder, calls `render`, keeps the existing not-ignored warning, prints the path, and opens it.
6. **Session page.** In `handoff-session-ui.py`:
   - `Session.__init__` reads `claude_session` and calls `session_path` once. An `EvidencePathError` exits 2 through the existing handler.
   - `diagram()` and `diagram_target()` are both `session_path(repo, sid, "diagram", f"session-timeline-{run_stamp(receipt_path)}.svg")`; `diagram()` returns it only when it is a file. The `<stem>_*.svg` glob and the exact-stem form go away, and the migration renames those files.
   - `render_transcript(job_id)` computes `transcript_path(repo, job.name)` once, uses that one path both for the cache check and for `transcript.render(job, output, template)`, and never calls `transcript.main`. One render therefore cannot pick two destinations if a newer receipt appears halfway through.
   - The payload's Git-ignore banner checks `transcript_path(repo, job_id)`: the same function, so the banner inspects the path that is written.
   - `render_cost` builds its cache path as `session_path(repo, sid, f"cost-receipt-{run_stamp(receipt_path)}.html")` and still renders through `cost.main([...])`, which now computes the same folder. `output_path` goes; `session_path` replaces its guard.
   - The diagram prompt names the new target.
7. **Tests.** Update the paths in the existing assertions. A test that writes a receipt into `sessions/<date>_<x>/` gives it `claude_session: <x>`, because outputs follow the field. Add:
   - `test_receipt.py`:
     - each row of the session table above, plus an explicit id winning over the env value
     - the save landing at `sessions/<date>_<id>/receipt-<stamp>.md`
     - an unsafe id refused: `../x`, `a/b`, `.`, `-x`, `" abc"`
     - the legacy note on stderr when `.handoff/receipts/` exists
   - Validator cases in `test_receipt.py`: a UUID and `none` pass; `../x`, `a/b`, `.` and `..` fail.
   - `test_cost_receipt.py`:
     - fix `:520`, which passes `--claude-session none` while inheriting the environment
     - output in the receipt's session folder
     - `last` across two session folders, including the tie-break
     - the not-found error carrying the legacy note
     - a symlink input named `notes.md` producing `cost-receipt-<stamp>.*`, the same names the session page produces
   - `test_render_transcript.py`:
     - owner from an indexing receipt
     - from the env value when nothing indexes the job
     - `none` when there is neither
     - the newest of two indexing receipts in two sessions
     - a v6 receipt still indexing
     - an unreadable receipt raising rather than reading as unindexed
     - a symlinked `sessions` folder refused
   - `test_session_ui.py`:
     - the diagram path
     - the cost and transcript cache paths
     - the banner path equal to the written path
     - a job indexed by an older and a newer receipt in two sessions: viewing the older one writes the transcript to the newer session's folder, and the cost receipt to the older one's

Acceptance: `python3 -m unittest discover -s tests` and `node --test tests/test_transcript_viewer.mjs` pass; the baseline was 914 and 56/56, so the unit count only grows. `python3 -m py_compile` passes on every changed script.

### Task 2: Migration (row B)

Files: new `scripts/migrate-sessions.py` and `tests/test_migrate_sessions.py`; add `check_file "scripts/migrate-sessions.py"` to `scripts/check-skill-repo.sh` and the script to the `py_compile` line in `.github/workflows/checks.yml`.

CLI: `migrate-sessions.py [--repo PATH] [--apply] [--projects-root PATH]`. `--repo` defaults to the current directory and `--projects-root` to `~/.claude/projects`. Without `--apply` it prints the plan and writes nothing. A repo with no `.handoff/` exits 2.

**Step 1: inventory, no mutation.** Ownership is computed before anything moves, from both the legacy receipts still in `receipts/` and the receipts already under `sessions/`. That is what lets a run resume after an interruption.

- *Legacy receipts* are regular files in `.handoff/receipts/` whose names match `RECEIPT_STAMP`. Read each with `extract_block` (schema-tolerant), never `validate()` or `load_receipt()`.
  - When `claude_session` fullmatches `SESSION_ID`, it is the planned session.
  - When it is `none`, run recovery.
  - Anything else, or no block at all, means the file stays where it is and is reported.
- *Recovery* runs only when there is at least one `none` receipt. The needle for each receipt is `saved: <repo.resolve()>/.handoff/receipts/<name>`. Read every `<projects-root>/*/*.jsonl` once.
  - A file's stem is its session id. Skip the file whose stem equals the current `CLAUDE_CODE_SESSION_ID`, then count distinct session ids per needle.
  - Exactly one, and it passes `SESSION_ID`: plan a rewrite to that id, and record which transcript file matched.
  - Zero, or more than one: the receipt goes to `sessions/none/` untouched.
- *Migrated receipts* are the `sessions/*/receipt-*.md` files. Their folder name is their session.
- *Stamp map*: stamp to session, from both sets. When one stamp maps to two different sessions, it is ambiguous: its dependents stay and are reported.
- *Job map*: job id to session, through `receipt_job_ids` and `owner_key` across both sets. Use the same ordering as `job_owner`.
- *Conflicted receipts*: a legacy receipt whose target already exists (`os.path.lexists`, so a dangling symlink counts) stays put. Its stamp's dependents stay too, and so does any transcript whose newest owner it is. An existing file is never evidence that a source's dependents belong to it.
- *Cost receipts* are files in `.handoff/cost-receipts/` named `cost-receipt-<suffix>.md` or `.html`. When `<suffix>` is a mapped stamp, the file goes to that session; otherwise it goes to `sessions/none/`. The name is kept.
- *Diagrams* in `.handoff/receipts/diagram/` named `receipt-<stamp>[_-]handoff-session-timeline(@2x)?.(svg|png)` or `receipt-<stamp>.svg` go to `sessions/<session of stamp, else none>/diagram/session-timeline-<stamp>[@2x].<ext>`. When two sources map to one target, the second is a conflict.
- *Transcripts* `.handoff/transcripts/<jobId>.html` go to `sessions/<job map owner, else none>/transcript-<jobId>.html`.
- Everything else in the three legacy folders is left and reported, for example the `receipt-<stamp>-cost.{md,html}` files seen in one repo. So is any symlinked source. The exception is `.DS_Store`, which cleanup deletes.
- Every target is built with `session_path`, so an unsafe id or a symlinked destination folder is refused for that file and reported.

**Step 2: print the plan.** One line per action:

- `move <src> -> <dst>`
- `rewrite <src>: claude_session none -> <id> (matched <jsonl path>)`
- `skip <src>: <reason>`
- `leave <src>: unrecognised`
- `delete <src>: .DS_Store`, so the one deletion shows in a dry run

**Step 3: apply** (only with `--apply`), in order: receipts, cost receipts, diagrams, transcripts, cleanup. Create target folders with `mkdir(parents=True, exist_ok=True)` after the `session_path` check.

- *Move*: `os.link(src, dst)`, then `os.unlink(src)`. The link fails if `dst` exists, so nothing is ever overwritten, even by a file that appears after the inventory.
- *Rewrite*:
  - Replace the one line matching `^claude_session:[ \t]*none[ \t]*$` inside the receipt block. More or fewer than one match is a failure.
  - Check that `extract_block` of the new text equals that of the old text in every field except `claude_session`, which must now equal the id. The schema version is never touched.
  - Write the new text to a temp file in the target folder, `os.link` it to the target, remove the temp file, then unlink the source.
  - On any failure, remove the temp file, leave the source untouched, and report it.
- *Cleanup*: delete `.DS_Store` in the legacy folders. Then `rmdir` `receipts/diagram`, `receipts`, `cost-receipts` and `transcripts`, each only when it is empty.

**Step 4: re-render reminders.** These are stateless and print in both modes, so they survive an interruption. For every `sessions/<date>_<id>/cost-receipt-<stem>.md` where `<id>` is not `none`, whose `- Session:` line reads `` `none` ``, and for which `sessions/<date>_<id>/receipt-<stem>.md` exists, print `re-render: python3 <script folder>/render-cost-receipt.py <receipt path> --repo <repo> --no-open`.

**Exit**: 0 when nothing was skipped, left behind, or failed, including a second run with nothing to do; 1 otherwise; 2 for bad arguments or no `.handoff/`.

**Tests** (`tests/test_migrate_sessions.py`, fixture trees built in temp dirs; set or remove `CLAUDE_CODE_SESSION_ID` in each):

- A legacy tree turns into the expected `sessions/` tree. The tree holds v6, v7 and v8 receipts; two receipts in one session; a cost receipt and a diagram whose stamp has no receipt; all three diagram name forms plus a PNG; an indexed and an orphan transcript; a stray `receipt-<stamp>-cost.md`; and a `.DS_Store`.
- `none` recovery against a fake `--projects-root`:
  - one match: rewritten, and validated structurally
  - zero matches: untouched, filed under `none`
  - two sessions: untouched, filed under `none`
  - a match only in the current session's file: excluded
  - a transcript that mentions the path without the `saved: ` prefix: not a match
- Dry run writes nothing; the tree's listing and file hashes are unchanged.
- Re-run: a second `--apply` exits 0 and changes nothing.
- Interruption at each boundary, built as a partial tree:
  - receipts moved but nothing else: dependents follow the migrated receipts
  - a rewritten receipt already moved while its cost receipt is still legacy: the cost receipt follows, and the re-render reminder prints
  - a cost receipt moved while transcripts are still legacy
- Conflicts:
  - an existing target, and a dangling symlink at the target: the source stays, exit 1, and its dependents stay
  - two diagram sources mapping to one target
- A symlinked source is left. A symlinked `sessions/` folder is refused.
- A rewrite whose block check fails leaves the source byte-identical.

Acceptance: the new tests pass; `bash scripts/check-skill-repo.sh .` gives fail=0; `python3 -m py_compile scripts/migrate-sessions.py` passes.

### Task 3: Skill prose, docs, version (row C)

Replace every runtime and user-facing mention of the legacy paths with the session folder, say what the generator now does with the session id, and bump the version.

- `SKILL.md`
  - version `3.11.0` (line 3)
  - the cost receipt section (lines 69–71): newest saved receipt under `.handoff/sessions/`, written beside its receipt in that session's folder
  - visualise (94, 133): the diagram target `.handoff/sessions/<date>_<session_id>/diagram/session-timeline-<stamp>.svg`
  - Output Contract (209, 215): the save path. Replace "`claude_session` is the current session" with: the generator reads it from `CLAUDE_CODE_SESSION_ID`, so omit `--claude-session` unless regenerating a past run's receipt, and `none` is refused inside Claude Code
  - keep the template line `claude_session: <sessionId or none>`
- `references/claude-driven.md:160`: the same two changes, in the Phase 5 command's own words. `references/tryout.md:33`: the receipt path.
- `test-prompts.json:9`: the path in `session-receipt-required`. `evals/cases/cost-receipt-renders-not-delegates.yaml` lines 8 and 33: path and pattern become `.handoff/sessions`.
- `README.md` lines 101 and 105: path examples. Add a short upgrade note naming the migration command: `python3 ~/.claude/skills/agent-handoff/scripts/migrate-sessions.py --repo <repo>`, run once without `--apply` to see the plan.
- `docs/user-guide/agent-handoff.html`: lines 196 (version), 598 and 658.
- `docs/user-guide/diagrams/evidence-flow.svg:82` and `handoff-lifecycle.svg:245`: `sessions/`. `docs/skill-file-references/file-references.svg`: line 45 (version) and line 156, `.handoff/{jobs,sessions,e2e}/`.
- `.gitignore` comment at line 25: `receipts` becomes `sessions`.
- `CHANGELOG.md`: a `## v3.11.0 (<date>)` entry above v3.10.1, in the existing style.
  - **breaking**: the layout, and the migration command
  - fix: the env-sourced session id, and the refusals
  - note: the narrowed `claude_session` grammar under the unchanged schema 8
  - note: the diagram's new name
- Leave alone: the implemented `docs/specs/plan_*.md` files and `examples/v3.6.1-conversation-cost-receipt.{md,html}`. They record what ran at the time.

Acceptance: `bash scripts/check-skill-repo.sh .` gives fail=0; `python3 scripts/run-test-prompts.py` passes. And this prints nothing:

```bash
grep -rn -e '\.handoff/receipts' -e '\.handoff/cost-receipts' -e '\.handoff/transcripts' \
  -e 'receipts/diagram' -e 'is the current session' SKILL.md references README.md docs/user-guide test-prompts.json evals
```

### Task 4: Review and migration rehearsal (driver)

1. Review each row's diff against its task, removed lines included (`git diff | grep '^-'`).
2. Rehearse on a scratch copy:
   ```bash
   S=$(mktemp -d); mkdir "$S/repo"; cp -R .handoff "$S/repo/.handoff"
   python3 scripts/migrate-sessions.py --repo "$S/repo"           # plan only
   python3 scripts/migrate-sessions.py --repo "$S/repo" --apply
   python3 scripts/migrate-sessions.py --repo "$S/repo" --apply   # nothing to do, exit 0
   ```
   The copy's receipts have new absolute paths, so real transcripts cannot match them. Recovery on a copy is covered by the `--projects-root` fixture tests. Real recovery is checked with a dry run of the real repo: `python3 scripts/migrate-sessions.py --repo ~/dev/ai-sdlc/toolchain/apm-lite` should plan the rewrite of both of its `none` receipts to `86cdd857-…` and write nothing.
3. Migrate this repo for real: `python3 scripts/migrate-sessions.py --repo . --apply`. Then confirm `python3 scripts/render-cost-receipt.py .handoff/sessions/20261026_7743ad0c-f043-4f41-8f43-8ab5f785ba3f/receipt-20261026T144123Z.md --repo . --no-open` writes beside that receipt. Also confirm `python3 scripts/handoff-session-ui.py .handoff/sessions/20261026_7743ad0c-f043-4f41-8f43-8ab5f785ba3f/receipt-20261026T144123Z.md --repo . --no-open` finds `diagram/session-timeline-20261026T144123Z.svg`; that is a v8 receipt that has a diagram.
4. Ask the user before migrating any other repo.
5. Receipt roundtrip, both id sources:
   ```bash
   T=$(mktemp -d); python3 scripts/make-receipt.py --start --repo "$T"
   CLAUDE_CODE_SESSION_ID=abc-1 python3 scripts/make-receipt.py --repo "$T" --phase review --checks ci   # saved under sessions/<today>_abc-1/
   env -u CLAUDE_CODE_SESSION_ID python3 scripts/make-receipt.py --repo "$T" --phase review --checks ci --claude-session x-2 --no-save | python3 scripts/validate-receipt.py -
   ```
6. Wrap up: this run's own receipt is the first one written to the new layout.

### Task 5: Date prefix on session folders (row D)

Added after rows A, B and C were reviewed, at the maintainer's request. The tree holds their uncommitted work; this row changes it in place. Nothing has shipped with unprefixed folders, so no code handles them: a bare `sessions/<id>/` folder is simply not a session folder.

Files: `scripts/handoff_runtime.py`, `scripts/make-receipt.py`, `scripts/render-cost-receipt.py`, `scripts/handoff-session-ui.py`, `scripts/migrate-sessions.py`, and the five test modules that build session trees.

1. `handoff_runtime.py`: add `folder_session` and `run_date`, change `session_path`, and have `saved_receipts` key on `folder_session`, as *Shared helpers* now says. `job_owner` keeps returning a session id, so `transcript_path` needs no change. Its env fallback passes no date, which means today.
2. `make-receipt.py`: pass `date=ended.strftime("%Y%m%d")` when it builds the save path. Update the module docstring and the `--no-save` help.
3. `render-cost-receipt.py` `main`: pass `date=run_date(receipt_path)`.
4. `handoff-session-ui.py`: `diagram_target` and `render_cost` pass `date=run_date(self.receipt_path)`, so the page and `cost.main` agree when a receipt's folder does not exist yet.
5. `migrate-sessions.py`:
   - Before any `place()` call, compute the earliest stamp's date per session across the legacy receipts, recovered ids included. Pass it as `date=` when planning and again when `apply_plan` rebuilds the path. An existing folder keeps its name, because `session_path` finds it first.
   - `reminders` uses `folder_session`, not `SESSION_ID`.
   - Update the module docstring.

Tests:

- `folder_session`: `none`; a UUID with a date; an id containing `_`; rejected: a bare id, `20261008_none`, `2026100_x`, `20261008_`, `20261008_../x`.
- `run_date`: a stamped name, and `notes.md` giving `None`.
- `session_path`: a new folder takes the given date; a `None` date gives today, with the clock patched; an existing folder wins over a different given date; two folders for one id pick the first sorted name; `none` ignores the date; a bad `date` raises; a symlinked `sessions` is refused before it is listed.
- `make-receipt.py`: a save names `<ended date>_<id>`, and a second run on a later date, through `--ended-at`, lands in the first run's folder.
- `render-cost-receipt.py`: a receipt with no folder yet creates one dated from its stamp; `last` ranks across folders by stamp, not by folder date.
- `migrate-sessions.py`: a session with receipts on two dates lands in one folder named for the earlier; two runs interrupted between those receipts still land in one folder; a `none` receipt whose session is recovered takes its earliest receipt's date; `none` stays bare.

Acceptance: the Task 1 and Task 2 suites pass with the variable set and unset, with no test deleted or weakened; the paths they assert gain the prefix.

## Verification

```bash
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py
bash install.sh --dry-run
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
```

## Not doing

- Moving `jobs/`, `goal.md`, `session-start`, `rejected/` or `e2e/` into a session folder.
- Reading the legacy layout at runtime.
- Requiring or generating the `@2x.png`.
- Re-rendering cost receipts inside the migration. It prints the commands instead.
- Fixing the cost receipt's driver row after a `/clear`. The design's risks section records it.

## Spec review

Round 1, `job-2026-10-31T18-08-40-47349-spec-review` (deep_reasoner, codex gpt-6.1-sol xhigh, read-only), returned 4 blocking and 4 advisory findings. All were verified against the code and folded in above:

- The readers never call `validate()`, so the id check lives in `session_path`, which every writer calls.
- Stored receipts span schemas 3–8, so metadata is read with `extract_block`.
- Partial apply needs ownership computed up front, and no-overwrite installs.
- The page and the standalone renderer had to agree on input normalization and symlink guards.
- Advisory: deterministic owner ordering and a pinned path per render; a corrected verification block; environment isolation beyond `test_receipt.py`; orphan diagrams and the gate files.
