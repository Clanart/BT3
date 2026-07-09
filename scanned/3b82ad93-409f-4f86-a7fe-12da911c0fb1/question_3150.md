# Q3150: NEAR BTC/Zcash chain config mapping global asset-conservation invariant break at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO transfer paths through `ft_on_transfer` and finalize flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` violate `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain` in the `global asset-conservation invariant break` attack class because uses per-chain config to decide which token id and connector represent BTC-like assets for UTXO bridging becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
