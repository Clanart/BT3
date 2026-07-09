# Q2079: NEAR UTXO other-chain forwarder same fee collectible twice

## Question
Can an unprivileged attacker reach `public UTXO branch reached through `ft_on_transfer`` and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` remove or preserve fee-bearing state in a way that allows the same fee to be claimed twice, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Target pending-transfer cleanup, fast-transfer removal, and replay protection around fee-claim proofs.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Claim once, then replay or mutate non-economic proof fields and assert that no second claim succeeds or blocks unrelated transfers.
