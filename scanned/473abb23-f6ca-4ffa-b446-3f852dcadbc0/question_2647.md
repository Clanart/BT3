# Q2647: EVM OmniBridge onERC1155Received burn debits the wrong logical account

## Question
Can an unprivileged attacker use `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches` so that `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` burns or withholds value from a caller context different from the one the bridge event later attributes, violating `ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received`
- Entrypoint: `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches`
- Attacker controls: the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject.
- Invariant to test: ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event.
