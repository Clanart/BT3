# Q3924: NEAR UTXO fast resolver fast amount-plus-fee check can be bypassed via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO fast path reached through `ft_on_transfer`` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast amount-plus-fee check can be bypassed` under finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
