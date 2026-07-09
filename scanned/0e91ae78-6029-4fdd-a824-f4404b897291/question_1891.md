# Q1891: EVM Wormhole logMetadataExtension remote publication drifts from local deployment state at boundary values

## Question
Can an unprivileged attacker trigger `public metadata flow through `logMetadata` on Wormhole-backed chains` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` violate `metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims` in the `remote publication drifts from local deployment state` attack class because serializes a Wormhole `LogMetadata` payload and publishes it before incrementing the nonce becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension`
- Entrypoint: `public metadata flow through `logMetadata` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token address, name, symbol, and decimals
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
