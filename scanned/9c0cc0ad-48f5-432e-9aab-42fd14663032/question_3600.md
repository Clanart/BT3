# Q3600: NEAR bind_token entry refund goes to wrong logical owner at boundary values

## Question
Can an unprivileged attacker trigger `public `bind_token` proof-submission flow` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::bind_token` violate `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths` in the `refund goes to wrong logical owner` attack class because verifies a deploy-token proof, writes token mappings in `bind_token_callback`, then refunds leftover deposit in a second callback becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Target asynchronous state removal, carried predecessor identities, and stored owner fields. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Trace refund ownership across success and failure branches and assert that only the original funder can recover the reserved storage. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
