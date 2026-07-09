# Q3692: NEAR transfer-id/unified-id mixing relayer substitution changes economic recipient at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer and UTXO branches` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` violate `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization` in the `relayer substitution changes economic recipient` attack class because mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target branches where a stored fast-transfer status replaces the canonical recipient or fee recipient. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Verify that relayer substitution happens only for the exact matching transfer id and exact matching parameters of the relayed fast payout. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
