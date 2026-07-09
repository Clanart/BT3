# Q3231: EVM BridgeToken mint different callback outcomes produce the same user-visible success

## Question
Can an unprivileged attacker use `public settlement-side mint reachable only through bridge-owner calls` so that `evm/src/omni-bridge/contracts/BridgeToken.sol::mint` treats materially different callback outcomes as the same economic result because of owner-only mint can either mint directly or mint with a message-enabled variant consumed by the bridge settlement flow, violating `mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::mint`
- Entrypoint: `public settlement-side mint reachable only through bridge-owner calls`
- Attacker controls: beneficiary, amount, optional message bytes, and the receiver’s callback behavior when message-based minting is used
- Exploit idea: Target branches that interpret callback bytes leniently or default to success-like behavior on malformed returns.
- Invariant to test: mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release
- Expected Immunefi impact: Contract execution flows
- Fast validation: Enumerate all callback result shapes and assert one unique mapping from callback outcome to bridge state transition.
