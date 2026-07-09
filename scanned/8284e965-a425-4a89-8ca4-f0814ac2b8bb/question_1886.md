# Q1886: EVM BridgeToken burn burn debits the wrong logical account at boundary values

## Question
Can an unprivileged attacker trigger `public outbound-side burn reachable only through bridge-owner calls` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/BridgeToken.sol::burn` violate `wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice` in the `burn debits the wrong logical account` attack class because owner-only burn is the bridge’s canonical way to destroy wrapped supply before emitting outbound claims becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::burn`
- Entrypoint: `public outbound-side burn reachable only through bridge-owner calls`
- Attacker controls: account and amount chosen by the bridge
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
