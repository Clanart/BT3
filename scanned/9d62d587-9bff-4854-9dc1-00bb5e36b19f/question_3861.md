# Q3861: NEAR bind_token entry unregister can sever state that callbacks still need via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `bind_token` proof-submission flow` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::bind_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `unregister can sever state that callbacks still need` under verifies a deploy-token proof, writes token mappings in `bind_token_callback`, then refunds leftover deposit in a second callback, violating `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
