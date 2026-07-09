# Q3330: NEAR bind_token entry refund goes to wrong logical owner via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `bind_token` proof-submission flow` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::bind_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `refund goes to wrong logical owner` under verifies a deploy-token proof, writes token mappings in `bind_token_callback`, then refunds leftover deposit in a second callback, violating `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
