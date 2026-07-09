# Q780: NEAR UTXO other-chain forwarder fee and principal split divergence

## Question
Can an unprivileged attacker enter through `public UTXO branch reached through `ft_on_transfer`` with crafted amount, fee, or native-fee inputs and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` use inconsistent fee and principal values across turns a verified UTXO-origin transfer into a new pending transfer for another chain after allocating a new origin nonce and destination nonce, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Focus on branches where fee checks happen before normalization, denormalization, callback resolution, or storage billing.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount/fee/native-fee edge cases around zero, max, and decimal boundaries and assert that emitted value plus stored fee always equals consumed value.
