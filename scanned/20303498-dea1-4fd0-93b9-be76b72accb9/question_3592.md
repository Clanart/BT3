# Q3592: NEAR UTXO transfer dispatcher fast path and normal path can both pay at boundary values

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for UTXO-origin settlement` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer` violate `UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states` in the `fast path and normal path can both pay` attack class because routes UTXO-origin settlements into Near or other-chain legs, creates fast-transfer state when applicable, and tracks `UnifiedTransferId` rather than plain nonces becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for UTXO-origin settlement`
- Attacker controls: token id, amount, signer/sender split, UTXO transfer message, origin chain, relayer fee, recipient, and message
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
