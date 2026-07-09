# Q1396: EVM OmniBridge logMetadata1155 remote publication drifts from local deployment state

## Question
Can an unprivileged attacker exploit `public EVM metadata logging for ERC-1155 wrappers` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` publishes a deploy or metadata message that no longer matches local token state because of maps a `(tokenAddress, tokenId)` pair to a deterministic synthetic address before publishing metadata for multi-token bridging, violating `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Focus on message publication before/after nonce increments, mapping writes, or external deploy steps.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Permanent freezing of funds
- Fast validation: Fail each external step independently and assert that every published message corresponds to exactly one deployed and bindable local token.
