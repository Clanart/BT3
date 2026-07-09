# Q1066: EVM OmniBridge logMetadata native versus wrapped registration confusion through cross-module drift

## Question
Can an unprivileged attacker use `public EVM metadata logging entrypoint` with control over token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `native versus wrapped registration confusion` attack class because reads metadata from an arbitrary ERC-20-like token, passes it to `logMetadataExtension`, and emits a `LogMetadata` event, violating `metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata`
- Entrypoint: `public EVM metadata logging entrypoint`
- Attacker controls: token contract address, token-reported `name`, `symbol`, `decimals`, and msg.value for extensions
- Exploit idea: Target vault-existence checks, deployed-token caches, origin-chain inference, and custom-minter registration. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: metadata logging must not let malicious token contracts create bridge-side asset identities or deployment proofs that can later mint or map the wrong asset
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Flip each classification predicate around existing mappings and assert that deployment and later settlement always preserve the same custody model. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata` and the adjacent mint, burn, or custody accounting after every branch.
