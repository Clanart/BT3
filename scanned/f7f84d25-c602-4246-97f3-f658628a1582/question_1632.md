# Q1632: NEAR transfer-id/unified-id mixing fast path and normal path can both pay via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public fast-transfer and UTXO branches` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path and normal path can both pay` under mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
