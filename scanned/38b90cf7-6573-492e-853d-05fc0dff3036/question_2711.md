# Q2711: NEAR transfer-id/unified-id mixing fast-transfer status changes in the wrong order

## Question
Can an unprivileged attacker trigger `public fast-transfer and UTXO branches` so that `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` marks, removes, or reuses fast-transfer state in an order that opens replay or fee-claim gaps, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle.
