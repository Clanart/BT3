# Q2347: EVM BridgeToken mint custody accounting diverges from wrapped supply through cross-module drift

## Question
Can an unprivileged attacker use `public settlement-side mint reachable only through bridge-owner calls` with control over beneficiary, amount, optional message bytes, and the receiver’s callback behavior when message-based minting is used and desynchronize `evm/src/omni-bridge/contracts/BridgeToken.sol::mint` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `custody accounting diverges from wrapped supply` attack class because owner-only mint can either mint directly or mint with a message-enabled variant consumed by the bridge settlement flow, violating `mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::mint`
- Entrypoint: `public settlement-side mint reachable only through bridge-owner calls`
- Attacker controls: beneficiary, amount, optional message bytes, and the receiver’s callback behavior when message-based minting is used
- Exploit idea: Target branches that mint, burn, lock, unlock, transfer vault assets, or unwrap native value. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Build a per-asset conservation model and assert that total claims never exceed total backing after every public flow. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/BridgeToken.sol::mint` and the adjacent mint, burn, or custody accounting after every branch.
