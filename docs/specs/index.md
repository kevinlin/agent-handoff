# Specification Index

## Design

| File | Topic | Coverage |
|---|---|---|
| [design_agent-handoff.md](design_agent-handoff.md) | Four-phase delegation flow | 5 plans: cc-to-cc, copilot, deep-reasoner, e2e-gauntlet, review-gates |
| [design_agent-handoff-evidence.md](design_agent-handoff-evidence.md) | Session evidence, transcripts, receipts | 3 plans: duration, cost-receipt, transcript-viewer |
| [design_agent-identities-and-config.md](design_agent-identities-and-config.md) | Identities, configuration, setup | 1 plan: permission-mode |
| [design_session-visualisation.md](design_session-visualisation.md) | Session visualisation page | 0 plans |

## Plan

| File | Topic | Upstream design | Status |
|---|---|---|---|
| [plan_add-duration-to-handoff-session-receipt.md](plan_add-duration-to-handoff-session-receipt.md) | Session duration field (v3.1.0) | design_agent-handoff-evidence | implemented |
| [plan_cost-receipt.md](plan_cost-receipt.md) | Cost receipt rendering (v3.6.1) | design_agent-handoff-evidence | implemented |
| [plan_deep-reasoner-spec-review.md](plan_deep-reasoner-spec-review.md) | Optional spec review by deep_reasoner | design_agent-handoff | implemented |
| [plan_e2e-gauntlet-roles.md](plan_e2e-gauntlet-roles.md) | E2E specifier/verifier identities (v3.2.0) | design_agent-handoff | implemented |
| [plan_per-identity-ermission-mode.md](plan_per-identity-ermission-mode.md) | Per-identity permission_mode (v3.7.1) | design_agent-identities-and-config | implemented |
| [plan_rename-partner-skill-to-agent-handoff.md](plan_rename-partner-skill-to-agent-handoff.md) | Rename from partner-skill | — | implemented |
| [plan_review-gates.md](plan_review-gates.md) | Review gates with round caps (v3.8.0) | design_agent-handoff | implemented |
| [plan_support-cc-to-cc-handoff.md](plan_support-cc-to-cc-handoff.md) | Claude Code→Claude Code handoff (v3.5.0) | design_agent-handoff | implemented |
| [plan_support-copilot-cli-backend.md](plan_support-copilot-cli-backend.md) | Copilot as third backend (v3.7.0) | design_agent-handoff | implemented |
| [plan_transcript-viewer.md](plan_transcript-viewer.md) | Transcript viewer (v3.6.0) | design_agent-handoff-evidence | implemented |
