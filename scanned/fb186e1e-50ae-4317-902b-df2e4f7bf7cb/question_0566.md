# Q566: EVM OmniBridge deployToken canonical token identity collision at boundary values

## Question
Can an unprivileged attacker trigger `public EVM `deployToken(bytes,MetadataPayload)`` with boundary-controlled inputs covering decimal caps, zero values, and normalization edges and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` violate `one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings` in the `canonical token identity collision` attack class because hashes a Borsh-encoded metadata payload, checks ECDSA recovery against `nearBridgeDerivedAddress`, deploys a bridge-token proxy, and writes token mappings becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken`
- Entrypoint: `public EVM `deployToken(bytes,MetadataPayload)``
- Attacker controls: signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Concentrate on decimal caps, zero values, and normalization edges.
- Invariant to test: one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Sweep boundary values for decimal caps, zero values, and normalization edges and assert that the same invariant holds at every edge.
