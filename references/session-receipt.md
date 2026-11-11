# Handoff Session Receipt

Every non-trivial run ends with this block. It indexes the run: which jobs belong to it and which CLI executed each. It carries no token or cost figure. Generate it with `make-receipt.py`, never by hand. The generator validates every field, prints nothing when one is invalid, and saves what it validated.

```text
[Handoff session receipt]
phase: <planning | codex implementation | delegated implementation | review | final fix>
claude_session: <sessionId or none>
duration: <99min 12sec>
codex_jobs: <0 | count>
codex_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
cc_jobs: <0 | count>
cc_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
copilot_jobs: <0 | count>
copilot_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
cursor_jobs: <0 | count>
cursor_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
antigravity_jobs: <0 | count>
antigravity_job_durations: <none | jobId=12min 04sec; jobId=3min 41sec>
checks: <commands run or not run>
anomalies: <none | job stalled | job failed | takeback | failed check | arbitration | other>
scope: <project | global | n/a>
config_source: <session | project | global | default | n/a>
roles_used: <none | JSON array of {role, host, model, effort, verified}>
receipt_schema_version: 8
```

## Generating it

```bash
python3 "$HANDOFF_DIR/scripts/make-receipt.py" --repo "$REPO" \
  --phase "<phase>" --checks "<checks>" --anomalies "<anomalies>" \
  --codex-jobs <n> --cc-jobs <n> --copilot-jobs <n> --cursor-jobs <n> --antigravity-jobs <n> \
  --scope <scope> --config-source <source> --roles-used '<JSON array>'
```

What you pass, and where each value comes from:

| Field | Source |
|---|---|
| `phase` | where the run ended. Use `delegated implementation` when the delegated work was not all codex-backed |
| the five job counts (`codex_jobs`, `cc_jobs`, `copilot_jobs`, `cursor_jobs`, `antigravity_jobs`) | this run's `delegate-job.sh` jobs, fix rounds included, split by the `backend=` line in each job's `meta` under `.handoff/jobs/`. Count from those files, not from recall. A job with no `backend=` line is codex. `cc_jobs` is the claude backend. A copilot, cursor or antigravity job is never folded into another count |
| `checks` | the commands you ran, or `not run` |
| `anomalies` | `none`, or every entry under the goal file's `## Anomalies` and `## Arbitration`. A ruling is written `arbitration: <task> approve|reject|no verdict (<jobId>)`, approvals included. The receipt takes one line per field, so join entries with `; ` |
| `scope`, `config_source` | `handoff-config.py resolve` or `handoff-setup.py --status`. Both are `n/a` when the run touched no configured role |
| `roles_used` | one entry per role the run invoked. `role`, `model` and `effort` come from each job's `meta`; `host` is the CLI that executed the role: the job's `backend`, with `claude` written as `claude_code`; `verified` comes from the resolved config. A fix round carries its parent's role and backend. The spec-review job, an arbiter job and the e2e roles are listed like any other. List a role that is unconfigured or unverified with `verified: false`: never guess it true, and never drop it to make the receipt look cleaner |

What the generator measures itself:

- `claude_session`, from `CLAUDE_CODE_SESSION_ID`, which Claude Code sets in every Bash subprocess. Omit `--claude-session`. Pass an id only to regenerate a past run's receipt.
- `duration`, wall clock from `.handoff/session-start` to now, so a run that sat on a permission prompt carries that wait. Phase 0 stamps the marker with `make-receipt.py --start`; without it the generator refuses.
- The five `*_job_durations`, from each job's `submitted_at` and `exit_code`, split by backend. Jobs left from an earlier run are excluded, and one still running reads `running`.
- `receipt_schema_version`, which is always `8`.

The receipt is saved to `.handoff/sessions/<date>_<claude_session>/receipt-<stamp>.md`. `/agent-handoff cost-receipt` and `/agent-handoff visualise` read that file, so a run that only printed the block leaves both with nothing. `--no-save` is for one case: a past run's receipt regenerated with `--ended-at` that should not leave a second file. `validate-receipt.py <file>` re-checks a saved receipt. Regenerate one that fails with the current generator. Never patch it by hand.

## Reporting back

When a run, a setup or a tryout ends, tell the user:

- the phase it ended in, and the Claude Code session id;
- the files changed and the checks run;
- the review findings fixed, and those still open;
- whether the work is ready to commit. Do not commit by default;
- every monitoring anomaly: an idle job, a permission wait, an empty review output, no diff, a failed check;
- the receipt block.

## What the run may claim

Do not fabricate token savings. No counter measures a saving, an avoided cost, or context kept out of the driver. Report what is verifiable: which tasks ran on which backend, how many jobs and fix rounds, that the full diff was reviewed against the acceptance criteria, and that the checks passed. Measured consumption is the cost receipt's job (`references/session-evidence.md`).
