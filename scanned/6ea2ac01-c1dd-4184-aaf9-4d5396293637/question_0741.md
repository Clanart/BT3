# Q741: EVM BridgeToken mint delivery callback leaves inconsistent state

## Question
Can an unprivileged attacker trigger a token-delivery callback from `public settlement-side mint reachable only through bridge-owner calls` that causes `evm/src/omni-bridge/contracts/BridgeToken.sol::mint` to keep or remove settlement state inconsistently with delivered value because of owner-only mint can either mint directly or mint with a message-enabled variant consumed by the bridge settlement flow, violating `mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release`?

## Target
- File/function: `evm/src/omni-bridge/contracts/BridgeToken.sol::mint`
- Entrypoint: `public settlement-side mint reachable only through bridge-owner calls`
- Attacker controls: beneficiary, amount, optional message bytes, and the receiver’s callback behavior when message-based minting is used
- Exploit idea: Focus on `ft_transfer_call`, unwrap callbacks, and post-delivery resolution that decide whether to burn, refund, or remove records.
- Invariant to test: mint-with-message and plain mint must not differ in a way that lets an attacker keep minted value while also forcing a settlement rollback or duplicate release
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Enumerate callback results and assert that each result maps to exactly one consistent combination of delivered value, replay state, and storage refund.
