# Q3892: EVM OmniBridge logMetadata1155 ABI version switch changes metadata identity via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `public EVM metadata logging for ERC-1155 wrappers` and then replay or reorder later bind, deploy, or metadata-consumption step so that `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` ends up accepting two inconsistent interpretations of the same economic event specifically around `ABI version switch changes metadata identity` under maps a `(tokenAddress, tokenId)` pair to a deterministic synthetic address before publishing metadata for multi-token bridging, violating `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Target old-style versus new-style token metadata return shapes and zero-length special cases. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Return ambiguous ABI payloads and assert that the bridge either rejects them or derives the exact intended metadata once. Then replay or reorder later bind, deploy, or metadata-consumption step and assert that the bridge still exposes only one valid economic outcome.
