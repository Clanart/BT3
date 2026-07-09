# Q2601: NEAR resolve_utxo_fin_transfer custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `callback after sending tokens for UTXO-to-Near settlement` to make `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` increase wrapped supply or reduce custody without the complementary change on the other side, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
