# Q1240: EVM BridgeToken burn native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `public outbound-side burn reachable only through bridge-owner calls` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/BridgeToken.sol::burn` violate `wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice` in the `native versus wrapped branch switch` attack class because owner-only burn is the bridge’s canonical way to destroy wrapped supply before emitting outbound claims becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::burn`
- Entrypoint: `public outbound-side burn reachable only through bridge-owner calls`
- Attacker controls: account and amount chosen by the bridge
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: wrapped-token burns must always stay synchronized with emitted bridge events so users cannot redeem, migrate, or replay burned value twice
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
