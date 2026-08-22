# Darwin Ratchet For Partner

Partner uses a validation-gated improvement loop inspired by Darwin-style skill optimization: evaluate, improve one dimension, test, then keep or revert. The point is not to make the skill longer. The point is to make the next real run more reliable.

## Editable Dimensions

Only improve one dimension per round:

1. Planning handoff
2. The split decision
3. Codex delegation contract
4. Job monitoring
5. Full-review gate
6. Permission policy
7. User-facing final report
8. Install and publish assets

## Keep Criteria

Keep a change only when all are true:

- The changed dimension is named before editing.
- At least one real miniloop or two test prompts support the change.
- The package check passes: `bash scripts/check-skill-repo.sh .`
- The change does not introduce secrets, private paths, or unreproducible dependencies.
- The README remains easier to understand in 10 seconds, not just more complete.

## Revert Criteria

Revert or rework when any are true:

- Test prompts become less specific.
- The workflow grows ambiguous phrases such as "consider", "maybe", "as appropriate", or "根据情况".
- A new rule conflicts with the safety boundary.
- A review or fix round produces no actionable gain after repeated prompts.
- The expected improvement is less than 1 point on a 100-point skill score.

Use a reviewable revert or patch. Do not use `git reset --hard` as the default rollback method.

## Independent Review Rule

For high-risk workflow changes, do not let the same agent be the only maker and only judge. Use one of:

- Claude Code plan, Codex implementation, Claude Code review, Codex verification.
- Codex implementation, Claude Code review, Codex test evidence.
- Two independent review passes with different prompts.

## Dry-Run Budget

Dry runs are allowed, but they are not proof. If more than 30% of validation is dry-run-only, mark the result as provisional and require one real miniloop before public release.
