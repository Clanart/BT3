# Q3631: EVM OmniBridge logMetadata truncated seed or salt aliases remote assets at boundary values

## Question
Can an unprivileged attacker trigger `public EVM metadata logging entrypoint` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` violate `metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset` in the `truncated seed or salt aliases remote assets` attack class because reads metadata from an arbitrary ERC-20-like token, passes it to `logMetadataExtension`, and emits a `LogMetadata` event becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata`
- Entrypoint: `public EVM metadata logging entrypoint`
- Attacker controls: token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
