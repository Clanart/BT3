# Q3532: NEAR UTXO other-chain forwarder fast amount-plus-fee check can be bypassed through cross-module drift

## Question
Can an unprivileged attacker use `public UTXO branch reached through `ft_on_transfer`` with control over UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status and desynchronize `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `fast amount-plus-fee check can be bypassed` attack class because turns a verified UTXO-origin transfer into a new pending transfer for another chain after allocating a new origin nonce and destination nonce, violating `UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain`
- Entrypoint: `public UTXO branch reached through `ft_on_transfer``
- Attacker controls: UTXO transfer message, origin chain, destination chain, relayer fee, and fast-transfer status
- Exploit idea: Probe denormalization, zero-fee, and token-decimal edge cases in fast paths. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: UTXO forwarding must not let one origin UTXO create multiple cross-chain obligations with inconsistent fee or recipient binding
- Expected Immunefi impact: Balance manipulation
- Fast validation: Fuzz amount and fee around normalization boundaries and assert that the accepted fast total always matches the canonical transfer total for that token. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::utxo_fin_transfer_to_other_chain` and the adjacent replay-protection bookkeeping after every branch.
