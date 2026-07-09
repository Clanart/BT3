# Q2534: NEAR migrated token map lookup origin inference changes custody branch at boundary values

## Question
Can an unprivileged attacker trigger `public `ft_on_transfer` migration branch plus DAO-created migration state` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` violate `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path` in the `origin inference changes custody branch` attack class because relies on a stored old-token to new-token mapping when users route through the swap branch becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Probe naming-convention inference and cache invalidation around deployed or migrated tokens. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Balance manipulation
- Fast validation: Generate tokens near every naming boundary and assert that origin inference matches the canonical mapping and custody model. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
