# Q2037: EVM OmniBridge logMetadata1155 hashed or padded seed collision

## Question
Can an unprivileged attacker reach `public EVM metadata logging for ERC-1155 wrappers` with overlong or adversarial token identifiers and make `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` derive the same local seed or salt for two remote assets because of maps a `(tokenAddress, tokenId)` pair to a deterministic synthetic address before publishing metadata for multi-token bridging, violating `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Target hashed token strings, low-half salts, and deterministic-address truncation.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search the seed space for collisions and assert that every derivation function preserves uniqueness of remote asset identity.
