# Q1403: EVM BridgeToken burn burn debits the wrong logical account

## Question
Can an unprivileged attacker use `public outbound-side burn reachable only through bridge-owner calls` so that `evm/src/omni-bridge/contracts/BridgeToken.sol::burn` burns or withholds value from a caller context different from the one the bridge event later attributes, violating `wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::burn`
- Entrypoint: `public outbound-side burn reachable only through bridge-owner calls`
- Attacker controls: account and amount chosen by the bridge
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject.
- Invariant to test: wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event.
