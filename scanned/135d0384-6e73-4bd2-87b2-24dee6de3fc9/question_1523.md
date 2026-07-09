# Q1523: NEAR send_tokens helper resume-path replay or duplication via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public finalize and fast paths` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::send_tokens` ends up accepting two inconsistent interpretations of the same economic event specifically around `resume-path replay or duplication` under chooses between wNEAR unwrap, bridge-token mint, `ft_transfer`, and `ft_transfer_call` depending on token type and message presence, violating `asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::send_tokens`
- Entrypoint: `internal helper reached from public finalize and fast paths`
- Attacker controls: token id, recipient account, amount, optional message, current gas budget, and whether the token is wNEAR, deployed, or external
- Exploit idea: Abuse yield/resume or asynchronous callback timing so the same pending outbound transfer is restarted after it already progressed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: asset-delivery helper logic must not let attacker-controlled message shape or gas availability change the economic branch in a way that unbacks or strands funds
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Trigger timeouts, duplicate funding, and repeated callback delivery and assert that the resumed transfer either progresses once or cleanly fails once. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
