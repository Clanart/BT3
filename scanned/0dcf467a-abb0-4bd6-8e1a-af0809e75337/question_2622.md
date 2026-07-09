# Q2622: NEAR lock_tokens_if_needed one inbound event spawns multiple outbound obligations

## Question
Can an unprivileged attacker settle through `internal helper reached from public init/finalize/fast paths` and make `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` both release local value and create a second valid outbound bridge obligation via locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims.
