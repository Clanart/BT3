# Q3263: NEAR UTXO fast resolver fast path can pay before canonical parameters are locked

## Question
Can an unprivileged attacker use `public UTXO fast path reached through `ft_on_transfer`` to make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` release a fast-transfer payout before the canonical transfer parameters are irreversibly fixed, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement.
