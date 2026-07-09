# Q1143: NEAR transfer-id/unified-id mixing replay state keyed too narrowly for the true domain through cross-module drift

## Question
Can an unprivileged attacker use `public fast-transfer and UTXO branches` with control over origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId` and desynchronize `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `replay state keyed too narrowly for the true domain` attack class because mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Look for nonce-only or bucket-only replay keys where the full economic domain includes more fields. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct distinct valid events that share the same nonce-like field and assert that settling one does not block or authorize the other incorrectly. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` and the adjacent replay-protection bookkeeping after every branch.
