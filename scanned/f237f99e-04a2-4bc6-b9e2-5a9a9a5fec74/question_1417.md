# Q1417: Solana UsedNonces::use_nonce stale or reordered proof acceptance

## Question
Can an unprivileged attacker replay an older but still valid proof through `public nonce-tracking path through `finalize_transfer` instructions` and make `solana/programs/bridge_token_factory/src/state/used_nonces.rs::use_nonce` treat it as fresh because of tracks nonce usage in bit arrays, updates `max_used_nonce`, and compensates or charges rent to the authority reserve as nonce ranges expand, violating `nonce tracking must reject every replay while keeping reserve accounting exact, even under gaps, huge nonces, or interleaved PDA initialization`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/used_nonces.rs::use_nonce`
- Entrypoint: `public nonce-tracking path through `finalize_transfer` instructions`
- Attacker controls: destination nonce, used-nonces PDA slot, payer funding, authority PDA lamports, and nonce gaps
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event.
- Invariant to test: nonce tracking must reject every replay while keeping reserve accounting exact, even under gaps, huge nonces, or interleaved PDA initialization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state.
