# Q1555: EVM OmniBridge deployToken native versus wrapped registration confusion via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM `deployToken(bytes,MetadataPayload)`` and then replay or reorder later bind, deploy, or metadata-consumption step so that `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` ends up accepting two inconsistent interpretations of the same economic event specifically around `native versus wrapped registration confusion` under hashes a Borsh-encoded metadata payload, checks ECDSA recovery against `nearBridgeDerivedAddress`, deploys a bridge-token proxy, and writes token mappings, violating `one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken`
- Entrypoint: `public EVM `deployToken(bytes,MetadataPayload)``
- Attacker controls: signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
