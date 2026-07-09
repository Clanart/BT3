# Q2941: EVM OmniBridge onERC1155Received burn debits the wrong logical account through cross-module drift

## Question
Can an unprivileged attacker use `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches` with control over the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract and desynchronize `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` from the adjacent mint, burn, or custody accounting that shares the same asset, nonce, proof subject, or mapping specifically in the `burn debits the wrong logical account` attack class because accepts ERC-1155 transfers only when `operator == address(this)` to prevent direct deposits outside the intended bridge flow, violating `ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received`
- Entrypoint: `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches`
- Attacker controls: the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Focus on drift between this module and the adjacent mint, burn, or custody accounting.
- Invariant to test: ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Also assert cross-module consistency between `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` and the adjacent mint, burn, or custody accounting after every branch.
