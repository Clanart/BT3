# Q3040: NEAR UTXO transfer dispatcher final settlement and later fee claim can diverge at boundary values

## Question
Can an unprivileged attacker trigger ``ft_on_transfer` branch for UTXO-origin settlement` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `near/omni-bridge/src/lib.rs::utxo_fin_transfer` violate `UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states` in the `final settlement and later fee claim can diverge` attack class because routes UTXO-origin settlements into Near or other-chain legs, creates fast-transfer state when applicable, and tracks `UnifiedTransferId` rather than plain nonces becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for UTXO-origin settlement`
- Attacker controls: token id, amount, signer/sender split, UTXO transfer message, origin chain, relayer fee, recipient, and message
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
