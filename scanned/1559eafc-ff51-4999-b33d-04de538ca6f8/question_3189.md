# Q3189: NEAR resolve_utxo_fin_transfer callback-bearing token flow exposes inconsistent intermediate state

## Question
Can an unprivileged attacker exploit a callback-bearing branch in `callback after sending tokens for UTXO-to-Near settlement` so that `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` exposes intermediate state that a receiver or token contract can act on inconsistently, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target `ft_transfer_call`, ERC-1155 safe transfers, or custom-minter callbacks that occur before cleanup finishes.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Contract execution flows
- Fast validation: Instrument reentrant-capable receivers and assert that every externally-observable intermediate state is either harmless or replay-proof.
