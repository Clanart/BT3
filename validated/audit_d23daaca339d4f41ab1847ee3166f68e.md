### Title
ERC20/ERC1155 `finTransfer` Does Not Revert on Non-Zero `msg.value`, Permanently Freezing Caller ETH — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` is declared `external payable`. When the payload specifies a non-native token (`payload.tokenAddress != address(0)`), the function performs an ERC20 mint/transfer/burn but never validates that `msg.value == 0`. The base `OmniBridge` contract's `finTransferExtension` is a virtual no-op, so any ETH attached to such a call is silently absorbed by the contract and permanently frozen — there is no ETH withdrawal or rescue function anywhere in the contract.

---

### Finding Description

`finTransfer` handles both native ETH transfers (`payload.tokenAddress == address(0)`) and ERC20/ERC1155/bridge-token transfers (`payload.tokenAddress != address(0)`). The function signature is `external payable`, meaning it unconditionally accepts ETH from callers.

For the non-native branch (lines 323–355), the code mints, transfers, or burns the ERC20/ERC1155 token but makes no use of `msg.value`. After the token operation, it calls `finTransferExtension(payload)`, which in the base `OmniBridge` contract is a virtual no-op (lines 369–371). The ETH is therefore silently retained by the contract.

A grep across all EVM source files confirms there is no `withdraw`, `rescueETH`, `recoverETH`, or `emergencyWithdraw` function anywhere in the contract. The only ETH-receiving surface is `receive() external payable {}` (line 574), which also provides no recovery path.

Contrast this with `initTransfer` for ERC20 tokens: the base `OmniBridgeWormhole.initTransferExtension` correctly propagates `extensionValue` to Wormhole, and the base `OmniBridge.initTransferExtension` explicitly reverts if `value != 0` (lines 503–505). No equivalent guard exists for `finTransfer`.

The same class of issue also affects:
- `logMetadata` (line 224) — `external payable`, `logMetadataExtension` is a no-op in the base contract
- `logMetadata1155` (line 234) — same
- `deployToken` (line 135) — `external payable`, `deployTokenExtension` is a no-op in the base contract

---

### Impact Explanation

Any ETH sent alongside a `finTransfer` call for a non-native token is permanently irrecoverable. There is no admin withdrawal function, no sweep mechanism, and no refund path. The funds are locked in the bridge contract forever.

This matches the allowed impact: **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

`finTransfer` is callable by any unprivileged address that possesses a valid MPC-signed `TransferMessagePayload`. Relayers and users who finalize cross-chain transfers are the natural callers. Mistakenly attaching ETH is realistic because:

1. The same contract handles native ETH transfers (where `msg.value` is required), creating confusion.
2. Wallet UIs and scripts may default to including a small ETH value.
3. Copy-paste errors between native and ERC20 finalization calls are common.

The signature requirement does not prevent this: the signature covers the payload fields, not `msg.value`. Any holder of a valid signed payload can trigger the freeze.

---

### Recommendation

Add a `msg.value == 0` guard in the non-native branch of `finTransfer`, or add a top-level check when `payload.tokenAddress != address(0)`:

```solidity
function finTransfer(
    bytes calldata signatureData,
    BridgeTypes.TransferMessagePayload calldata payload
) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
    // Reject ETH for non-native token finalization
    if (payload.tokenAddress != address(0) && msg.value != 0) {
        revert InvalidValue();
    }
    // ... rest of function
}
```

Apply the same guard to `logMetadata`, `logMetadata1155`, and `deployToken` unless ETH is intentionally required by the extension (e.g., Wormhole fee), in which case validate `msg.value == _wormhole.messageFee()` exactly.

---

### Proof of Concept

1. A valid MPC-signed `TransferMessagePayload` exists for an ERC20 token transfer (e.g., USDC from NEAR to EVM).
2. A relayer calls:
   ```solidity
   omniBridge.finTransfer{value: 1 ether}(signatureData, payload);
   ```
3. The function passes signature verification, mints the ERC20 to `payload.recipient`, calls the no-op `finTransferExtension`, and returns successfully.
4. The 1 ETH is now held by the `OmniBridge` contract.
5. No admin function, no `withdraw`, no `rescueETH` exists — the ETH is permanently frozen.

**Relevant lines:**

`finTransfer` declared `payable` with no `msg.value` guard for non-native tokens: [1](#0-0) 

Non-native branch performs ERC20 operations with no ETH accounting: [2](#0-1) 

`finTransferExtension` is a virtual no-op in the base contract — ETH is silently absorbed: [3](#0-2) 

`initTransferExtension` correctly reverts on non-zero value (showing the guard pattern exists but was not applied to `finTransfer`): [4](#0-3) 

No ETH recovery function exists anywhere in the EVM source: [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-282)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L323-355)
```text
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L502-505)
```text
    ) internal virtual {
        if (value != 0) {
            revert InvalidValue();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```
