# Q1832: NEAR resolve_utxo_fin_transfer RBF or connector semantics leave stale bridge state at boundary values

## Question
Can an unprivileged attacker trigger `callback after sending tokens for UTXO-to-Near settlement` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` violate `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases` in the `RBF or connector semantics leave stale bridge state` attack class because interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target UTXO callbacks and connector withdrawal coupling where external UTXO semantics may evolve while bridge state stays fixed. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model replacement or reordered UTXO events and assert that the bridge cannot settle both the replaced and replacement economic event. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
