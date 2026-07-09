# Q3227: EVM OmniBridge logMetadata1155 truncated seed or salt aliases remote assets

## Question
Can an unprivileged attacker reach `public EVM metadata logging for ERC-1155 wrappers` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` truncate or hash remote asset identifiers in a way that aliases two deployable assets, violating `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Target low-half salts, 20-byte address truncation, hashed token strings, and fixed-width seed buffers.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Balance manipulation
- Fast validation: Search for seed collisions and assert that distinct remote assets cannot share a local deploy address or mint PDA.
