# Q2498: EVM deterministic ERC-1155 alias truncated seed or salt aliases remote assets at boundary values

## Question
Can an unprivileged attacker trigger `public helper used by ERC-1155 metadata and transfer flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` violate `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows` in the `truncated seed or salt aliases remote assets` attack class because computes a synthetic bridge token address as `bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))` becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
