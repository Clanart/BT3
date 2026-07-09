# Q2642: EVM OmniBridge logMetadata hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public EVM metadata logging entrypoint` with overlong or adversarial token identifiers and make `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` derive the same local seed or salt for two remote assets because of reads metadata from an arbitrary ERC-20-like token, passes it to `logMetadataExtension`, and emits a `LogMetadata` event, violating `metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata`
- Entrypoint: `public EVM metadata logging entrypoint`
- Attacker controls: token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
