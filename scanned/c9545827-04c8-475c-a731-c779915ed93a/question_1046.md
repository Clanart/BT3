# Q1046: NEAR unlock_tokens_if_needed unlock or relock asymmetry through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reached from public finalize paths` with control over token id, chain kind interpreted as origin, and amount and desynchronize `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `unlock or relock asymmetry` attack class because unlocks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed`
- Entrypoint: `internal helper reached from public finalize paths`
- Attacker controls: token id, chain kind interpreted as origin, and amount
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: unlock accounting must release exactly the liquidity that a valid inbound settlement consumes and no more
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Also assert cross-module consistency between `near/omni-bridge/src/token_lock.rs::unlock_tokens_if_needed` and the adjacent lock and unlock accounting after every branch.
