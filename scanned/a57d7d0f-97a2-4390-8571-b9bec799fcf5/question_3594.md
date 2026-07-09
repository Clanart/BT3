# Q3594: NEAR resolve_utxo_fin_transfer callback-bearing token flow exposes inconsistent intermediate state at boundary values

## Question
Can an unprivileged attacker trigger `callback after sending tokens for UTXO-to-Near settlement` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` violate `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases` in the `callback-bearing token flow exposes inconsistent intermediate state` attack class because interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
