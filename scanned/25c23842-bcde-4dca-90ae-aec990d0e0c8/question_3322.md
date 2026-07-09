# Q3322: NEAR UTXO transfer dispatcher fast path and normal path can both pay via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through ``ft_on_transfer` branch for UTXO-origin settlement` and then replay or reorder later fee-claim proof submission so that `near/omni-bridge/src/lib.rs::utxo_fin_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `fast path and normal path can both pay` under routes UTXO-origin settlements into Near or other-chain legs, creates fast-transfer state when applicable, and tracks `UnifiedTransferId` rather than plain nonces, violating `UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::utxo_fin_transfer`
- Entrypoint: ``ft_on_transfer` branch for UTXO-origin settlement`
- Attacker controls: token id, amount, signer/sender split, UTXO transfer message, origin chain, relayer fee, recipient, and message
- Exploit idea: Target relayer substitution, `origin_transfer_id`, and the moment when fast transfers become finalised or removable. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO-origin flows must not let one origin outpoint or replacement message fan out into multiple bridge settlements or mismatched lock states
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Simulate fast settlement before and after the canonical proof arrives and assert that total user-plus-relayer payouts never exceed the original transfer amount plus intended fee split. Then replay or reorder later fee-claim proof submission and assert that the bridge still exposes only one valid economic outcome.
