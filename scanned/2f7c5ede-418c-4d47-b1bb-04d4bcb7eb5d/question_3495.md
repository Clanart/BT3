# Q3495: EVM OmniBridge deployToken endianness mismatch forks authenticated bytes through cross-module drift

## Question
Can an unprivileged attacker use `public EVM `deployToken(bytes,MetadataPayload)`` with control over signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `endianness mismatch forks authenticated bytes` attack class because hashes a Borsh-encoded metadata payload, checks ECDSA recovery against `nearBridgeDerivedAddress`, deploys a bridge-token proxy, and writes token mappings, violating `one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken`
- Entrypoint: `public EVM `deployToken(bytes,MetadataPayload)``
- Attacker controls: signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys
- Exploit idea: Target Borsh helpers and hand-built payload encoders across Rust, Solidity, and Cairo. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Cross-generate payloads on every implementation and assert byte-for-byte equality for every field combination that can reach signatures or proofs. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` and the adjacent token-mapping and asset-identity logic after every branch.
