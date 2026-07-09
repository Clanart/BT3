# Q1890: EVM Wormhole deployTokenExtension remote publication drifts from local deployment state at boundary values

## Question
Can an unprivileged attacker trigger `public deploy flow through `deployToken` on Wormhole-backed chains` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension` violate `message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa` in the `remote publication drifts from local deployment state` attack class because serializes a Wormhole `DeployToken` payload and publishes it with the current nonce before incrementing `wormholeNonce` becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::deployTokenExtension`
- Entrypoint: `public deploy flow through `deployToken` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token string, deployed token address, decimals, and origin decimals
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: message publication must stay synchronized with on-chain deployment state so a failed or reordered publish cannot leave a live token with no corresponding remote registration or vice versa
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
