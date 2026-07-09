# Q3063: NEAR lock_tokens_if_needed one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public init/finalize/fast paths` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` violate `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event` in the `one inbound event spawns multiple outbound obligations` attack class because locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
