# Q2044: EVM BridgeToken burn custody accounting diverges from wrapped supply

## Question
Can an unprivileged attacker use `public outbound-side burn reachable only through bridge-owner calls` to make `evm/src/omni-bridge/contracts/BridgeToken.sol::burn` increase wrapped supply or reduce custody without the complementary change on the other side, violating `wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::burn`
- Entrypoint: `public outbound-side burn reachable only through bridge-owner calls`
- Attacker controls: account and amount chosen by the bridge
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value.
- Invariant to test: wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow.
