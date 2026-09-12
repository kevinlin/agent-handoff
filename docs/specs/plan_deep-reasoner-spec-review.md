# deep_reasoner spec review — an optional cross-agent review of the plan

## Context

Handoff's Phase 1 produces the plan (`.handoff/goal.md`, and under the full protocol a Goal Packet)
and then hands it to the user. The only check on that plan today is the **adversarial gate**, which the
driver runs against itself — one agent is both maker and judge of the plan, which is exactly the failure
`references/darwin-ratchet.md` warns about. The arbiter is not this check: it blind-solves a contested
*answer*, it never reads a plan.

This adds a third instrument: an optional, one-shot, **informed** review of the spec by `deep_reasoner`
on its own configured backend, fired at the moment the workflow hands the plan to the user. The driver
assesses the findings and adjusts the plan; the reviewer never edits anything.

Shape follows the `--with-e2e` precedent exactly: one boolean, default off, absent means off, no new
identity, no schema version bump, no new runtime script. Both dispatch paths already exist
(`delegate-codex.sh --role deep_reasoner --read-only` for a codex backend, the `handoff-deep-reasoner`
subagent for a claude backend).

## Decisions

- **Config field, not a new identity**: `hosts.claude_code.identities.deep_reasoner.auto_review_spec = true`.
  Valid only on `deep_reasoner` — fails closed elsewhere rather than being silently ignored.
  `schema_version` stays `2`; absent means off, so every existing config keeps its current behaviour.
- **Default off**, written only under `handoff-setup.py --spec-review`. The user can always ask for the
  review by hand; the toggle only decides whether it fires on its own.
- **Fires once per run, never automatically again.** The outcome is recorded in a new `## Spec Review`
  block in `.handoff/goal.md`; a non-empty block means the automatic review is spent. The goal file is
  rewritten per run, so the marker resets naturally and a resumed session or a `/loop` tick cannot re-fire it.
- **Sequential, before the plan reaches the user**: review → driver assesses → plan adjusted → plan (or
  Goal Packet) goes to the user carrying the outcome.
- **Not blind, and said so.** The arbiter's contamination rule does not apply — the plan under review *is*
  the driver's answer. Same-vendor weakness gets the same treatment as the arbiter protocol: note it,
  don't hide it.
- **Read-only.** The reviewer returns findings; it never writes the goal file, the spec, or product code.
- No receipt change: `roles_used` already carries `deep_reasoner`, and the review's job directory is
  already counted in `codex_jobs`.

## Tasks

### 1. Design doc (commit before any code)

`docs/specs/design_agent-handoff.md` — new section **"Extension: deep_reasoner spec review"** after
"Flow placement", plus one risk bullet. Content: the config field and why it is not a sixth identity, the
once-per-run rule and where the marker lives, how it differs from the adversarial gate and the arbiter,
the same-vendor caveat, and one line saying this is not part of the Gauntlet borrow — it reuses that
feature's optional-add-on shape only.

### 2. Config engine — `scripts/handoff-config.py`

- `IDENTITY_FIELD_ORDER` gains `auto_review_spec` (appended last, so existing files still emit byte-identically).
- `_validate_data`: must be a boolean, and only on `deep_reasoner`.
- `_parse_override`: boolean branch alongside `verified` (it currently parses everything else as a string).
- `set`: `--spec-review` / `--no-spec-review` mutually exclusive pair, default `None`, folded into the
  existing `updates` dict. It must **not** trip the `identity_changed` verification reset — the toggle
  changes no routing.

Tests in `tests/test_handoff_config.py`: round-trip on `deep_reasoner`, rejection on `fast_worker`,
rejection of a non-boolean, override parses `deep_reasoner.auto_review_spec=true` as a bool, and a
three-identity document without the field still writes back unchanged.
→ verify: `python3 -m unittest tests.test_handoff_config`

### 3. Setup engine — `scripts/handoff-setup.py`

- `build_parser`: `--spec-review` / `--no-spec-review`, `set_defaults(spec_review=False)` — same shape as
  the `--with-e2e` group.
