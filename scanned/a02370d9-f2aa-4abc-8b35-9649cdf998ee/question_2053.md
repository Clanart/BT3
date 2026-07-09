# Q2053: Solana finalize_transfer_sol stale or reordered proof acceptance

## Question
Can an unprivileged attacker replay an older but still valid proof through `public Solana `finalize_transfer_sol` instruction` and make `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol` treat it as fresh because of verifies the NEAR-derived signature for a SOL transfer, uses nonce tracking, and processes native-SOL finalization, violating `native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/lib.rs::finalize_transfer_sol`
- Entrypoint: `public Solana `finalize_transfer_sol` instruction`
- Attacker controls: signed payload bytes, destination nonce, recipient account, payer funding, and SOL-specific account layout
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event.
- Invariant to test: native-SOL settlement must not be replayable or capable of releasing value under the wrong domain, amount, or recipient binding
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state.
