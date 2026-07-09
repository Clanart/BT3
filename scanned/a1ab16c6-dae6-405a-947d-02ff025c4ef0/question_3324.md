# Q3324: NEAR resolve_utxo_fin_transfer callback-bearing token flow exposes inconsistent intermediate state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after sending tokens for UTXO-to-Near settlement` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `callback-bearing token flow exposes inconsistent intermediate state` under interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