- `choose_identities`: one helper applied before **both** returns (custom mode returns early at ~line 277)
  that sets `identities["deep_reasoner"]["auto_review_spec"] = True` when the flag is on. Flag off → key
  absent → an apply removes a previously written one, which is how the toggle turns off.
- `show_status`: append `spec_review=<true|false>` to the `deep_reasoner` line only. Existing tests assert
  on `"<identity>: backend=..."` prefixes, so a suffix on one line is safe.
- `interactive`: one `[y/N]` prompt next to the existing e2e one.
- `PRESETS` stays untouched — adding a fourth value kind to the preset matrix would break `_preset_matrices`
  and the UI's preset-comparison check for no gain.
- No change needed to `preserve_verification`, `smoke`, `AGENT_TEXT`, or `ROUTING_LINES`: the generated
  `handoff-deep-reasoner` body ("challenge faulty premises, return a concise conclusion with evidence and
  risks") already fits a spec review, and the packet carries the specifics.

Tests in `tests/test_handoff_setup.py`: `--spec-review` writes the field; the default apply omits it; a
second apply without the flag removes it; `--status` reports it.
→ verify: `python3 -m unittest tests.test_handoff_setup`

### 4. Setup UI — `scripts/handoff-setup-ui.py`

- `build_state`: add `"initial_spec_review": bool(identities.get("deep_reasoner", {}).get("auto_review_spec"))`.
- `normalize_payload`: `spec_review = bool(raw.get("spec_review"))`, returned in the payload.
- `engine_arguments`: append `--spec-review` / `--no-spec-review` unconditionally, next to the e2e flag.
- Page: one checkbox near the deep_reasoner card (or its own small block), `change` → `invalidate()`,
  included in `payload()`, seeded from `state.initial_spec_review` in `load()`.

Tests in `tests/test_handoff_setup_ui.py`: payload carries the flag both ways; `engine_arguments` always
states it; state exposes the seed.
→ verify: `python3 -m unittest tests.test_handoff_setup_ui`

> Note, not in scope unless you want it: `withE2e` is never seeded from the existing config either, so
> re-running the wizard silently clears configured e2e identities. Same one-line fix in `load()`. Say the
> word and I'll fold it in.

### 5. Flow prose (product surface — CI gates it)

- `references/claude-driven.md` — Phase 1 gains a final step: **Spec review (optional)**. When
  `auto_review_spec` is true, or the user asks, send the Spec Review Packet to `deep_reasoner` through its
  configured backend (`delegate-codex.sh submit --role deep_reasoner --read-only --label spec-review`, or
  the `handoff-deep-reasoner` subagent when the backend is `claude`), assess the findings, adjust the plan,
  record the outcome in the goal file's `## Spec Review` block, then hand the plan to the user. Fires at
  most once per run; a further review is only on explicit request. States the same-vendor caveat and that
  the reviewer's findings are input to the driver's judgment, not a verdict.
- `references/handoff-template.md` — third packet, **Spec Review Packet**: why-forward context, the spec
  and goal excerpt inline, the acceptance criteria, a one-sentence ask for prioritized findings against
  them, the read-only constraint, and the output rule. Its constraints line forbids editing anything —
  the opposite direction from the e2e packets, and worth saying next to that note.
- `references/goal-template.md` — `## Spec Review` block with its one-line semantics
  (`status: not run | requested | done — <identity/jobId>, <what changed in the plan>`) and the once-only rule.
- `references/setup.md` — the toggle in the single-page UI rules.
- `docs/specs/design_agent-identities-and-config.md` — field row, `deep_reasoner`-only constraint, example, `set` CLI mention, and the
  same forward-compat note the optional identities carry.
- `SKILL.md` (Configuration section + `version: 3.4.0`), `README.md` (badge, identity table row detail,
  what-it-delivers bullet), `CHANGELOG.md` (`## v3.4.0`).
→ verify: `bash scripts/check-skill-repo.sh .`

### 6. Regression prompts — `test-prompts.json`

Three cases: the toggle on fires exactly one review and the driver adjusts the plan; a revised plan does
**not** trigger a second automatic review; the toggle off means the review only runs when the user asks.
`must_not`: repeat the review automatically, let `deep_reasoner` rewrite the plan or the goal file, treat
the review as user authorization, or block the run when the identity is unconfigured.
→ verify: `python3 scripts/run-test-prompts.py`

### 7. User guide — `docs/user-guide/agent-handoff.html`

- Meta line to `v3.4.0` and today's date (it currently reads v3.2.0, one version stale).
- `deep_reasoner` card in the identity grid: the review responsibility, marked opt-in.
- Stage 1: new `<h3>` plus a `.callout claude` mirroring the acceptance-coverage callout — what fires it,
  once only, what the driver does with the findings, what happens when it is off.
- Stage 2: a Spec Review Packet bullet in "The packet contracts", and a row in "What crosses at each
  boundary" (sent: the plan and spec verbatim, the acceptance criteria, the ask for prioritized findings;
  not sent: any authority to edit the plan, the goal file, or the repo).
