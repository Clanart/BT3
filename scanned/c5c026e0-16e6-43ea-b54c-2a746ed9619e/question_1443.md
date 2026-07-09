# Q1443: Solana finalize_transfer nonce bitmap stale or reordered proof acceptance

## Question
Can an unprivileged attacker replay an older but still valid proof through `public inbound Solana flow through `finalize_transfer` and `finalize_transfer_sol`` and make `solana/programs/bridge_token_factory/src/state/used_nonces.rs plus finalize instruction` treat it as fresh because of buckets destination nonces into multiple PDAs and compensates rent as the highest observed nonce advances, violating `cross-PDA nonce accounting must not let a replay land in an uninitialized bucket or refund reserve lamports while keeping the nonce marked unused`?

## Target
- File/function: `solana/programs/bridge_token_factory/src/state/used_nonces.rs plus finalize instruction`
- Entrypoint: `public inbound Solana flow through `finalize_transfer` and `finalize_transfer_sol``
- Attacker controls: destination nonce, PDA bucket index, payer rent, and initialization order
- Exploit idea: Focus on receipt ids, VAA sequence use, block-hash freshness, and whether replay state keys the exact economic event.
- Invariant to test: cross-PDA nonce accounting must not let a replay land in an uninitialized bucket or refund reserve lamports while keeping the nonce marked unused
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Submit old proofs after later events and assert that replay protection and freshness checks reject them without stranding legitimate state.
