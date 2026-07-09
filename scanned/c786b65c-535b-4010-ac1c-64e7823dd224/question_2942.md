# Q2942: EVM deterministic ERC-1155 alias low-half deploy salt aliases another token id through cross-module drift

## Question
Can an unprivileged attacker use `public helper used by ERC-1155 metadata and transfer flows` with control over ERC-1155 token address and token id and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `low-half deploy salt aliases another token id` attack class because computes a synthetic bridge token address as `bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))`, violating `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` and the adjacent token-mapping and asset-identity logic after every branch.
