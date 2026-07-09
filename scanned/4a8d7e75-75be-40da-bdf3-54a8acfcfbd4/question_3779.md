# Q3779: Solana init_transfer_sol shared Wormhole nonce can be replayed or gap-filled

## Question
Can an unprivileged attacker drive `public Solana `init_transfer_sol` instruction` so that `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` leaves exploitable gaps or reuse in the shared Wormhole nonce space across message classes, violating `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Target contracts that reuse one monotonic Wormhole nonce across deploy, init, metadata, and finalize messages.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Interleave message classes and failures and assert that nonce progression remains globally unique and monotonic for emitted messages.
