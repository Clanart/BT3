# Q2348: EVM BridgeToken burn custody accounting diverges from wrapped supply through cross-module drift

## Question
Can an unprivileged attacker use `public outbound-side burn reachable only through bridge-owner calls` with control over account and amount chosen by the bridge and desynchronize `evm/src/omni-bridge/contracts/BridgeToken.sol::burn` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `custody accounting diverges from wrapped supply` attack class because owner-only burn is the bridge’s canonical way to destroy wrapped supply before emitting outbound claims, violating `wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::burn`
- Entrypoint: `public outbound-side burn reachable only through bridge-owner calls`
- Attacker controls: account and amount chosen by the bridge
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/BridgeToken.sol::burn` and the adjacent mint, burn, or custody accounting after every branch.
