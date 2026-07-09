# Q2683: NEAR UTXO other-chain forwarder fast path changes fee semantics without changing proof identity

## Question
Can an unprivileged attacker use `public UTXO branch reached through `ft_on_transfer`` to create a fast-transfer state whose effective fee differs from the fee later proven and claimed via `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Target relayer-sponsored fast paths where the first leg is paid before the canonical proof arrives.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare claimed fee, relayer payout, and stored transfer fee across both legs and assert that the bridge never accepts a mismatch.
