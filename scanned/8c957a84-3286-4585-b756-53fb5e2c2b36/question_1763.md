# Q1763: NEAR UTXO fast resolver fast path and normal path can both pay through cross-module drift

## Question
Can an unprivileged attacker use `public UTXO fast path reached through `ft_on_transfer`` with control over fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient and desynchronize `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` from the adjacent the next module that consumes the same asset or transfer id that shares the same asset, nonce, proof subject, or mapping specifically in the `fast path and normal path can both pay` attack class because finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Focus on drift between this module and the adjacent the next module that consumes the same asset or transfer id.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` and the adjacent the next module that consumes the same asset or transfer id after every branch.
