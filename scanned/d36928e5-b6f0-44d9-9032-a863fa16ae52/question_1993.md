# Q1993: NEAR resolve_utxo_fin_transfer mint-with-message path differs economically from plain mint

## Question
Can an unprivileged attacker trigger `callback after sending tokens for UTXO-to-Near settlement` so that `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` mints through a callback-bearing path whose failure semantics differ from plain minting, violating `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases`?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches.
