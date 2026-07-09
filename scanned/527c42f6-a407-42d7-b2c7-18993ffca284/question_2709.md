# Q2709: NEAR BTC/Zcash chain config mapping global asset-conservation invariant break

## Question
Can an unprivileged attacker combine the public surface behind `public UTXO transfer paths through `ft_on_transfer` and finalize flows` with the code paths summarized by `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` and make total redeemable claims across chains exceed the total burned, locked, or custodied assets tracked by uses per-chain config to decide which token id and connector represent BTC-like assets for UTXO bridging, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Treat the target as one part of a multi-leg conservation system rather than an isolated bug class.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build an invariant test that sums principal, fees, wrapped supply, custody, and lock rows across all affected branches and assert conservation after every step.
