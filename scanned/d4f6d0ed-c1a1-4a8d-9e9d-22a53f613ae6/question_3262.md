# Q3262: NEAR UTXO other-chain forwarder fast amount-plus-fee check can be bypassed

## Question
Can an unprivileged attacker craft a fast-transfer input to `public UTXO branch reached through `ft_on_transfer`` so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` accepts an amount-plus-fee equality that does not correspond to the eventual canonical transfer because of turns a verified UTXO-origin transfer into a new pending transfer for another chain after allocating a new origin nonce and destination nonce, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token.
