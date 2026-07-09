# Q2893: NEAR UTXO transfer dispatcher final settlement and later fee claim can diverge through cross-module drift

## Question
Can an unprivileged attacker use ``ft_on_transfer` branch for UTXO-origin settlement` with control over token id, amount, signer/sender split, UTXO transfer message, origin chain, relayer fee, recipient, and message and desynchronize `near/omni-bridge/src/lib.rs::utxo_fin_transfer` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `final settlement and later fee claim can diverge` attack class because routes UTXO-origin settlements into Near or other-chain legs, creates fast-transfer state when applicable, and tracks `UnifiedTransferId` rather than plain nonces, violating `UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for UTXO-origin settlement`
- Attacker controls: token id, amount, signer/sender split, UTXO transfer message, origin chain, relayer fee, recipient, and message
- Exploit idea: Target differences between settle-time denormalization and claim-time recomputation of fee, dust, or relayer substitution. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states
- Expected Immunefi impact: Balance manipulation
- Fast validation: Compare settled principal, stored transfer record, and fee-claim proof under edge amounts and assert that the three always reconstruct one consistent event. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::utxo_fin_transfer` and the adjacent replay-protection bookkeeping after every branch.
