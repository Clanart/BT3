# Q1408: EVM Wormhole logMetadataExtension remote publication drifts from local deployment state

## Question
Can an unprivileged attacker exploit `public metadata flow through `logMetadata` on Wormhole-backed chains` so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` publishes a deploy or metadata message that no longer matches local token state because of serializes a Wormhole `LogMetadata` payload and publishes it before incrementing the nonce, violating `metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension`
- Entrypoint: `public metadata flow through `logMetadata` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token address, name, symbol, and decimals
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps.
- Invariant to test: metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token.
