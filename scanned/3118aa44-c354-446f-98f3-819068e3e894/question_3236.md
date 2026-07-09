# Q3236: EVM Wormhole logMetadataExtension cross-contract deploy or finalize callbacks can alias another subject

## Question
Can an unprivileged attacker use `public metadata flow through `logMetadata` on Wormhole-backed chains` so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` receives a successful callback from the wrong contract instance or for the wrong subject because of serializes a Wormhole `LogMetadata` payload and publishes it before incrementing the nonce, violating `metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension`
- Entrypoint: `public metadata flow through `logMetadata` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token address, name, symbol, and decimals
- Exploit idea: Probe callback code that assumes one-to-one correspondence between outstanding promise and token or transfer subject.
- Invariant to test: metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Open multiple outstanding operations and assert that each callback can only complete the exact originating subject.
