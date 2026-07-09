# Q2682: NEAR migrated token map lookup global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `public `ft_on_transfer` migration branch plus DAO-created migration state` with the code paths summarized by `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by relies on a stored old-token to new-token mapping when users route through the swap branch, violating `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
