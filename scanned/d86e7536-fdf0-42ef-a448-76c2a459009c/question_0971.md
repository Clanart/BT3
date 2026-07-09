# Q971: Solana SOL vault branch replay guard can be bypassed or consumed incorrectly via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana native-SOL bridge instructions` and then replay or reorder the complementary outbound or inbound bridge leg so that `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs` ends up accepting two inconsistent interpretations of the same economic event specifically around `replay guard can be bypassed or consumed incorrectly` under moves SOL through a dedicated vault PDA and signs/consumes payloads that are similar to token-based flows but with different account models, violating `native-SOL paths must remain perfectly coupled to their payloads so the same lamports cannot back multiple bridge events or be withdrawn under a different nonce`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/instructions/user/init_transfer_sol.rs and finalize_transfer_sol.rs`
- Entrypoint: `public Solana native-SOL bridge instructions`
- Attacker controls: payer lamports, vault balances, signed payload, destination nonce, and fee fields
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: native-SOL paths must remain perfectly coupled to their payloads so the same lamports cannot back multiple bridge events or be withdrawn under a different nonce
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
