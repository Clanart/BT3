# Q2562: NEAR BTC/Zcash chain config mapping origin inference changes custody branch at boundary values

## Question
Can an unprivileged attacker trigger `public UTXO transfer paths through `ft_on_transfer` and finalize flows` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` violate `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain` in the `origin inference changes custody branch` attack class because uses per-chain config to decide which token id and connector represent BTC-like assets for UTXO bridging becomes fragile at those edges?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Probe naming-convention inference and cache invalidation around deployed or migrated tokens. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Balance manipulation
- Fast validation: Generate tokens near every naming boundary and assert that origin inference matches the canonical mapping and custody model. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
