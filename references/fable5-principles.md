# Frontier-Model Prompting Principles (Fable 5 Masterclass distillation)

Shared rules for every agent-to-agent prompt in Handoff. Distilled from Anthropic's Fable 5 (Mythos) Prompting Masterclass; the principles transfer to any frontier agentic model, including the Codex side.

## Why-Forward Context

Frontier models perform on *why*, not just *what*. Open every handoff with:

> I'm working on [the larger task] for [who it's for]. They need
> [what the output enables]. With that in mind: [the actual request].

Never send a bare instruction ("refactor this file") without the purpose.

## Brevity Over Exhaustiveness

Short prompts with a clear goal beat long constraint lists. Over-specifying degrades output — you are constraining a model that would have found the right approach itself. Specify only genuine blockers as constraints. Do not port prompt templates written for older, weaker models.

## Explicit Checkpoints

Autonomous agents define their own checkpoints unless you set them:

> Pause only when the work genuinely requires input: a destructive or
> irreversible action, a real scope change, or something only I can
> provide. Otherwise keep going and report back when done.

Put this rule in the goal file and in every delegation packet.

## Effort as a Handoff Parameter

Effort level is the intelligence/latency/cost dial. Prefer `delegate-codex.sh --role <identity>` so backend, effort, and model resolve from `/agent-handoff config`'s config instead of being picked ad hoc per call; pass an explicit `--effort` only when a specific task genuinely needs to override its identity's default. Without a `--role` or explicit `--effort`, the tool falls back to `high` — reserve `xhigh` for the hardest, quality-critical jobs (expect long runtimes); `medium` only for genuinely trivial mechanical work. The two levels above `xhigh` are Codex-side only and not every model offers them: `max` where correctness outranks runtime, and `ultra` (GPT-5.6) which adds automatic task delegation inside the job, so prefer it only when the job is genuinely one the worker should subdivide itself. What each backend accepts is per-CLI, never one shared enum; `/agent-handoff config` reads the live list. On a subscription plan, do not economize on effort at the price of rework.

## Resume Instead of Restart

Frontier models occasionally stop early. Recovery is one line, sent to the same session:

> Continue end-to-end from [last checkpoint]. Reference: [goal file / job
> log]. Report back when complete.

Use `delegate-codex.sh resume` for this — never restart the task from zero.

## Memory Instruction

When an agent has a place to write lessons (rollout memory, memory dir, mem0), include:

> Store one lesson per note with a one-line summary. Record corrections and
> confirmed approaches alike, including why they mattered. Don't save what
> the repo or chat history already records. Update an existing note rather
> than duplicating it. Delete notes that turn out to be wrong.

## Output Discipline

Dense output keeps the receiving agent's context clean. Every delegation packet ends with:

> DO NOT send optional commentary. Answer only what was asked — no
> preamble, no unsolicited suggestions, no closing remarks. End with at
> most 3 lines of lessons learned.

This is measured, not folklore: a terse reviewer contract cut reviewer output by 41% with no loss in judgment quality (Superpowers 6 autoresearch, 25+ controlled experiments).

## Delegate Execution, Don't Downshift the Planner

Delegation pays twice, and either payoff alone justifies it. It moves execution onto a subscription meter instead of the planner's. And it keeps the driver's context window clear of execution history it will never need again: a background job's transcript stays in its own job directory, so what comes back is a result rather than a thousand lines of tool output. A claude-backed job earns the second payoff even where it moves no meter at all.

Neither payoff is a reason to make the planner do quality-critical work with a cheaper model. Right-sizing is the default lean, never a hard rule: architecture, the split decision itself, cross-module integration, security/correctness paths, and final acceptance stay with the planner even though it is the expensive seat.

One channel carries delegated work; the other two are escape hatches:

| Channel | Shape | Use when |
|---|---|---|
| Handoff job — `delegate-codex.sh --role <identity>` | out-of-process background job on the identity's configured backend, durable state, loop monitoring, resume rework, receipt evidence | a real unit of delegated work: runs while you continue, gets full-reviewed, may need fix rounds |
| One-shot Codex subagent | in-process, blocking, no jobId, no durable state | a stuck step wanting a second diagnosis, or a throwaway assist |
| Raw Task-tool subagent | in-process, isolated context, returns a summary | no Handoff identity fits the work at all |

The Handoff job runs on whichever CLI the identity's `backend` names. Which meter it bills follows from that, and so does whether a given run saves subscription quota — but the job shape, the monitoring loop, the bounded fix round, and the receipt evidence are identical on either backend. A subagent is a single-call primitive; the Handoff job is an orchestration layer (submit, monitor, review, rework, receipt, memory). Pick by whether the work needs that lifecycle, not by which meter you would prefer.

**Never swap an identity to move a job onto another vendor.** If `fast_worker` is claude-backed and you would rather its work ran on Codex, that is a configuration change — `/agent-handoff config`, or `handoff-config.py set --role fast_worker --backend codex` — and you say so. It is not a quiet re-route to `deep_reasoner` because that identity happens to be Codex-backed. The split decision picked an identity for the capability the work needs; re-routing to buy a cheaper meter throws that judgment away and hides the swap in a jobId. `delegate-codex.sh` refuses a `--backend` contradicting a named role for the same reason. When the configured identity is genuinely wrong for the work, say so and ask.

Picking *which* subagent definition to spawn, once you are in one of the escape hatches, is a separate three-level lookup — see "Sub Agent Routing" in `references/claude-driven.md`. It answers which agent definition takes an in-session call, and has nothing to do with the delegated-job path.

## Don't Throttle the Planner's Thinking

Restricting the orchestrator's thinking backfires: in controlled runs it raised turns from 92 to 138 and doubled output — thinking buys turn efficiency (Superpowers 6 autoresearch). Economize on the execution axis (delegate downward), never on the planner's reasoning budget.

Do bound the planner's **execution surface**. Reasoning effort and repository discovery are different dimensions: keep the configured effort, but hand the planner a compact evidence packet rather than letting it rediscover the repo recursively. That preserves high-effort judgment without paying for the same context twice.
