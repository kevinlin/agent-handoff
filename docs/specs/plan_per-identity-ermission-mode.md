# v3.7.1 — per-identity `permission_mode`

## Context

Today a delegated worker's permission posture is decided by the backend, not by the user:
a claude worker always gets `--permission-mode bypassPermissions`, a copilot worker always
gets `--allow-all-tools`, and a codex worker gets whatever `~/.codex/config.toml` says. The
only dial is `HANDOFF_CLAUDE_PERMISSION_MODE`, which is claude-only and undiscoverable.
`docs/specs/plan_support-cc-to-cc-handoff.md:274` recorded the earlier decision — *"No
per-identity `permission_mode` field — the env override covers the sandbox case"* — and this
change reverses it.

Add one field to the identity triple so each identity carries its own posture, using the
two-value abstraction from `docs/research/cross-agent-cli-permissin-mode.md`:

- `default` — the CLI's own normal, bounded posture. Nothing prompts (a background job has
  no approval surface), so an unapproved action **denies**.
- `allow-all` — that CLI's native unrestricted mode.

The research doc's own closing line is the contract: `allow-all` means *use the provider's
native unrestricted mode*, not force three CLIs into one security posture.

This plan was reviewed by `deep_reasoner` (job `job-2026-09-10T00-37-42-59402-spec-review`,
codex/gpt-6-astra/xhigh, read-only, 0 denials). Every finding is folded in below; the three
I ruled against are named under "Rulings".

## Flag mapping

Every flag verified against the installed CLIs' `--help`.

| Backend | `default` | `allow-all` |
|---|---|---|
| claude | `--permission-mode dontAsk` + `--allowed-tools` worker set | `--permission-mode bypassPermissions` *(today)* |
| codex (fresh) | no sandbox flag — `~/.codex/config.toml` decides *(today)* | `--dangerously-bypass-approvals-and-sandbox` |
| codex (resume) | no sandbox override *(today)* | `--dangerously-bypass-approvals-and-sandbox` |
| copilot | `--allow-all-tools` *(today)* | `--allow-all-tools --allow-all-paths --allow-all-urls` |

`--read-only` still overrides everything on all three (`-s read-only` /
`--permission-mode plan` / `--mode plan`), unchanged.

**Only claude changes behaviour under `default`.** Codex and copilot `default` are exactly
what they emit today; `allow-all` is a new, opt-in escalation on all three. That is a
deliberate narrowing of blast radius for a patch release.

claude's `default` tool set is `Read Glob Grep Edit Write Bash` — enough to edit and verify,
and a real narrowing: `dontAsk` makes the user's own settings.json `deny`/`ask` rules hard
denials instead of bypassing them, and MCP, browser, WebFetch and delegation tools are no
longer blanket-approved. The set is *not* applied to `--read-only` jobs.

### Deviations from the research doc, each named on purpose

