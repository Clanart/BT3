# Q2345: EVM OmniBridge onERC1155Received one inbound event spawns multiple outbound obligations through cross-module drift

## Question
Can an unprivileged attacker use `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches` with control over the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `one inbound event spawns multiple outbound obligations` attack class because accepts ERC-1155 transfers only when `operator == address(this)` to prevent direct deposits outside the intended bridge flow, violating `ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received`
- Entrypoint: `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches`
- Attacker controls: the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract
- Exploit idea: Focus on forward-to-other-chain branches and fast-transfer substitution where an inbound event becomes a new pending transfer. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping
- Expected Immunefi impact: Cross-chain replay attacks enabling double-spending
- Fast validation: Track value and replay state across both the inbound leg and the forwarded leg and assert that one source event cannot increase total outstanding claims. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` and the adjacent mint, burn, or custody accounting after every branch.
