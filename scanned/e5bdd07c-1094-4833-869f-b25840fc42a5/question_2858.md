# Q2858: NEAR transfer-id/unified-id mixing fast-transfer status changes in the wrong order via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public fast-transfer and UTXO branches` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast-transfer status changes in the wrong order` under mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
