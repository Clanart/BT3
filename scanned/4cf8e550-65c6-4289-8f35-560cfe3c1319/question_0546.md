# Q546: NEAR lock_tokens_if_needed burn or lock before irreversible state at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public init/finalize/fast paths` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` violate `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event` in the `burn or lock before irreversible state` attack class because locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
