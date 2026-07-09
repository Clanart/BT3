# Q3668: NEAR UTXO fast resolver fast path can pay before canonical parameters are locked at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO fast path reached through `ft_on_transfer`` with boundary-controlled inputs covering zero-fee, fee-equals-amount, and near-overflow amount splits and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` violate `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg` in the `fast path can pay before canonical parameters are locked` attack class because finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg. Concentrate on zero-fee, fee-equals-amount, and near-overflow amount splits.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement. Sweep boundary values for zero-fee, fee-equals-amount, and near-overflow amount splits and assert that the same invariant holds at every edge.
