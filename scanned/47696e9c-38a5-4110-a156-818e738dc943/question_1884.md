# Q1884: EVM deterministic ERC-1155 alias address alias collapses distinct bridge subjects at boundary values

## Question
Can an unprivileged attacker trigger `public helper used by ERC-1155 metadata and transfer flows` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress` violate `distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows` in the `address alias collapses distinct bridge subjects` attack class because computes a synthetic bridge token address as `bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))` becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::deriveDeterministicAddress`
- Entrypoint: `public helper used by ERC-1155 metadata and transfer flows`
- Attacker controls: ERC-1155 token address and token id
- Exploit idea: Attack mixed-case hex, leading-zero, base58, or padded-address forms across chain adapters. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: distinct ERC-1155 assets must never collide onto one synthetic bridge token identity or share state across bridge flows
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Round-trip equivalent-looking addresses through every parser and assert injective mapping into local bridge identities. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
