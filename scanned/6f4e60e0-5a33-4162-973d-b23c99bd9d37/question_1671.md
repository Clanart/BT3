# Q1671: NEAR resolve_utxo_fin_transfer RBF or connector semantics leave stale bridge state through cross-module drift

## Question
Can an unprivileged attacker use `callback after sending tokens for UTXO-to-Near settlement` with control over token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner and desynchronize `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `RBF or connector semantics leave stale bridge state` attack class because interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target UTXO callbacks and connector withdrawal coupling where external UTXO semantics may evolve while bridge state stays fixed. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model replacement or reordered UTXO events and assert that the bridge cannot settle both the replaced and replacement economic event. Also assert cross-module consistency between `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` and the adjacent mint, burn, or custody accounting after every branch.
