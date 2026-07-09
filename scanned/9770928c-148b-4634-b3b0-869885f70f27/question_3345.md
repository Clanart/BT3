# Q3345: NEAR lock_tokens_if_needed burn debits the wrong logical account via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public init/finalize/fast paths` and then replay or reorder the complementary outbound or inbound bridge leg so that `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn debits the wrong logical account` under locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
