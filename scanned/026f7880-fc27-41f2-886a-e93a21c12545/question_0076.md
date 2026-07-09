# Q76: EVM Wormhole logMetadataExtension partial deployment rollback leaves live alias

## Question
Can an unprivileged attacker trigger a partial failure through `public metadata flow through `logMetadata` on Wormhole-backed chains` such that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` leaves behind either a live token without mappings or mappings without a usable token because of serializes a Wormhole `LogMetadata` payload and publishes it before incrementing the nonce, violating `metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension`
- Entrypoint: `public metadata flow through `logMetadata` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token address, name, symbol, and decimals
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed.
- Invariant to test: metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound.
