# Q879: NEAR lock_tokens_if_needed native versus wrapped branch switch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `internal helper reached from public init/finalize/fast paths` and then replay or reorder the complementary outbound or inbound bridge leg so that `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed` ends up accepting two inconsistent interpretations of the same economic event specifically around `native versus wrapped branch switch` under locks bridge liquidity only when the token origin chain differs from the chosen chain and amount is nonzero, violating `lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event`?

## Target
- File/function: `near/omni-bridge/src/token_lock.rs::lock_tokens_if_needed`
- Entrypoint: `internal helper reached from public init/finalize/fast paths`
- Attacker controls: token id, chain kind interpreted as destination, and amount
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: lock accounting must not skip a real collateral obligation or lock the wrong asset/chain tuple for one cross-chain event
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
