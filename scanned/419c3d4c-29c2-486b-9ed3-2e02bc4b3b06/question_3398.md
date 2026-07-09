# Q3398: NEAR UTXO fast resolver fast path can pay before canonical parameters are locked via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO fast path reached through `ft_on_transfer`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path can pay before canonical parameters are locked` under finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
