# Q2536: NEAR UTXO fast resolver fee recipient can be substituted or reclaimed by attacker at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO fast path reached through `ft_on_transfer`` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` violate `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg` in the `fee recipient can be substituted or reclaimed by attacker` attack class because finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target optional fee-recipient fields, predecessor-captured identities, and relayer substitution on fast paths. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Settle and claim with varied fee-recipient encodings and assert that only the intended recipient can ever collect that fee. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
