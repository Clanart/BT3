# Q2106: NEAR BTC/Zcash chain config mapping origin inference changes custody branch

## Question
Can an unprivileged attacker choose a token through `public UTXO transfer paths through `ft_on_transfer` and finalize flows` such that `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` infers the wrong origin chain from naming, caches, or config, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Probe naming-convention inference and cache invalidation around deployed or migrated tokens.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Balance manipulation
- Fast validation: Generate tokens near every naming boundary and assert that origin inference matches the canonical mapping and custody model.
