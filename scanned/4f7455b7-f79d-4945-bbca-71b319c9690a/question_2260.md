# Q2260: NEAR transfer-id/unified-id mixing fast path can pay before canonical parameters are locked via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public fast-transfer and UTXO branches` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path can pay before canonical parameters are locked` under mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