1. **codex `default` passes no sandbox flag**, where the doc says `workspace-write`.
   Probed: under `workspace-write` an ordinary file write succeeds but `.git/` writes are
   refused, and `sandbox_workspace_write.writable_roots` on the repo root does not lift it.
   Forcing it would break `references/e2e-gauntlet.md:29` — the worker-commits-on-its-
   worktree-branch contract — for any user who has not granted `.git` per repo, and would
   narrow a `danger-full-access` user whose e2e worker commits today. Inheriting the user's
   codex config keeps `docs/specs/design_agent-handoff.md:207` ("codex workers are bounded
   by the sandbox in the user's own codex config") true instead of quietly false.
2. **codex has no `--ask-for-approval` on `exec`** (top-level flag only). `codex exec`
   never prompts, so the doc's `approval=never` half is already satisfied.
3. **copilot `default` keeps `--allow-all-tools`**, where the doc lists `--allow-tool=write`
   plus stack-specific test/build/lint rules. The doc does permit test execution; the
   unsolved part is enumerating those commands per repo, which Handoff cannot know.
   `--allow-all-tools` leaves Copilot's path and URL verification on; `allow-all` turns
   those off.

### Stated limitations, in the docs and not only here

- **No OS-level sandbox for claude `default`.** The doc's claude row also asks for enforced
  sandboxing, workspace-only IO, and denied network. This release changes the
  permission-rule layer only. `references/darwin-ratchet.md` is explicit about one dimension
  per change, and network denial interacts with a worker's ability to install and test.
  Document the gap rather than implying containment the flags do not provide.
- **Codex denials are not counted.** `STATUS_SCAN_PY` counts a Claude-shaped
  `subtype: permission_denied` event and a copilot `error.code: "denied"`; a codex sandbox
  refusal matches neither, and `cmd_result` keeps a `command_execution` item's command but
  discards its outcome (`delegate-codex.sh:870`). The shared codex denial test feeds a
  Claude-shaped event (`test_delegate_role.py:360`), so it proves nothing about codex.
  Out of scope here; say so where the flow claims the denial count is the safety net.
- **`permission_denied` is advisory, not enforced.** `job_state` derives DONE from the exit
  code (`delegate-codex.sh:332`) and `cmd_status` warns but still succeeds
  (`delegate-codex.sh:779`). The prompt-shaped guard stays prompt-shaped.

## Changes

### 1. `scripts/handoff-config.py` — the field

- `PERMISSION_MODES = ("default", "allow-all")`; `DEFAULT_PERMISSION_MODE = "default"`.
- `IDENTITY_FIELD_ORDER`: insert `permission_mode` after `effort`.
- `_validate_data`: optional; when present must be in `PERMISSION_MODES`.
- **`resolve_config` materializes the default** onto every configured identity after the
  merge, so absence is resolved in exactly one place and no consumer has to interpret it.
  This also settles the merge case: a global `allow-all` plus a project identity that omits
  the field keeps `allow-all`, because the merge is per field and materialization runs last.
- `set`: `--permission-mode {default,allow-all}`, added to the explicit `updates` dict at
  `handoff-config.py:680`.
- Not added to the `("backend","model","effort")` tuple in
  `_invalidate_inherited_verification` — smoke verifies a model/effort pair, not a posture,
  same reasoning `auto_review_spec` already uses. Say that limitation out loud in the schema
  doc.
- `_parse_override` needs no change: it already accepts any `IDENTITY_FIELD_ORDER` name.

### 2. `scripts/delegate-codex.sh` — the mapping

- New `PERMISSION_POSTURE` / `PERMISSION_POSTURE_SOURCE` globals.
- `cmd_submit`: read `permission_mode` from the same `resolve` JSON that already yields
  backend/model/effort. No `--role` → `default`. Source `config:<layer>` or `default`,
  matching the existing convention (which, like `model_source`, names the top participating
  layer rather than per-field provenance).
- Precedence: `--read-only` > `HANDOFF_CLAUDE_PERMISSION_MODE` (claude only, kept, still
  validated before `--read-only` overrides it) > config `permission_mode` > `default`.
- `resolve_claude_permission_mode` / `resolve_copilot_permission_mode` take the posture; add
  `resolve_codex_permission_mode` so codex stops being the branch with no posture.
- `write_run_script`, `write_claude_exec_line`, `write_copilot_exec_line` and both codex
  branches emit per the table.
- `meta` and `--dry-run` gain `permission_posture=` and `permission_posture_source=`
  (`config:<layer>` | `explicit` | `env` | `read-only` | `parent` | `default`). The existing
  `permission_mode=` key keeps its current meaning — the **effective** concrete per-backend
  mode — so an override that changes execution is still visible. Extend the job-layout
  header comment to distinguish the two.
- **`cmd_resume` must not widen authority.** Two fixes, one invariant:
  - inherit `permission_posture` from the parent's `meta`; **absent → `default`**, never
    `allow-all`. A missing key does not prove the parent was unrestricted: a pre-3.7.1 codex
    parent inherited whatever `~/.codex/config.toml` said (possibly `read-only`), a copilot
    parent kept path and URL checks, and a claude parent may have run under a restrictive
    env override.
  - inherit `read_only` from the parent's `meta` when `--read-only` is not passed. Today
    `cmd_resume` initialises `READ_ONLY="false"` (`delegate-codex.sh:518`) and never reads
    the parent, so a fix round on a read-only parent silently becomes writable. Same
    invariant, same function, so it is fixed here rather than left as a known escalation.
- `warn_permission_bypass` stays keyed on the **effective** concrete mode, not the requested
  posture, so `default` + `HANDOFF_CLAUDE_PERMISSION_MODE=bypassPermissions` still warns and
  `allow-all` + `--read-only` does not falsely warn. Extend it to codex `allow-all`. The
  remedy line differs for a role-less job and a resumed job, whose posture has no config row
  to edit.
- Update the `usage()` heredoc.

### 3. `scripts/handoff-setup.py` — the engine

- Re-export `PERMISSION_MODES`; `--role-permission-mode IDENTITY=VALUE` (append), parsed by
  the existing `parse_identity_values`, validated against `PERMISSION_MODES`.
- `choose_identities`: write `permission_mode`, defaulting to `default`. **`PRESETS` stays
  3-tuples** — every preset means `default`.
- `build_plan`: populate `sources[identity]["permission_mode_value"]` alongside the existing
  `*_value` assignments (`handoff-setup.py:655-659`), or `print_plan` cannot show it.
- `print_plan` Selections line and `show_status` line gain `permission=<value>`.
- **The terminal wizard (`handoff-setup.py:1246`) must ask for it too** — the field is
  mandatory in *both* setup UIs, and `references/setup.md:10` names the terminal path as the
  no-browser route. Its custom branch collects backend/model/effort per identity; add a
  fourth prompt defaulting to `default` on empty input.
- Smoke untouched: it already runs `--permission-mode plan` / `--mode plan`.

### 4. `scripts/handoff-setup-ui.py` — the mandatory UI field

- `_preset_matrices` and `build_state`'s `current`: add `permission_mode`, reading
  `values.get("permission_mode", "default")`. State gains `permission_modes` and labels.
- `normalize_payload`: validate per identity against `engine.PERMISSION_MODES`; missing →
  `default`. **Add `permission_mode` to the field tuple at `handoff-setup-ui.py:481-491`** —
  once the normalized identity carries the field and `comparable` does not, every preset-mode
  payload is rejected outright.
- `engine_arguments`: emit `--role-permission-mode` in custom mode.
- Card markup (`renderCards`, ~`handoff-setup-ui.py:1240`): a fifth `<div class="field">`
  with `data-field="permission_mode"`. The generic `bindIdentityInputs` handler already
  writes `matrix[identity][field]`, so no handler change; editing it flips to custom mode
  like every other control. Add the field to `payload()` and the `syncHeroMap` summary.
- CSS: one more track on `.identity { grid-template-columns: … }`
  (`handoff-setup-ui.py:823`). The two responsive rules collapse to `1fr 1fr` and `1fr` and
  need no edit.

### 5. Tests

- `tests/test_handoff_config.py`: valid/invalid values; absent-means-default through
  `resolve`; the global-`allow-all` + project-without-the-field merge case; emitted field
  order; `set --permission-mode` round-trip; `resolve --override`; posture change does not
  clear `verified`.
- `tests/test_delegate_role.py`: **`submit_raw` currently submits role-less
  (`test_delegate_role.py:435`), so a class attribute alone cannot exercise a configured
  posture** — the harness needs a role-configured submit path. Then, per backend: both
  postures' argv; `--read-only` beats config and drops the claude allowlist; a fix round
  inherits posture *and* `read_only` from the parent; a parent with no `permission_posture`
  resumes as `default`; env override still wins on claude and still warns.
  **Update the three tests asserting the old values** — `:189` (codex `""`), `:245` (claude
  `bypassPermissions`), `:287` (copilot `allow-all-tools`); codex and copilot keep theirs,
  claude becomes `dontAsk`.
- **Rewrite `test_argv_never_carries_the_export_or_blanket_permission_flags`
  (`test_delegate_role.py:761`) deliberately.** `--allow-all-tools --allow-all-paths
  --allow-all-urls` is documented by GitHub as exactly equivalent to `--allow-all`/`--yolo`;
  claiming the spelling preserves the old ban would be evasion. The new policy: export flags
  (`--share`, `--share-gist`, `--enable-memory`) and `--worktree` are banned unconditionally;
  blanket permission flags appear only under effective `allow-all` and never under
  `--read-only`. Assert both halves.
- `tests/test_handoff_setup.py`: `--role-permission-mode`, `--status`, plan output, and the
  terminal wizard prompt.
- `tests/test_handoff_setup_ui.py`: payload round-trip, bad value rejected, preset comparison,
  the page wiring the select, and reload-after-apply showing the written value.

### 6. Docs and version (bump together, per `CLAUDE.md`)

- `SKILL.md` frontmatter `version: 3.7.1` and the Configuration paragraph (`SKILL.md:26`);
  `README.md:8` badge; `README.md:227` posture line.
- `docs/specs/design_agent-identities-and-config.md`: field row, example, the field-order sentence (line 92), CLI
  examples, and the note that posture does not invalidate `verified`.
- `references/claude-driven.md:76-78` and `references/setup.md` (both UI contracts).
- `docs/specs/design_agent-handoff.md`: §Phase 2 "The permission posture, stated plainly",
  the §Fail-closed table — the new refusal is the **sixth** condition, not a fifth — and the
  §Risks list for the three stated limitations.
- `test-prompts.json:529` (`cc-worker-permission-bypass`) still demands unconditional claude
  bypass messaging and the env-var remedy; rewrite its `expected_behavior`.
- `docs/user-guide/agent-handoff.html:223` describes the old per-backend containment; update
  it.
- `CHANGELOG.md` `## v3.7.1`; new `docs/releases/v3.7.1.md`.
- `docs/specs/plan_support-cc-to-cc-handoff.md:274` — mark the reversal, do not delete it.
- Receipt stays **v6**: `roles_used` entries are `additionalProperties: false`
  (`docs/receipt-schema.json:149`), so posture evidence stays in job `meta`. Expanding the
  receipt is deliberately out of scope.
- No new required file for `check-skill-repo.sh`.

## Rulings on the review

Folded in: the resume escalation (both halves), the copilot test-policy honesty, the
effective-vs-requested warning distinction, `resolve` materializing the default, the terminal
wizard, `build_plan`'s value population, the `set` updates dict, `test-prompts.json`, the
user guide, the corrected preset-comparison rationale, the sixth-not-fifth correction, the
harness's role-less submit, and the corrected blast radius (copilot does not change).

Ruled against, with reasons:

- **Keep `-s workspace-write` for codex `default`.** Rejected on the probe above: it breaks
  the worker-commit contract and overrides a user config the design doc promises to respect.
  Inheriting is the smaller and more honest change.
- **Add enforced sandboxing to claude `default` in this release.** Rejected as a second
  dimension in one change, against `references/darwin-ratchet.md`. Documented as a named gap.
- **Build codex denial detection now.** Rejected for the same reason; documented as a named
  gap so the flow stops implying the count covers codex.

## Verification

Full CI set, matching `.github/workflows/checks.yml`:

```bash
bash scripts/check-skill-repo.sh .
bash -n scripts/delegate-codex.sh && python3 -m py_compile scripts/handoff-config.py \
  scripts/handoff-setup.py scripts/handoff-setup-ui.py
python3 -m unittest discover -s tests
node --test tests/test_transcript_viewer.mjs
python3 scripts/run-test-prompts.py
SOURCE_DATE_EPOCH=1782921600 python3 scripts/showcase-cost-ledger.py --markdown
git diff --exit-code -- examples/showcase-cost-ledger.json
bash install.sh --dry-run
```

Behavioural probe against the real CLIs, in a scratch git repo. `--dry-run` returns before
`run.sh` is written (`delegate-codex.sh:461`), so the flags are read off a real submit:

```bash
# packet: "Append a line to probe.txt, run `bash check.sh`, then report."
# check.sh exits 0; the packet also asks the worker to read ../outside.txt (must be denied).
for posture in default allow-all; do
  python3 scripts/handoff-config.py --scope project set --role fast_worker --permission-mode $posture
  id=$(bash scripts/delegate-codex.sh submit --repo "$SCRATCH" --prompt-file "$p" --role fast_worker)
  cat "$SCRATCH/.handoff/jobs/$id/run.sh"          # flags match the table
  bash scripts/delegate-codex.sh result "$id" --repo "$SCRATCH"
done
```

Pass criteria — flags alone do not establish these:

- `default`: `probe.txt` changed on disk **and** `check.sh` actually ran (its marker file
  exists), on each of the three backends. This is the claim that `default` is usable, and it
  is the one the review says is unproven by flag spelling.
- `default` on claude: the out-of-scope read is refused and `result` reports
  `permission_denied: 1`.
- `allow-all`: same job succeeds, `permission_denied: 0`.
- Repeat the codex leg once under an `e2e_specifier`-style `--worktree` job that commits, to
  confirm the inherited codex sandbox still permits the commit the contract requires.
- `resume` on a `--read-only` parent without the flag stays read-only; `resume` on a job whose
  `meta` has no `permission_posture` line runs `default`.

Wizard, both surfaces:

```bash
python3 scripts/handoff-setup-ui.py --repo "$SCRATCH"   # field present per identity, preview → apply, reload shows it
python3 scripts/handoff-setup.py --interactive --repo "$SCRATCH"
python3 scripts/handoff-setup.py --status --repo "$SCRATCH"
```
