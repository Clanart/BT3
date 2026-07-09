# Q849: NEAR UTXO transfer dispatcher recipient or fee-recipient rebinding via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through ``ft_on_transfer` branch for UTXO-origin settlement` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `recipient or fee-recipient rebinding` under routes UTXO-origin settlements into Near or other-chain legs, creates fast-transfer state when applicable, and tracks `UnifiedTransferId` rather than plain nonces, violating `UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for UTXO-origin settlement`
- Attacker controls: token id, amount, signer/sender split, UTXO transfer message, origin chain, relayer fee, recipient, and message
- Exploit idea: Exploit optional fee-recipient fields, fast-transfer relayer substitution, or predecessor-captured identities. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Build pairs of proofs/messages that vary only in recipient-oriented fields and assert that settlement, fee claim, and event emission stay bound to one tuple. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
