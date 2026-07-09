# Q740: EVM deterministic ERC-1155 alias hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public helper used by ERC-1155 metadata and transfer flows` with overlong or adversarial token identifiers and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` derive the same local seed or salt for two remote assets because of computes a synthetic bridge token address as `bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))`, violating `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
