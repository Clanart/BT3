# Q1440: NEAR UTXO other-chain forwarder UTXO native-token requirement bypass

## Question
Can an unprivileged attacker craft a UTXO-facing outbound flow through `public UTXO branch reached through `ft_on_transfer`` that makes `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` accept a non-native token or wrong chain config because of turns a verified UTXO-origin transfer into a new pending transfer for another chain after allocating a new origin nonce and destination nonce, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Target token-origin checks and chain-specific native-token requirements in BTC/Zcash-style flows.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz chain/token combinations and assert that every accepted UTXO-facing flow uses exactly the configured native asset for that chain.
