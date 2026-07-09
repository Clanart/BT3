# Q1279: NEAR UTXO fast resolver final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO fast path reached through `ft_on_transfer`` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` violate `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg` in the `final settlement and later fee claim can diverge` attack class because finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
