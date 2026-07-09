# Q1141: NEAR BTC/Zcash chain config mapping asset mapping drifts away from actual token semantics through cross-module drift

## Question
Can an unprivileged attacker use `public UTXO transfer paths through `ft_on_transfer` and finalize flows` with control over token id, configured UTXO chain connectors, origin chain, and destination chain and desynchronize `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `asset mapping drifts away from actual token semantics` attack class because uses per-chain config to decide which token id and connector represent BTC-like assets for UTXO bridging, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation. Also assert cross-module consistency between `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` and the adjacent token-mapping and asset-identity logic after every branch.
