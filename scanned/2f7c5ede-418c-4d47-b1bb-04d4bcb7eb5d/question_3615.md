# Q3615: NEAR lock_tokens_if_needed burn debits the wrong logical account at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public init/finalize/fast paths` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` violate `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event` in the `burn debits the wrong logical account` attack class because locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
