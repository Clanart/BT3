# Q1349: NEAR resolve_utxo_fin_transfer RBF or connector semantics leave stale bridge state

## Question
Can an unprivileged attacker exploit `callback after sending tokens for UTXO-to-Near settlement` so that `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` keeps or reopens bridge state after a UTXO replacement or connector-side change because of interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target UTXO callbacks and connector withdrawal coupling where external UTXO semantics may evolve while bridge state stays fixed.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model replacement or reordered UTXO events and assert that the bridge cannot settle both the replaced and replacement economic event.
