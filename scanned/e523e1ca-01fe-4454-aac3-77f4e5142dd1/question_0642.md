# Q642: NEAR BTC/Zcash chain config mapping wrong token can satisfy UTXO native-asset requirement at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO transfer paths through `ft_on_transfer` and finalize flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` violate `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain` in the `wrong token can satisfy UTXO native-asset requirement` attack class because uses per-chain config to decide which token id and connector represent BTC-like assets for UTXO bridging becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Target token-id lookup and chain-config binding for BTC-like connectors. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Swap configured and non-configured assets and assert that every accepted UTXO path uses the exact configured native token id. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
