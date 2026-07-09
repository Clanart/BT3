# Q239: EVM BridgeToken burn burn or lock before irreversible state via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public outbound-side burn reachable only through bridge-owner calls` and then replay or reorder the later settlement leg on another chain so that `evm/src/omni-bridge/contracts/BridgeToken.sol::burn` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn or lock before irreversible state` under owner-only burn is the bridge’s canonical way to destroy wrapped supply before emitting outbound claims, violating `wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::burn`
- Entrypoint: `public outbound-side burn reachable only through bridge-owner calls`
- Attacker controls: account and amount chosen by the bridge
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped. Then replay or reorder the later settlement leg on another chain and assert that the bridge still exposes only one valid economic outcome.
