# Q517: NEAR resolve_utxo_fin_transfer asset-branch confusion on finalization at boundary values

## Question
Can an unprivileged attacker trigger `callback after sending tokens for UTXO-to-Near settlement` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` violate `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases` in the `asset-branch confusion on finalization` attack class because interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
