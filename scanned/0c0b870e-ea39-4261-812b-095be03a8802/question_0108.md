# Q108: NEAR migrated token map lookup migration swap leaves old and new claims live

## Question
Can an unprivileged attacker route value through `public `ft_on_transfer` migration branch plus DAO-created migration state` so that `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` burns the old token but still leaves a live claim on the old path while minting the new token, violating `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Target old/new token migration flows that combine bridge burning and replacement minting.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track both token supplies and pending transfer state and assert that migrating one unit cannot leave two redeemable claims.
