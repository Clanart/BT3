# Q1954: NEAR transfer-id/unified-id mixing fast path and normal path can both pay at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer and UTXO branches` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` violate `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization` in the `fast path and normal path can both pay` attack class because mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
