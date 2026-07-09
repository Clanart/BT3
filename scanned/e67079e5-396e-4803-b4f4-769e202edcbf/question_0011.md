# Q11: NEAR UTXO transfer dispatcher replay guard can be bypassed or consumed incorrectly

## Question
Can an unprivileged attacker settle through ``ft_on_transfer` branch for UTXO-origin settlement` and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer` either bypass replay protection or consume it for the wrong event because of routes UTXO-origin settlements into Near or other-chain legs, creates fast-transfer state when applicable, and tracks `UnifiedTransferId` rather than plain nonces, violating `UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for UTXO-origin settlement`
- Attacker controls: token id, amount, signer/sender split, UTXO transfer message, origin chain, relayer fee, recipient, and message
- Exploit idea: Stress replay-protection state keyed only by nonce, transfer id, or bitmap position across branches and chains.
- Invariant to test: UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Replay valid proofs/signatures with altered non-economic fields and assert that only the exact originally-settled event is rejected as already used.
