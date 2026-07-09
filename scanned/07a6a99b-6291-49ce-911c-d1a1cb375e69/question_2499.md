# Q2499: EVM BridgeToken mint custody accounting diverges from wrapped supply at boundary values

## Question
Can an unprivileged attacker trigger `public settlement-side mint reachable only through bridge-owner calls` with boundary-controlled inputs covering zero, maximal, and malformed user-controlled values and make `evm/src/omni-bridge/contracts/BridgeToken.sol::mint` violate `mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release` in the `custody accounting diverges from wrapped supply` attack class because owner-only mint can either mint directly or mint with a message-enabled variant consumed by the bridge settlement flow becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::mint`
- Entrypoint: `public settlement-side mint reachable only through bridge-owner calls`
- Attacker controls: beneficiary, amount, optional message bytes, and the receiver’s callback behavior when message-based minting is used
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Concentrate on zero, maximal, and malformed user-controlled values.
- Invariant to test: mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Sweep boundary values for zero, maximal, and malformed user-controlled values and assert that the same invariant holds at every edge.
