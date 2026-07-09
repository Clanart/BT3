# Q1924: NEAR UTXO fast resolver fast path and normal path can both pay at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO fast path reached through `ft_on_transfer`` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` violate `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg` in the `fast path and normal path can both pay` attack class because finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
