# Q1439: NEAR migrated token map lookup custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `public `ft_on_transfer` migration branch plus DAO-created migration state` to make `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` increase wrapped supply or reduce custody without the complementary change on the other side, violating `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
