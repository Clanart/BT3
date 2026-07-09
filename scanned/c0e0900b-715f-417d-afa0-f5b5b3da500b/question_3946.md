# Q3946: NEAR transfer-id/unified-id mixing UTXO and nonce-based ids can be mixed up via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public fast-transfer and UTXO branches` and then replay or reorder matching fast-transfer completion or fee-claim leg so that `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised`` ends up accepting two inconsistent interpretations of the same economic event specifically around `UTXO and nonce-based ids can be mixed up` under mixes plain nonce-based `TransferId` state with UTXO-based `UnifiedTransferId` state depending on branch, violating `replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization`?

## Target
- File/function: `near/omni-bridge/src/storage.rs and `is_unified_transfer_finalised``
- Entrypoint: `public fast-transfer and UTXO branches`
- Attacker controls: origin chain, origin nonce, UTXO ids, and the kind tag inside `UnifiedTransferId`
- Exploit idea: Target `UnifiedTransferId` handling and any branch that converts between transfer-id kinds. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: replay protection must never treat two distinct transfer-id kinds as equivalent or let an attacker pivot between them to bypass finalization
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Construct transfers that share neighboring fields across id kinds and assert that replay protection keeps the domains disjoint. Then replay or reorder matching fast-transfer completion or fee-claim leg and assert that the bridge still exposes only one valid economic outcome.
