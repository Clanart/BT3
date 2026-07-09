# Q1067: EVM OmniBridge logMetadata1155 malicious metadata manufactures a bridge identity through cross-module drift

## Question
Can an unprivileged attacker use `public EVM metadata logging for ERC-1155 wrappers` with control over ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `malicious metadata manufactures a bridge identity` attack class because maps a `(tokenAddress, tokenId)` pair to a deterministic synthetic address before publishing metadata for multi-token bridging, violating `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Exploit arbitrary token metadata calls, old/new ABI switching, or malformed strings in metadata proofs. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Publish or prove pathological metadata values and assert that downstream deployment and mapping logic still binds to the right remote asset and decimals. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` and the adjacent token-mapping and asset-identity logic after every branch.
