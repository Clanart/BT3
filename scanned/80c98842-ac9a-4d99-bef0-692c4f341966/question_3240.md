# Q3240: Solana finalize_transfer_sol shared Wormhole nonce can be replayed or gap-filled

## Question
Can an unprivileged attacker drive `public Solana `finalize_transfer_sol` instruction` so that `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol` leaves exploitable gaps or reuse in the shared Wormhole nonce space across message classes, violating `native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol`
- Entrypoint: `public Solana `finalize_transfer_sol` instruction`
- Attacker controls: signed payload bytes, destination nonce, recipient account, payer funding, and SOL-specific account layout
- Exploit idea: Target contracts that reuse one monotonic Wormhole nonce across deploy, init, metadata, and finalize messages.
- Invariant to test: native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Interleave message classes and failures and assert that nonce progression remains globally unique and monotonic for emitted messages.
