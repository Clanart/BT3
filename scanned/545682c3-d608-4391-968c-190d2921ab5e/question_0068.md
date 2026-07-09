# Q68: EVM OmniBridge onERC1155Received recipient or message ambiguity

## Question
Can an unprivileged attacker supply attacker-controlled recipient or message data through `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches` and make `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received` encode or parse it differently than downstream chains expect via accepts ERC-1155 transfers only when `operator == address(this)` to prevent direct deposits outside the intended bridge flow, violating `ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping`?

## Target
- File/function: `evm/src/omni-bridge/contracts/OmniBridge.sol::onERC1155Received`
- Entrypoint: `ERC-1155 hook reached during public `initTransfer1155` and `finTransfer` branches`
- Attacker controls: the operator, token address, token id, amount, and calldata supplied by the ERC-1155 contract
- Exploit idea: Exploit non-canonical string, ByteArray, hex, or account-id forms to make one source-side intent resolve to a different destination-side recipient or message.
- Invariant to test: ERC-1155 custody must never be reachable through a path that bypasses nonce tracking, event emission, or token-identity mapping
- Expected Immunefi impact: Unauthorized transaction
- Fast validation: Cross-check source-side serialization against every downstream parser and assert that equivalent-looking inputs cannot resolve to distinct destination accounts or app messages.
