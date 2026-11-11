# Skill change gate

Use this gate when improving the Handoff workflow itself, or when applying it to a substantial task. The loop is: evaluate, change one dimension, test, then keep or revert. The aim is a more reliable next run, not a longer skill.

## One dimension per round

1. Planning
2. The split decision
3. The delegation contract
4. Job monitoring
5. The full-review gate
6. Permission policy
7. The user-facing final report
8. Install and publish assets

## Keep a change only when all of these hold

- The dimension was named before editing.
- At least one real miniloop or two test prompts support the change.
- The package check passes: `bash scripts/check-skill-repo.sh .`
- It introduces no secrets, private paths or unreproducible dependencies.
- The README is still easy to grasp in 10 seconds, not only more complete.

## Revert or rework when any of these holds

- Test prompts became less specific.
- The workflow gained vague phrases such as "consider", "maybe", "as appropriate" or "it depends".
- A new rule conflicts with a safety boundary.
- Another review or fix round produces no actionable gain.

Use a reviewable revert or a patch. Do not use a hard Git reset.

Stop when another prompt loop produces low signal: no actionable gain, or an improvement too small to show in a test prompt or a real run.

## The maker is not the only judge

On a high-risk workflow change, the agent that made the change must not be the only one that judges it. Use one of:

- one agent plans and reviews, a second implements and verifies;
- two independent review passes with different prompts.

## Dry runs

A dry run is not proof. When more than 30% of the validation is dry-run-only, mark the result provisional and require one real miniloop before a public release.
