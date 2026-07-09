# Q3096: EVM Wormhole logMetadataExtension shared Wormhole nonce can be replayed or gap-filled at boundary values

## Question
Can an unprivileged attacker trigger `public metadata flow through `logMetadata` on Wormhole-backed chains` with boundary-controlled inputs covering nonce boundaries, bucket boundaries, and maximal counters and make `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` violate `metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims` in the `shared Wormhole nonce can be replayed or gap-filled` attack class because serializes a Wormhole `LogMetadata` payload and publishes it before incrementing the nonce becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension`
- Entrypoint: `public metadata flow through `logMetadata` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token address, name, symbol, and decimals
- Exploit idea: Target contracts that reuse one monotonic Wormhole nonce across deploy, init, metadata, and finalize messages. Concentrate on nonce boundaries, bucket boundaries, and maximal counters.
- Invariant to test: metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Interleave message classes and failures and assert that nonce progression remains globally unique and monotonic for emitted messages. Sweep boundary values for nonce boundaries, bucket boundaries, and maximal counters and assert that the same invariant holds at every edge.
