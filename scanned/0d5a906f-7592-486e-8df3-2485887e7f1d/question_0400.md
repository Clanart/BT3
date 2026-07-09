# Q400: EVM OmniBridge logMetadata1155 canonical token identity collision through cross-module drift

## Question
Can an unprivileged attacker use `public EVM metadata logging for ERC-1155 wrappers` with control over ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `canonical token identity collision` attack class because maps a `(tokenAddress, tokenId)` pair to a deterministic synthetic address before publishing metadata for multi-token bridging, violating `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Target hashed token ids, deterministic synthetic addresses, PDA seeds, and address-to-token maps. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for collisions and alias conditions and assert that two distinct remote assets cannot share one local token identity or mapping row. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` and the adjacent token-mapping and asset-identity logic after every branch.
