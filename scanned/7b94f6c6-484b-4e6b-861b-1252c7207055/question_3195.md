# Q3195: NEAR bind_token entry refund goes to wrong logical owner

## Question
Can an unprivileged attacker exploit callbacks behind `public `bind_token` proof-submission flow` so that `near/omni-bridge/src/lib.rs::bind_token` refunds storage to an account other than the one that actually funded the state because of verifies a deploy-token proof, writes token mappings in `bind_token_callback`, then refunds leftover deposit in a second callback, violating `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage.
