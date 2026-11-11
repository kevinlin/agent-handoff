# Backend: antigravity

- **Preflight:** `agy --help`, and read it: only a help text that lists both `--conversation` and `--dangerously-skip-permissions` identifies the CLI. `agy --version` prints a bare number, an IDE launcher has been linked as `agy` before, and `antigravity` is always the IDE launcher.
- **Model and effort:** the model is a whole `agy` catalogue id that carries its own effort, so the effort is always `model`. An empty model and `auto` are both refused: a resume passes the model again, and an unpinned run would use whatever model the CLI has selected at that moment.
- **Posture:** `default` and `allow-all` are the same here. Both skip every permission check, Handoff knows of no rule that narrows it, and every writing job prints the bypass warning. Handoff writes nothing under `~/.gemini/`.
- **Read-only:** plan mode, never with the skip flag. Plan mode refuses every shell command, `ls` and `git diff` included, and the job's turn ends at the first refused action. A read-only job can therefore only read files: inline what it must see (the diff, for a review) and state check results instead of asking it to run them.
- **After a read-only job, check the tree** with `git status --short`. An allow rule in the user's `agy` settings might let a write through. If the tree changed, reject the job's output, save the changes to `.handoff/rejected/<task-id>.patch`, return those paths to their state before the job, and record the anomaly `read-only write`.
- **Denials** are reported only on the final `result` event, and a run stopped by a denial still exits 0 with status `SUCCESS`, so the count is the only signal. `permission_denied: unknown` (`null` in `result --json`) means the finished job's log has no `result` event. Unknown is not zero: re-run the checks yourself.
- **Checks:** `agy` logs no exit code for a shell command, so `commands_run` lists commands without results. Confirm a worker's report that a check passed by re-running it.

Failures:

- A model rejected at launch gives `FAILED` with one `result` event of status `ERROR` and no session id. The message and the available models are in `log.jsonl` and `stderr.log`, and `result` shows it as a `result_error`. `resume` refuses that job. A fix round whose model has left the catalogue since its parent ran fails the same way, and `resume` never substitutes a model. Recover with `handoff-config.py set` naming a model the catalogue still offers, then a fresh `submit` with the full packet.
- `context_lost: true` on a fix round means `agy` could not find the conversation and started a new one, so the round ran without its parent's context. Resubmit with the full packet instead of resuming again.
- A `print_timeout` entry under `errors` in `result --json` means the output is partial, even with exit 0 and `SUCCESS`. Treat the job as failed, record the anomaly, and resubmit.
