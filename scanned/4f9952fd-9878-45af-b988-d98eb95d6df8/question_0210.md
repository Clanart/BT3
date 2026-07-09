# Q210: NEAR lock_tokens_if_needed burn or lock before irreversible state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public init/finalize/fast paths` and then replay or reorder the complementary outbound or inbound bridge leg so that `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn or lock before irreversible state` under locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
