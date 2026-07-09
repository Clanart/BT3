# Q2648: EVM deterministic ERC-1155 alias low-half deploy salt aliases another token id

## Question
Can an unprivileged attacker reach `public helper used by ERC-1155 metadata and transfer flows` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` deploy or reference another token’s address because the contract address salt uses only part of a larger hash, violating `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Target Starknet deployment where the full token-id hash is the map key but only the low portion becomes the deploy salt.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for colliding low-half salts and assert that address derivation remains unique for all deployable token ids.