- Stage 4 "How disagreements are settled": "Two instruments" becomes three — the gate attacks a plan you
  wrote, the spec review has an independent agent read that plan once, the arbiter blind-solves the problem
  again. State plainly that only the arbiter is blind.
- Drift-control table: new row — *"the plan is judged only by the agent that wrote it"* / held by the
  optional cross-agent review recorded in the goal file / enforced by *Convention, once per run*.

### 8. Diagrams — `docs/user-guide/diagrams/*.svg` (all four)

- `phase1-plan-split.svg` — new side card at `x=648, y=1204` (free space beside "Ready to delegate"),
  dashed edge in from the gate node and a dashed return edge for "plan adjusted", in the muted
  `#94a3b8` style used by the other optional side cards. No coordinate shifts. Also fix the caption
  "jobId still empty — nothing has left the driver" → no *implementation* has left the driver.
- `handoff-packet-anatomy.svg` — title "the three named contracts" → four; `viewBox` height `910 → ~1170`;
  a full-width Spec Review Packet card at `y≈810..1050` laid out as three inner columns; move the existing
  "Shared invariant" block (`y=836/858/876`) below it.
- `feedback-loop.svg` — insert a sixth escalation row after "the plan itself" (*a second opinion on the
  plan · optional, once*), shift the rows below by the 46px pitch, `viewBox` height `940 → 986`, move the
  foot line.
- `flow-overview.svg` — Phase 1 band subtitle "nothing delegated yet" → "no implementation delegated yet",
  and the gate card's warn line gains the optional review. Both cards are full at 108px, so no new line and
  no layout shift.

## Verification

```bash
python3 -m unittest discover -s tests
bash scripts/check-skill-repo.sh .
python3 scripts/run-test-prompts.py

# end-to-end config round trip in a scratch repo
python3 scripts/handoff-setup.py --preview --repo <tmp> --mode balanced --spec-review
python3 scripts/handoff-setup.py --apply   --repo <tmp> --mode balanced --spec-review
python3 scripts/handoff-config.py --repo <tmp> resolve | grep auto_review_spec
python3 scripts/handoff-setup.py --status  --repo <tmp>          # spec_review=true on deep_reasoner
python3 scripts/handoff-setup.py --apply   --repo <tmp> --mode balanced   # toggle off removes the field

# fail-closed check
python3 scripts/handoff-config.py --repo <tmp> set --role fast_worker --spec-review   # must error
```

Diagrams and page: `python3 -c "import xml.dom.minidom,glob;[xml.dom.minidom.parse(f) for f in glob.glob('docs/user-guide/diagrams/*.svg')]"`
for well-formedness, then open `docs/user-guide/agent-handoff.html` in a browser and check every figure
renders with no clipped text at the new viewBox sizes.

## Not doing

- No sixth identity, no `PRESETS` row, no receipt schema change, no new script, no new reference file
  (the packet goes in `handoff-template.md`, so `check-skill-repo.sh` and the README File Map stay as they are).
- No automatic re-review loop of any kind. Repetition is the failure mode this design is guarding against.
