# Q2449: NEAR resolve_utxo_fin_transfer mint-with-message path differs economically from plain mint at boundary values

## Question
Can an unprivileged attacker trigger `callback after sending tokens for UTXO-to-Near settlement` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer` violate `UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases` in the `mint-with-message path differs economically from plain mint` attack class because interprets callback refund semantics and either removes tracked UTXO finalization state or emits the completed event becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/lib.rs::resolve_utxo_fin_transfer`
- Entrypoint: `callback after sending tokens for UTXO-to-Near settlement`
- Attacker controls: token id, amount, callback outcome, UTXO transfer message, origin chain, and storage owner
- Exploit idea: Target bridge-token wrappers that mint to a temporary holder or rely on `ft_transfer_call`-style callbacks. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO resolution must not leave the same origin transfer both payable and replayable after callback edge cases
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Compare balances and state after every callback result and assert equivalence between message and no-message branches. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
