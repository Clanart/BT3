# Q3005: NEAR transfer-id/unified-id mixing fast-transfer status changes in the wrong order through cross-module drift

## Question
Can an unprivileged attacker use `public fast-transfer and UTXO branches` with control over origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId` and desynchronize `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast-transfer status changes in the wrong order` attack class because mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` and the adjacent replay-protection bookkeeping after every branch.
