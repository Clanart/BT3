# Q998: NEAR callback gas budgeting resume-path replay or duplication via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public finalization and fast-transfer flows with user-controlled `msg`` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers` ends up accepting two inconsistent interpretations of the same economic event specifically around `resume-path replay or duplication` under computes `ft_transfer_call` gas from prepaid minus used gas and falls back to strict minimum checks before minting or transferring, violating `callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens gas splitting plus callback consumers`
- Entrypoint: `public finalization and fast-transfer flows with user-controlled `msg``
- Attacker controls: message length, gas left at call time, and whether the path chooses `ft_transfer` or `ft_transfer_call`
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: callback gas budgeting must not let attacker-controlled message size or branching force a partial economic effect before the contract can safely resolve the outcome
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
