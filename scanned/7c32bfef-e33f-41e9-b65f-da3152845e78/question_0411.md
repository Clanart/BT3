# Q411: EVM Wormhole deployTokenExtension partial deployment rollback leaves live alias through cross-module drift

## Question
Can an unprivileged attacker use `public deploy flow through `deployToken` on Wormhole-backed chains` with control over msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals and desynchronize `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `partial deployment rollback leaves live alias` attack class because serializes a Wormhole `DeployToken` payload and publishes it with the current nonce before incrementing `wormholeNonce`, violating `message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension`
- Entrypoint: `public deploy flow through `deployToken` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` and the adjacent replay-protection bookkeeping after every branch.
