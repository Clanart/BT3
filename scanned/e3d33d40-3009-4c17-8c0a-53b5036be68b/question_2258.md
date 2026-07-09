# Q2258: NEAR BTC/Zcash chain config mapping origin inference changes custody branch via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public UTXO transfer paths through `ft_on_transfer` and finalize flows` and then replay or reorder the adjacent bridge step that consumes the same state so that `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` ends up accepting two inconsistent interpretations of the same economic event specifically around `origin inference changes custody branch` under uses per-chain config to decide which token id and connector represent BTC-like assets for UTXO bridging, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Probe naming-convention inference and cache invalidation around deployed or migrated tokens. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Balance manipulation
- Fast validation: Generate tokens near every naming boundary and assert that origin inference matches the canonical mapping and custody model. Then replay or reorder the adjacent bridge step that consumes the same state and assert that the bridge still exposes only one valid economic outcome.
