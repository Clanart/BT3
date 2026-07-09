# Q3735: NEAR bind_token entry unregister can sever state that callbacks still need

## Question
Can an unprivileged attacker combine `public `bind_token` proof-submission flow` with later callbacks so that `near/omni-bridge/src/lib.rs::bind_token` unregisters storage ownership before asynchronous cleanup runs, violating `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Target NEAR-style storage-management calls interleaved with yield/resume or token-delivery callbacks.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Unregister between async stages and assert that later cleanup still finds or preserves enough state to finish safely.
