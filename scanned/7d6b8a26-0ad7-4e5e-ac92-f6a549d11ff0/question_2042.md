# Q2042: EVM deterministic ERC-1155 alias truncated seed or salt aliases remote assets

## Question
Can an unprivileged attacker reach `public helper used by ERC-1155 metadata and transfer flows` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` truncate or hash remote asset identifiers in a way that aliases two deployable assets, violating `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA.
