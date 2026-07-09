# Q2346: EVM deterministic ERC-1155 alias truncated seed or salt aliases remote assets through cross-module drift

## Question
Can an unprivileged attacker use `public helper used by ERC-1155 metadata and transfer flows` with control over ERC-1155 token address and token id and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `truncated seed or salt aliases remote assets` attack class because computes a synthetic bridge token address as `bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))`, violating `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` and the adjacent token-mapping and asset-identity logic after every branch.
