# Q2108: NEAR transfer-id/unified-id mixing fast path can pay before canonical parameters are locked

## Question
Can an unprivileged attacker use `public fast-transfer and UTXO branches` to make `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` release a fast-transfer payout before the canonical transfer parameters are irreversibly fixed, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement.
