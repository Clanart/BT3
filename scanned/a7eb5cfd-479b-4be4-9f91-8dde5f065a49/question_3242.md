# Q3242: Solana init_transfer_sol native fee and token fee drawn from wrong asset bucket

## Question
Can an unprivileged attacker use `public Solana `init_transfer_sol` instruction` to make `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` pay native fee and token fee from inconsistent custody pools because of handles outbound native-SOL bridging while still posting the same class of Near-bound transfer payload, violating `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Focus on branches that mint native-fee tokens, transfer escrowed tokens, or unwrap wrapped native assets.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Balance manipulation
- Fast validation: Trace fee asset origin across every branch and assert that each fee component comes from the asset pool the bridge actually consumed.
