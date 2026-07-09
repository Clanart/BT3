# Q779: NEAR migrated token map lookup asset mapping drifts away from actual token semantics

## Question
Can an unprivileged attacker exploit `public `ft_on_transfer` migration branch plus DAO-created migration state` so that `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` keeps a token mapped as canonical after its actual runtime semantics or backing assumptions diverge, violating `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation.
