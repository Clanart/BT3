# Q579: EVM Wormhole deployTokenExtension partial deployment rollback leaves live alias at boundary values

## Question
Can an unprivileged attacker trigger `public deploy flow through `deployToken` on Wormhole-backed chains` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` violate `message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa` in the `partial deployment rollback leaves live alias` attack class because serializes a Wormhole `DeployToken` payload and publishes it with the current nonce before incrementing `wormholeNonce` becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension`
- Entrypoint: `public deploy flow through `deployToken` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
