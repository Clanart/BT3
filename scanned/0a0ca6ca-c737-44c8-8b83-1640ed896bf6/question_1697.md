# Q1697: NEAR unlock_tokens_if_needed custody accounting diverges from wrapped supply through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reached from public finalize paths` with control over token id, chain kind interpreted as origin, and amount and desynchronize `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `custody accounting diverges from wrapped supply` attack class because unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Also assert cross-module consistency between `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` and the adjacent lock and unlock accounting after every branch.
