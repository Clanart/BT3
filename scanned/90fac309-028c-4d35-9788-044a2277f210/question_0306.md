# Q306: NEAR BTC/Zcash chain config mapping wrong token can satisfy UTXO native-asset requirement via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO transfer paths through `ft_on_transfer` and finalize flows` and then replay or reorder the adjacent bridge step that consumes the same state so that `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` ends up accepting two inconsistent interpretations of the same economic event specifically around `wrong token can satisfy UTXO native-asset requirement` under uses per-chain config to decide which token id and connector represent BTC-like assets for UTXO bridging, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Target token-id lookup and chain-config binding for BTC-like connectors. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Swap configured and non-configured assets and assert that every accepted UTXO path uses the exact configured native token id. Then replay or reorder the adjacent bridge step that consumes the same state and assert that the bridge still exposes only one valid economic outcome.
