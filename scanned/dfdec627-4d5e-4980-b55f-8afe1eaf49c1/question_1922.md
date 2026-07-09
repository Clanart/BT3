# Q1922: NEAR migrated token map lookup custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `public `ft_on_transfer` migration branch plus DAO-created migration state` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` violate `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path` in the `custody accounting diverges from wrapped supply` attack class because relies on a stored old-token to new-token mapping when users route through the swap branch becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
