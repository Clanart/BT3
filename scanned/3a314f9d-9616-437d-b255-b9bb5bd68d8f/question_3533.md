# Q3533: NEAR UTXO fast resolver fast path can pay before canonical parameters are locked through cross-module drift

## Question
Can an unprivileged attacker use `public UTXO fast path reached through `ft_on_transfer`` with control over fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient and desynchronize `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `fast path can pay before canonical parameters are locked` attack class because finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target relayer-funded near-term payouts that rely on later proofs to confirm the first leg. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare fast-payout parameters to the later proof and assert that mismatched proofs cannot still unlock relayer fee or principal reimbursement. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` and the adjacent the next module that consumes the same asset or transfer id after every branch.
