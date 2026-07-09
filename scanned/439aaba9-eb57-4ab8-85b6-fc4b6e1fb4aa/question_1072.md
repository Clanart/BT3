# Q1072: EVM deterministic ERC-1155 alias hashed or padded seed collision through cross-module drift

## Question
Can an unprivileged attacker use `public helper used by ERC-1155 metadata and transfer flows` with control over ERC-1155 token address and token id and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` from the adjacent token-mapping and asset-identity logic that shares the same asset, nonce, proof subject, or mapping specifically in the `hashed or padded seed collision` attack class because computes a synthetic bridge token address as `bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))`, violating `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Focus on drift between this module and the adjacent token-mapping and asset-identity logic.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` and the adjacent token-mapping and asset-identity logic after every branch.
