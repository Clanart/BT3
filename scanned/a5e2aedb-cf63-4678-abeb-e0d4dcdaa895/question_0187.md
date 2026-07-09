# Q187: NEAR bind_token entry canonical token identity collision via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `bind_token` proof-submission flow` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::bind_token` ends up accepting two inconsistent interpretations of the same economic event specifically around `canonical token identity collision` under verifies a deploy-token proof, writes token mappings in `bind_token_callback`, then refunds leftover deposit in a second callback, violating `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
