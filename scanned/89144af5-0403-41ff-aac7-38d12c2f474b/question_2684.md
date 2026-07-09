# Q2684: NEAR UTXO fast resolver fast path changes fee semantics without changing proof identity

## Question
Can an unprivileged attacker use `public UTXO fast path reached through `ft_on_transfer`` to create a fast-transfer state whose effective fee differs from the fee later proven and claimed via `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`, violating `the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_fast`
- Entrypoint: `public UTXO fast path reached through `ft_on_transfer``
- Attacker controls: fast-transfer id, stored fast-transfer status, destination chain, amount, and relayer recipient
- Exploit idea: Target relayer-sponsored fast paths where the first leg is paid before the canonical proof arrives.
- Invariant to test: the same UTXO fast-transfer id must not both pay a relayer immediately and remain claimable on a second leg
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare claimed fee, relayer payout, and stored transfer fee across both legs and assert that the bridge never accepts a mismatch.
