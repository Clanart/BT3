# Q3905: Solana init_transfer_sol shared Wormhole nonce can be replayed or gap-filled via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public Solana `init_transfer_sol` instruction` and then replay or reorder the later settlement leg on another chain so that `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol` ends up accepting two inconsistent interpretations of the same economic event specifically around `shared Wormhole nonce can be replayed or gap-filled` under handles outbound native-SOL bridging while still posting the same class of Near-bound transfer payload, violating `native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::init_transfer_sol`
- Entrypoint: `public Solana `init_transfer_sol` instruction`
- Attacker controls: payer lamports, recipient string, amount, fee, native fee, and message
- Exploit idea: Target contracts that reuse one monotonic Wormhole nonce across deploy, init, metadata, and finalize messages. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: native-SOL outbound flows must not emit claims whose amount or fee exceeds the value actually escrowing or burning on Solana
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Interleave message classes and failures and assert that nonce progression remains globally unique and monotonic for emitted messages. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
