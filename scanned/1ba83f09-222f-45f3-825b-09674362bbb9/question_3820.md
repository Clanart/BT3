# Q3820: NEAR transfer-id/unified-id mixing UTXO and nonce-based ids can be mixed up

## Question
Can an unprivileged attacker use `public fast-transfer and UTXO branches` so that `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` treats a UTXO-origin transfer as equivalent to a nonce-based transfer or vice versa, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target `UnifiedTransferId` handling and any branch that converts between transfer-id kinds.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct transfers that share neighboring fields across id kinds and assert that replay protection keeps the domains disjoint.
