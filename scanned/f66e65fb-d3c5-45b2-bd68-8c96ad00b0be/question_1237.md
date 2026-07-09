# Q1237: EVM OmniBridge onERC1155Received native versus wrapped branch switch at boundary values

## Question
Can an unprivileged attacker trigger `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` violate `ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping` in the `native versus wrapped branch switch` attack class because accepts ERC-1155 transfers only when `operator == address(this)` to prevent direct deposits outside the intended bridge flow becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received`
- Entrypoint: `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches`
- Attacker controls: the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract
- Exploit idea: Target zero-address, deployed-token, custom-minter, native-vault, or bridge-token branch predicates. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping
- Expected Immunefi impact: Theft or permanent freezing of funds
- Fast validation: Force each branch predicate to flip around callbacks or mapping writes and assert that the same source asset can never produce two incompatible custody models. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
