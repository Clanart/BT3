# Q2187: EVM OmniBridge deployToken fake bridge-controlled token accepted as canonical via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM `deployToken(bytes,MetadataPayload)`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` ends up accepting two inconsistent interpretations of the same economic event specifically around `fake bridge-controlled token accepted as canonical` under hashes a Borsh-encoded metadata payload, checks ECDSA recovery against `nearBridgeDerivedAddress`, deploys a bridge-token proxy, and writes token mappings, violating `one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken`
- Entrypoint: `public EVM `deployToken(bytes,MetadataPayload)``
- Attacker controls: signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys
- Exploit idea: Target checks that only inspect mint authority, owner, or one mapping row without proving the full asset identity. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Construct plausible fake bridge-controlled assets and assert that deployment, settlement, and forwarding reject them unless they are the canonical mapping for that remote asset. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
