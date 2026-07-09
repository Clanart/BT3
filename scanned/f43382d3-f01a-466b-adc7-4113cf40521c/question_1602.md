# Q1602: NEAR UTXO fast resolver fast path and normal path can both pay via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO fast path reached through `ft_on_transfer`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path and normal path can both pay` under finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
