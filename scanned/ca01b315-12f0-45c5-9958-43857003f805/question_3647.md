# Q3647: Solana init_transfer_sol native fee and token fee drawn from wrong asset bucket at boundary values

## Question
Can an unprivileged attacker trigger `public Solana `init_transfer_sol` instruction` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` violate `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana` in the `native fee and token fee drawn from wrong asset bucket` attack class because handles outbound native-SOL bridging while still posting the same class of Near-bound transfer payload becomes fragile at those edges?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Focus on branches that mint native-fee tokens, transfer escrowed tokens, or unwrap wrapped native assets. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Balance manipulation
- Fast validation: Trace fee asset origin across every branch and assert that each fee component comes from the asset pool the bridge actually consumed. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
