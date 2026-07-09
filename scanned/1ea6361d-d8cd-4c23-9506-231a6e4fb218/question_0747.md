# Q747: EVM Wormhole logMetadataExtension malicious metadata manufactures a bridge identity

## Question
Can an unprivileged attacker invoke `public metadata flow through `logMetadata` on Wormhole-backed chains` with a malicious token or metadata payload so that `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` records a deceptive asset identity that later drives deployment or claims, violating `metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension`
- Entrypoint: `public metadata flow through `logMetadata` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token address, name, symbol, and decimals
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs.
- Invariant to test: metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals.
