# Q2643: EVM OmniBridge logMetadata1155 address alias collapses distinct bridge subjects

## Question
Can an unprivileged attacker exploit `public EVM metadata logging for ERC-1155 wrappers` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155` normalizes two distinct chain-specific addresses into the same local representation, violating `the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::logMetadata1155`
- Entrypoint: `public EVM metadata logging for ERC-1155 wrappers`
- Attacker controls: ERC-1155 contract address, token id, deterministic-token alias, and msg.value for extensions
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters.
- Invariant to test: the deterministic alias for an ERC-1155 asset must be collision-resistant and must never point two distinct underlying assets at one bridge identity
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities.
