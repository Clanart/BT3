# Q1401: EVM deterministic ERC-1155 alias address alias collapses distinct bridge subjects

## Question
Can an unprivileged attacker exploit `public helper used by ERC-1155 metadata and transfer flows` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` normalizes two distinct chain-specific addresses into the same local representation, violating `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities.
