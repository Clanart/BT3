# Q71: EVM BridgeToken burn burn or lock before irreversible state

## Question
Can an unprivileged attacker use `public outbound-side burn reachable only through bridge-owner calls` to force `evm/src/omni-bridge/contracts/BridgeToken.sol::burn` to burn or lock assets before the transfer record becomes safely irreversible, and then recover or redirect the bridge flow via owner-only burn is the bridge’s canonical way to destroy wrapped supply before emitting outbound claims, violating `wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::burn`
- Entrypoint: `public outbound-side burn reachable only through bridge-owner calls`
- Attacker controls: account and amount chosen by the bridge
- Exploit idea: Look for branches where custody changes happen before the final pending-state, mapping, or callback outcome is fixed.
- Invariant to test: wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Model failures between custody changes and state writes, then assert that no branch both consumes user value and allows the transfer to be replayed or dropped.
