# Q13: NEAR resolve_utxo_fin_transfer asset-branch confusion on finalization

## Question
Can an unprivileged attacker use `callback after sending tokens for UTXO-to-Near settlement` to make `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` release value through a more favorable branch than the source event actually authorized because of interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target native-versus-wrapped, vault-versus-mint, ERC-20-versus-ERC-1155, or custom-minter-versus-custody branches.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Assert that every accepted settlement lands on exactly the branch implied by the validated source asset type and mapping state.
