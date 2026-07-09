# Q1923: NEAR UTXO other-chain forwarder UTXO native-token requirement bypass at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO branch reached through `ft_on_transfer`` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` violate `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding` in the `UTXO native-token requirement bypass` attack class because turns a verified UTXO-origin transfer into a new pending transfer for another chain after allocating a new origin nonce and destination nonce becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Target token-origin checks and chain-specific native-token requirements in BTC/Zcash-style flows. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz chain/token combinations and assert that every accepted UTXO-facing flow uses exactly the configured native asset for that chain. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
