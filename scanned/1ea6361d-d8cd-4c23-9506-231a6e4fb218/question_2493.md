# Q2493: EVM OmniBridge logMetadata1155 hashed or padded seed collision at boundary values

## Question
Can an unprivileged attacker trigger `public EVM metadata logging for ERC-1155 wrappers` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` violate `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity` in the `hashed or padded seed collision` attack class because maps a `(tokenAddress, tokenId)` pair to a deterministic synthetic address before publishing metadata for multi-token bridging becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
