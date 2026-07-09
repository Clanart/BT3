# Q3225: EVM OmniBridge deployToken endianness mismatch forks authenticated bytes

## Question
Can an unprivileged attacker exploit `public EVM `deployToken(bytes,MetadataPayload)`` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken` serializes or parses numeric fields in an order that differs from another chain’s implementation, violating `one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deployToken`
- Entrypoint: `public EVM `deployToken(bytes,MetadataPayload)``
- Attacker controls: signature bytes, token string, name, symbol, decimals, msg.value, and timing versus other deploys
- Exploit idea: Target Borsh helpers and hand-built payload encoders across Rust, Solidity, and Cairo.
- Invariant to test: one signed metadata message must deploy exactly one canonical bridge token and must not be replayable across token ids, chains, or branch-specific encodings
- Expected Immunefi impact: Cryptographic flaws
- Fast validation: Cross-generate payloads on every implementation and assert byte-for-byte equality for every field combination that can reach signatures or proofs.
