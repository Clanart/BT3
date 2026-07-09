# Q2641: EVM OmniBridge deployToken hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public EVM `deployToken(bytes,MetadataPayload)`` with overlong or adversarial token identifiers and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` derive the same local seed or salt for two remote assets because of hashes a Borsh-encoded metadata payload, checks ECDSA recovery against `nearBridgeDerivedAddress`, deploys a bridge-token proxy, and writes token mappings, violating `one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken`
- Entrypoint: `public EVM `deployToken(bytes,MetadataPayload)``
- Attacker controls: signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
