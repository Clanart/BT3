# Q1469: NEAR BTC/Zcash chain config mapping custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `public UTXO transfer paths through `ft_on_transfer` and finalize flows` to make `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` increase wrapped supply or reduce custody without the complementary change on the other side, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
