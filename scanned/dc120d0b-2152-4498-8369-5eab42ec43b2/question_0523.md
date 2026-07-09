# Q523: NEAR bind_token entry canonical token identity collision at boundary values

## Question
Can an unprivileged attacker trigger `public `bind_token` proof-submission flow` with boundary-controlled inputs covering minimal deposits, maximal storage payloads, and withdrawal edges and make `near/omni-bridge/src/lib.rs::bind_token` violate `binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths` in the `canonical token identity collision` attack class because verifies a deploy-token proof, writes token mappings in `bind_token_callback`, then refunds leftover deposit in a second callback becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::bind_token`
- Entrypoint: `public `bind_token` proof-submission flow`
- Attacker controls: proof bytes, source chain selection, attached deposit, and timing versus token deployment
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Concentrate on minimal deposits, maximal storage payloads, and withdrawal edges.
- Invariant to test: binding an existing Near token to a foreign asset must remain one-to-one and fully collateral-accounted across proof replay, partial failure, and refund paths
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Sweep boundary values for minimal deposits, maximal storage payloads, and withdrawal edges and assert that the same invariant holds at every edge.
