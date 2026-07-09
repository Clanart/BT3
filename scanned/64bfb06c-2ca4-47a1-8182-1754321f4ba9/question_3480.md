# Q3480: NEAR lock_tokens_if_needed burn debits the wrong logical account through cross-module drift

## Question
Can an unprivileged attacker use `internal helper reached from public init/finalize/fast paths` with control over token id, chain kind interpreted as destination, and amount and desynchronize `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` from the adjacent lock and unlock accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn debits the wrong logical account` attack class because locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Focus on drift between this module and the adjacent lock and unlock accounting.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Also assert cross-module consistency between `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` and the adjacent lock and unlock accounting after every branch.
