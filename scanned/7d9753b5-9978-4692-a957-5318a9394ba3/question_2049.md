# Q2049: EVM Wormhole logMetadataExtension message publication drifts from on-chain state

## Question
Can an unprivileged attacker exploit `public metadata flow through `logMetadata` on Wormhole-backed chains` so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` publishes a Wormhole message that no longer matches local state because of serializes a Wormhole `LogMetadata` payload and publishes it before incrementing the nonce, violating `metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension`
- Entrypoint: `public metadata flow through `logMetadata` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token address, name, symbol, and decimals
- Exploit idea: Focus on nonce increment timing, extension calls, and underpaid publication fees.
- Invariant to test: metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Force publication or extension failures and assert that any emitted Wormhole message corresponds to one successfully-committed local economic action.
