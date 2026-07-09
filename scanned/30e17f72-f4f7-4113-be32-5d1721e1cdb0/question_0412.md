# Q412: EVM Wormhole logMetadataExtension partial deployment rollback leaves live alias through cross-module drift

## Question
Can an unprivileged attacker use `public metadata flow through `logMetadata` on Wormhole-backed chains` with control over msg.value, current `wormholeNonce`, token address, name, symbol, and decimals and desynchronize `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` from the adjacent replay-protection bookkeeping that shares the same asset, nonce, proof subject, or mapping specifically in the `partial deployment rollback leaves live alias` attack class because serializes a Wormhole `LogMetadata` payload and publishes it before incrementing the nonce, violating `metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension`
- Entrypoint: `public metadata flow through `logMetadata` on Wormhole-backed chains`
- Attacker controls: msg.value, current `wormholeNonce`, token address, name, symbol, and decimals
- Exploit idea: Look for deployment flows that cross multiple contracts or callbacks before all state is committed. Focus on drift between this module and the adjacent replay-protection bookkeeping.
- Invariant to test: metadata publication must not let malicious tokens or publication edge cases create replayable or ambiguous remote deployment claims
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each subcall to fail independently and assert that the resulting state is either fully rolled back or fully usable, never half-bound. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol::logMetadataExtension` and the adjacent replay-protection bookkeeping after every branch.
