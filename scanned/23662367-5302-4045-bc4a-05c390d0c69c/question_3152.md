# Q3152: NEAR transfer-id/unified-id mixing fast-transfer status changes in the wrong order at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer and UTXO branches` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` violate `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization` in the `fast-transfer status changes in the wrong order` attack class because mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
