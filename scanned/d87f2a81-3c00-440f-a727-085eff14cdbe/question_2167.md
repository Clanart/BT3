# Q2167: NEAR lock_tokens_if_needed unlock or relock asymmetry via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public init/finalize/fast paths` and then replay or reorder the complementary outbound or inbound bridge leg so that `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` ends up accepting two inconsistent interpretations of the same economic event specifically around `unlock or relock asymmetry` under locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Look for one branch that unlocks origin liquidity while another branch also mints or stores a second claim. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model successful and failed delivery plus fast-transfer branches and assert that aggregate locked liquidity matches outstanding claims after each path. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
