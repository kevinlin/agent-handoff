# Specification Index

## Design

| File | Topic | Coverage |
|---|---|---|
| [design_agent-handoff.md](design_agent-handoff.md) | Four-phase delegation flow | 5 plans: cc-to-cc, copilot, deep-reasoner, e2e-gauntlet, review-gates |
| [design_agent-handoff-evidence.md](design_agent-handoff-evidence.md) | Session evidence, transcripts, receipts | 3 plans: duration, cost-receipt, transcript-viewer |
| [design_agent-identities-and-config.md](design_agent-identities-and-config.md) | Identities, configuration, setup | 1 plan: permission-mode |
| [design_session-visualisation.md](design_session-visualisation.md) | Session visualisation page | 0 plans |

## Plan

| File | Topic | Upstream design | Version | Status |
|---|---|---|---|---|
| [plan_rename-partner-skill-to-agent-handoff.md](plan_rename-partner-skill-to-agent-handoff.md) | Rename from partner-skill | — | v3.0.0 | implemented |
| [plan_add-duration-to-handoff-session-receipt.md](plan_add-duration-to-handoff-session-receipt.md) | Session duration field | design_agent-handoff-evidence | v3.1.0 | implemented |
| [plan_e2e-gauntlet-roles.md](plan_e2e-gauntlet-roles.md) | E2E specifier/verifier identities | design_agent-handoff | v3.2.0 | implemented |
| [plan_deep-reasoner-spec-review.md](plan_deep-reasoner-spec-review.md) | Optional spec review by deep_reasoner | design_agent-handoff | v3.4.0 | implemented |
| [plan_support-cc-to-cc-handoff.md](plan_support-cc-to-cc-handoff.md) | Claude Code→Claude Code handoff | design_agent-handoff | v3.5.0 | implemented |
| [plan_transcript-viewer.md](plan_transcript-viewer.md) | Transcript viewer | design_agent-handoff-evidence | v3.6.0 | implemented |
| [plan_cost-receipt.md](plan_cost-receipt.md) | Cost receipt rendering | design_agent-handoff-evidence | v3.6.1 | implemented |
| [plan_support-copilot-cli-backend.md](plan_support-copilot-cli-backend.md) | Copilot as third backend | design_agent-handoff | v3.7.0 | implemented |
| [plan_per-identity-ermission-mode.md](plan_per-identity-ermission-mode.md) | Per-identity permission_mode | design_agent-identities-and-config | v3.7.1 | implemented |
| [plan_review-gates.md](plan_review-gates.md) | Review gates with round caps | design_agent-handoff | v3.8.0 | implemented |
