# Artefact rows

A row is an artefact row when its result is not a text diff (a build, a render, a clip, a voice track), when one of its commands outlasts a monitor tick, or when it calls a paid service. These rules apply to the row whatever identity runs it. They add to the flow and replace only the steps named here.

## Identity

Capability decides first. The acceptance gate is `e2e_tester` and an ambiguous diagnosis is `deep_reasoner`, however long they run. A row whose work is running tools goes to `operator`. When `operator` is not configured, say so once ("`/agent-handoff config --with-operator` would give tool-running rows their own identity") and give the row to `fast_worker`.

## The packet

Use the Delegation Packet and add:

- **Outputs.** Name an output directory that does not exist yet, and check that before you submit. The worker writes only there and under `.handoff/evidence/<task-id>/`, which must also be new. It never overwrites an existing file. Replacing an existing deliverable is your step, after acceptance.
- **Evidence.** Name what the worker must leave in the evidence directory: a probe of each output (`ffprobe` JSON, a frame count, a checksum, a build artefact's path), the exit status of each long command, and keyframes or a contact sheet for anything visual.
- **Long commands.** A command expected to outlast a tick starts in the background. Its output goes to a log file, and its exit status to a file when it ends. The worker checks on it at least every two minutes and reports the elapsed time. The check is the process, or the exit-status file where a sandbox denies a process probe. State an expected duration and a runtime cap. At the cap the worker stops the command and reports.
- **Network.** Say whether the row needs it. Where the backend's `default` posture denies it, the identity that runs the row needs `permission_mode = allow-all`. That is a config change: name it and offer it. Configuring `operator` keeps that posture off `fast_worker`.

A worker inherits your environment, API keys included. A claude-backed identity is the exception: it runs without `ANTHROPIC_*` and `CLAUDE_CODE_*`, so a row that needs one of those cannot run there.

## Paid calls

No paid call is made until the user has agreed to it.

1. Before Phase 2, list each paid service, what it is called for, and a total cap in calls or in money. Record the user's go-ahead in the goal file's `## Spend` block, in their words.
2. You own the cap. Give each paid row its own allocation in that block before you submit it. The allocations never exceed the total, and a fix round gets what its row has left.
3. In the packet, replace the "touch no secrets" line. Name the one environment variable to read, the service it may be sent to, the files that may be uploaded, and the row's allocation. Every other secret and every `.env` file stay off limits. The key is never printed or written to a file.
4. The worker appends a line to `.handoff/evidence/<task-id>/spend.jsonl` before each call, with the service and the purpose. After the call it adds the provider's request or job id and the outcome.
5. A call with no recorded outcome is unknown. The worker stops spending and reports it. Settle it against the provider's own record before any further paid call. Read that file before you resubmit or resume a paid row that failed or stalled.

## Monitoring

Phase 3 holds unchanged. The worker's two-minute checks are events in the job's own log, so a healthy long command never looks stalled.

## Review

For the row's outputs, in place of the diff review:

- Read every probe and exit-status file against the row's acceptance.
- Open the keyframes or the contact sheet yourself. Never accept on the worker's description of an image.
- Compare `spend.jsonl` with the row's allocation.
- What no model can judge (audio, motion feel, taste) is the user's call. Say what to open, and keep the row in `review` until they answer. Record the answer in Notes. An arbiter's approval never stands in for it.

Any text the row also changed still gets the full diff review.

When a row is rejected, move its output directory to `.handoff/rejected/<task-id>/`. An effect outside the machine (an upload, a publish, money spent) cannot be undone, so report it.

## Without git

Plain rows run in a folder that is not a git repository. In place of `git status`, record a file listing with sizes and modification times before and after each job. The e2e gate still needs git.
