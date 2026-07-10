### Title
ETH Mistakenly Sent with ERC20-Based `finTransfer` Calls Is Permanently Frozen — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`finTransfer` is declared `payable` and handles both native ETH and ERC20/bridge-token finalization in a single function. When `payload.tokenAddress != address(0)`, the function performs only token operations and never uses `msg.value`. There is no guard requiring `msg.value == 0` for non-ETH paths, and no admin ETH-rescue function exists. Any ETH sent alongside an ERC20-based `finTransfer` call is permanently frozen in the contract.

---

### Finding Description

`OmniBridge.finTransfer` is a single `payable` entry point that finalizes cross-chain transfers for **all** token types:

```solidity
// OmniBridge.sol line 279-282
function finTransfer(
    bytes calldata signatureData,
    BridgeTypes.TransferMessagePayload calldata payload
) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
```

Inside the function, ETH is only consumed on the `payload.tokenAddress == address(0)` branch:

```solidity
// lines 317-322
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
} else if (multiToken.tokenAddress != address(0)) {
    IERC1155(...).safeTransferFrom(...);   // no ETH used
} else if (customMinters[...] != address(0)) {
    ICustomMinter(...).mint(...);           // no ETH used
} else if (isBridgeToken[...]) {
    IBridgeToken(...).mint(...);            // no ETH used
} else {
    IERC20(...).safeTransfer(...);          // no ETH used
}
```

For every non-ETH branch, `msg.value` is silently accepted and never forwarded, refunded, or consumed. The base `finTransferExtension` is a no-op virtual:

```solidity
// lines 369-371
function finTransferExtension(
    BridgeTypes.TransferMessagePayload memory payload
) internal virtual {}
```

There is no `require(msg.value == 0)` guard on any non-ETH path, and a search of the entire EVM contract set confirms **no ETH withdrawal or rescue function exists**. The only ETH egress in the contract is the `finTransfer` ETH-path itself (line 319). ETH deposited via any other path is irrecoverable.

---

### Impact Explanation

Any ETH sent alongside a `finTransfer` call where `payload.tokenAddress != address(0)` is permanently frozen in the `OmniBridge` contract. There is no admin sweep, no `rescueETH`, and no other ETH egress path. This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows**.

---

### Likelihood Explanation

`finTransfer` is a permissionless function callable by any relayer or user. The same function signature is used for both ETH and ERC20 finalization. A user self-relaying their own ERC20 bridge transfer, a frontend that pre-populates `msg.value` from a prior ETH transfer, or a relayer script that mistakenly attaches ETH can all trigger this path. The probability is non-negligible — identical to the M-05 scenario where the same function serves both ETH and ERC20 orders.

---

### Recommendation

Add an explicit `msg.value == 0` guard for all non-ETH finalization paths, mirroring the fix recommended in M-05:

```solidity
if (payload.tokenAddress == address(0)) {
    require(msg.value >= payload.amount, "insufficient ETH");
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
} else {
    require(msg.value == 0, "non-zero ETH value for ERC20 transfer");
    // ... existing ERC20/bridge-token branches
}
```

Alternatively, remove `payable` from `finTransfer` entirely and add a separate `finTransferETH` entry point, so the compiler enforces zero ETH for all ERC20 paths.

---

### Proof of Concept

1. A valid MPC-signed `TransferMessagePayload` exists for an ERC20 bridge token (e.g., `payload.tokenAddress = 0xSomeERC20`, `payload.amount = 1000e18`).
2. A user (or relayer) calls:
   ```solidity
   omniBridge.finTransfer{value: 1 ether}(signatureData, payload);
   ```
3. The signature check passes (line 311). `completedTransfers[payload.destinationNonce]` is set to `true` (line 287). The ERC20 mint/transfer executes successfully (lines 337-354). `finTransferExtension` is a no-op (lines 369-371).
4. The call returns successfully. The 1 ETH is now held by the `OmniBridge` contract.
5. No function in the contract can retrieve this ETH — the only ETH egress is the `address(0)` branch of `finTransfer` (line 319), which requires a valid MPC signature for an ETH transfer payload. The frozen ETH is irrecoverable. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-282)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L317-355)
```text
        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
        } else if (customMinters[payload.tokenAddress] != address(0)) {
            ICustomMinter(customMinters[payload.tokenAddress]).mint(
                payload.tokenAddress,
                payload.recipient,
                payload.amount
            );
        } else if (isBridgeToken[payload.tokenAddress]) {
            if (payload.message.length == 0) {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount
                );
            } else {
                IBridgeToken(payload.tokenAddress).mint(
                    payload.recipient,
                    payload.amount,
                    payload.message
                );
            }
        } else {
            IERC20(payload.tokenAddress).safeTransfer(
                payload.recipient,
                payload.amount
            );
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L369-371)
```text
    function finTransferExtension(
        BridgeTypes.TransferMessagePayload memory payload
    ) internal virtual {}
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```
