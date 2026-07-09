# Q138: NEAR BTC/Zcash chain config mapping wrong token can satisfy UTXO native-asset requirement

## Question
Can an unprivileged attacker reach `public UTXO transfer paths through `ft_on_transfer` and finalize flows` and make `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` accept a token that is merely mapped, but not truly the configured native UTXO asset, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Target token-id lookup and chain-config binding for BTC-like connectors.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Swap configured and non-configured assets and assert that every accepted UTXO path uses the exact configured native token id.
