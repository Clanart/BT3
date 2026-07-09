# Q1414: Solana init_transfer_sol fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public Solana `init_transfer_sol` instruction` with crafted amount, fee, or native-fee inputs and make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` use inconsistent fee and principal values across handles outbound native-SOL bridging while still posting the same class of Near-bound transfer payload, violating `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
