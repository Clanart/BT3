# Q809: NEAR BTC/Zcash chain config mapping asset mapping drifts away from actual token semantics

## Question
Can an unprivileged attacker exploit `public UTXO transfer paths through `ft_on_transfer` and finalize flows` so that `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage` keeps a token mapped as canonical after its actual runtime semantics or backing assumptions diverge, violating `UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain`?

## Target
- File/function: `near/omni-bridge/src/btc.rs and `utxo_chain_connectors` usage`
- Entrypoint: `public UTXO transfer paths through `ft_on_transfer` and finalize flows`
- Attacker controls: token id, configured UTXO chain connectors, origin chain, and destination chain
- Exploit idea: Target upgrades, migration swaps, fake bridge-controlled tokens, and deploy callbacks.
- Invariant to test: UTXO chain-config lookup must not let a token registered for one connector satisfy the native-token requirement of another chain
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Change token semantics around an existing mapping and assert that the bridge does not keep treating the token as a valid canonical representation.
