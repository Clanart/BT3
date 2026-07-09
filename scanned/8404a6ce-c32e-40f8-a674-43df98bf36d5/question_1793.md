# Q1793: NEAR transfer-id/unified-id mixing fast path and normal path can both pay through cross-module drift

## Question
Can an unprivileged attacker use `public fast-transfer and UTXO branches` with control over origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId` and desynchronize `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast path and normal path can both pay` attack class because mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` and the adjacent replay-protection bookkeeping after every branch.
