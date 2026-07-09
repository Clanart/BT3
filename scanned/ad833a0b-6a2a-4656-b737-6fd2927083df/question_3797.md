# Q3797: NEAR UTXO other-chain forwarder fast-transfer status changes in the wrong order

## Question
Can an unprivileged attacker trigger `public UTXO branch reached through `ft_on_transfer`` so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` marks, removes, or reuses fast-transfer state in an order that opens replay or fee-claim gaps, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Target state transitions among pending, finalised, removed, and claimed statuses across both legs.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Drive all race orders between fast payout, canonical finalization, and fee claim and assert that each fast-transfer id follows one monotonic lifecycle.
