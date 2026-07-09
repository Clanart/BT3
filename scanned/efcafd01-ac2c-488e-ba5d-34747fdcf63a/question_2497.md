# Q2497: EVM OmniBridge onERC1155Received one inbound event spawns multiple outbound obligations at boundary values

## Question
Can an unprivileged attacker trigger `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches` with boundary-controlled inputs covering empty strings, maximal lengths, and malformed encodings and make `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` violate `ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping` in the `one inbound event spawns multiple outbound obligations` attack class because accepts ERC-1155 transfers only when `operator == address(this)` to prevent direct deposits outside the intended bridge flow becomes fragile at those edges?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received`
- Entrypoint: `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches`
- Attacker controls: the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Concentrate on empty strings, maximal lengths, and malformed encodings.
- Invariant to test: ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Sweep boundary values for empty strings, maximal lengths, and malformed encodings and assert that the same invariant holds at every edge.
