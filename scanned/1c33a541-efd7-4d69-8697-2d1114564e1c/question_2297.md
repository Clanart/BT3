# Q2297: NEAR resolve_utxo_fin_transfer mint-with-message path differs economically from plain mint through cross-module drift

## Question
Can an unprivileged attacker use `callback after sending tokens for UTXO-to-Near settlement` with control over token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner and desynchronize `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `mint-with-message path differs economically from plain mint` attack class because interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` and the adjacent mint, burn, or custody accounting after every branch.
