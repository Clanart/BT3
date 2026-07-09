# Q1073: EVM BridgeToken mint delivery callback leaves inconsistent state through cross-module drift

## Question
Can an unprivileged attacker use `public settlement-side mint reachable only through bridge-owner calls` with control over beneficiary, amount, optional message bytes, and the receiver’s callback behavior when message-based minting is used and desynchronize `evm/src/omni-bridge/contracts/BridgeToken.sol::mint` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `delivery callback leaves inconsistent state` attack class because owner-only mint can either mint directly or mint with a message-enabled variant consumed by the bridge settlement flow, violating `mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::mint`
- Entrypoint: `public settlement-side mint reachable only through bridge-owner calls`
- Attacker controls: beneficiary, amount, optional message bytes, and the receiver’s callback behavior when message-based minting is used
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/BridgeToken.sol::mint` and the adjacent mint, burn, or custody accounting after every branch.
