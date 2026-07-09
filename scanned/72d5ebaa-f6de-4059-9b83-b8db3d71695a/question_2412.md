# Q2412: NEAR transfer-id/unified-id mixing fast path can pay before canonical parameters are locked through cross-module drift

## Question
Can an unprivileged attacker use `public fast-transfer and UTXO branches` with control over origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId` and desynchronize `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast path can pay before canonical parameters are locked` attack class because mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement. Also assert cross-module consistency between `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` and the adjacent replay-protection bookkeeping after every branch.
