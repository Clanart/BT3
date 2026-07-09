# Q945: NEAR migrated token map lookup asset mapping drifts away from actual token semantics via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public `ft_on_transfer` migration branch plus DAO-created migration state` and then replay or reorder the adjacent bridge step that consumes the same state so that `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` ends up accepting two inconsistent interpretations of the same economic event specifically around `asset mapping drifts away from actual token semantics` under relies on a stored old-token to new-token mapping when users route through the swap branch, violating `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation. Then replay or reorder the adjacent bridge step that consumes the same state and assert that the bridge still exposes only one valid economic outcome.
