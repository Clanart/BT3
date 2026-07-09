# Q2794: EVM OmniBridge onERC1155Received burn debits the wrong logical account via reordered second step

## Question
Can an unprivileged attacker first create one valid bridge state through `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches` and then replay or reorder the complementary outbound or inbound bridge leg so that `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` ends up accepting two inconsistent interpretations of the same economic event specifically around `burn debits the wrong logical account` under accepts ERC-1155 transfers only when `operator == address(this)` to prevent direct deposits outside the intended bridge flow, violating `ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received`
- Entrypoint: `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches`
- Attacker controls: the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract
- Exploit idea: Target burns keyed to predecessor account, owner, or controller context rather than an explicit subject. Then chain it with a reordered or duplicated complementary bridge step.
- Invariant to test: ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Manipulate caller/proxy layouts and assert that the debited balance always belongs to the asset owner represented in the bridge event. Then replay or reorder the complementary outbound or inbound bridge leg and assert that the bridge still exposes only one valid economic outcome.
