# Q3003: NEAR BTC/Zcash chain config mapping global asset-conservation invariant break through cross-module drift

## Question
Can an unprivileged attacker use `public UTXO transfer paths through `ft_on_transfer` and finalize flows` with control over token id, configured UTXO chain connectors, origin chain, and destination chain and desynchronize `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `global asset-conservation invariant break` attack class because uses per-chain config to decide which token id and connector represent BTC-like assets for UTXO bridging, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step. Also assert cross-module consistency between `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` and the adjacent token-mapping and asset-identity logic after every branch.
