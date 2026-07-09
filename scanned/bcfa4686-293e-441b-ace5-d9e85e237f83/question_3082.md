# Q3082: EVM OmniBridge deployToken hashed or padded seed collision at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `deployToken(bytes,MetadataPayload)`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` violate `one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings` in the `hashed or padded seed collision` attack class because hashes a Borsh-encoded metadata payload, checks ECDSA recovery against `nearBridgeDerivedAddress`, deploys a bridge-token proxy, and writes token mappings becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken`
- Entrypoint: `public EVM `deployToken(bytes,MetadataPayload)``
- Attacker controls: signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
