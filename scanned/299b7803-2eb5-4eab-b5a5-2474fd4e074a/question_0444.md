# Q444: NEAR migrated token map lookup migration swap leaves old and new claims live through cross-module drift

## Question
Can an unprivileged attacker use `public `ft_on_transfer` migration branch plus DAO-created migration state` with control over old token choice, transfer amount, and downstream swap timing and desynchronize `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `migration swap leaves old and new claims live` attack class because relies on a stored old-token to new-token mapping when users route through the swap branch, violating `migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction`
- Entrypoint: `public `ft_on_transfer` migration branch plus DAO-created migration state`
- Attacker controls: old token choice, transfer amount, and downstream swap timing
- Exploit idea: Target old/new token migration flows that combine bridge burning and replacement minting. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: migration lookup must not let a user force a stale or wrong token mapping into an otherwise valid transfer path
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Track both token supplies and pending transfer state and assert that migrating one unit cannot leave two redeemable claims. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::get_migrated_token and migrate flow interaction` and the adjacent token-mapping and asset-identity logic after every branch.
