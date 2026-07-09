# Q1575: Solana init_transfer_sol fee and principal split divergence via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana `init_transfer_sol` instruction` and then replay or reorder the later settlement leg on another chain so that `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` ends up accepting two inconsistent interpretations of the same economic event specifically around `fee and principal split divergence` under handles outbound native-SOL bridging while still posting the same class of Near-bound transfer payload, violating `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
