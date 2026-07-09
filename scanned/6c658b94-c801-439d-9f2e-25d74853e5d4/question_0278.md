# Q278: NEAR UTXO fast resolver recipient or fee-recipient rebinding via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO fast path reached through `ft_on_transfer`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or fee-recipient rebinding` under finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
