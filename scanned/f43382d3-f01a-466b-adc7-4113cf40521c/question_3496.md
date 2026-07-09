# Q3496: EVM OmniBridge logMetadata truncated seed or salt aliases remote assets through cross-module drift

## Question
Can an unprivileged attacker use `public EVM metadata logging entrypoint` with control over token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `truncated seed or salt aliases remote assets` attack class because reads metadata from an arbitrary ERC-20-like token, passes it to `logMetadataExtension`, and emits a `LogMetadata` event, violating `metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata`
- Entrypoint: `public EVM metadata logging entrypoint`
- Attacker controls: token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` and the adjacent mint, burn, or custody accounting after every branch.
