# Q3042: NEAR resolve_utxo_fin_transfer custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `callback after sending tokens for UTXO-to-Near settlement` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` violate `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases` in the `custody accounting diverges from wrapped supply` attack class because interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
