# Q3987: NEAR bind_token entry unregister can sever state that callbacks still need through cross-module drift

## Question
Can an unprivileged attacker use `public `bind_token` proof-submission flow` with control over proof bytes, source chain selection, attached deposit, and timing versus token deployment and desynchronize `near/omni-bridge/src/lib.rs::bind_token` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `unregister can sever state that callbacks still need` attack class because verifies a deploy-token proof, writes token mappings in `bind_token_callback`, then refunds leftover deposit in a second callback, violating `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::bind_token` and the adjacent token-mapping and asset-identity logic after every branch.
