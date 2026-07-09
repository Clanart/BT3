# Q3798: NEAR UTXO fast resolver fast amount-plus-fee check can be bypassed

## Question
Can an unprivileged attacker craft a fast-transfer input to `public UTXO fast path reached through `ft_on_transfer`` so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast` accepts an amount-plus-fee equality that does not correspond to the eventual canonical transfer because of finalizes or removes a UTXO fast transfer depending on whether the destination is Near or another chain, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token.
