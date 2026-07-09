# Q2471: NEAR lock_tokens_if_needed unlock or relock asymmetry at boundary values

## Question
Can an unprivileged attacker trigger `internal helper reached from public init/finalize/fast paths` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` violate `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event` in the `unlock or relock asymmetry` attack class because locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
