# Q2145: NEAR resolve_utxo_fin_transfer mint-with-message path differs economically from plain mint via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `callback after sending tokens for UTXO-to-Near settlement` and then replay or reorder later callback or refund resolution so that `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` ends up accepting two inconsistent interpretations of the same economic event specifically around `mint-with-message path differs economically from plain mint` under interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Then replay or reorder later callback or refund resolution and assert that the bridge still exposes only one valid economic outcome.
