# Q2564: NEAR transfer-id/unified-id mixing fast path can pay before canonical parameters are locked at boundary values

## Question
Can an unprivileged attacker trigger `public fast-transfer and UTXO branches` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` violate `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization` in the `fast path can pay before canonical parameters are locked` attack class because mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
