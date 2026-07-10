### Title
Push-Payment ETH Transfer to Reverting Recipient Permanently Freezes Funds — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.finTransfer` uses a push-payment pattern to deliver native ETH to the recipient. If the recipient is a contract whose fallback reverts, the entire call reverts with `FailedToSendEther`. Because there is no admin-rescue or pull-payment fallback, the ETH remains permanently locked inside the bridge contract with no recovery path for the user.

### Finding Description

In `finTransfer`, when `payload.tokenAddress == address(0)` (native ETH), the bridge pushes ETH directly to the caller-supplied recipient:

```solidity
// OmniBridge.sol line 317-322
if (payload.tokenAddress == address(0)) {
    // slither-disable-next-line arbitrary-send-eth
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

The nonce is marked used on line 287 **before** the transfer:

```solidity
completedTransfers[payload.destinationNonce] = true;  // line 287
``` [2](#0-1) 

If the `.call` fails and `revert FailedToSendEther()` is thrown, the entire transaction reverts — including the nonce assignment — so the nonce is **not** permanently consumed. However, if the recipient is an immutable contract that always reverts on ETH receipt (e.g., a multisig, a smart-contract wallet with ETH rejection logic, or a purposely malicious contract), every subsequent retry of `finTransfer` will also revert. The ETH remains trapped in the bridge contract indefinitely.

The contract has no admin rescue function, no pull-payment withdrawal mechanism, and no way to redirect a failed delivery to an alternative address. The only function that can release ETH from the bridge is `finTransfer` itself, which is gated by the MPC-signed `payload.recipient` field — a field that cannot be changed after the cross-chain message is signed. [3](#0-2) 

The `receive()` function at line 574 allows the contract to accumulate ETH but provides no withdrawal path for stuck funds.

### Impact Explanation

A user who initiates a NEAR-to-EVM native ETH transfer specifying a contract recipient that reverts on ETH receipt will have their funds permanently frozen inside the bridge. The source-chain tokens are already consumed (locked/burned on NEAR), and the destination ETH is inaccessible. There is no on-chain recovery mechanism. This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation

Moderate. Common real-world cases where a contract recipient reverts on ETH receipt include:

- Gnosis Safe multisigs configured to reject direct ETH
- Smart-contract wallets that only accept ETH via specific function calls
- Contracts that were later upgraded to reject ETH
- Intentionally malicious contracts deployed by an attacker to grief themselves or to demonstrate the lock

Any bridge user who specifies such an address as recipient triggers the freeze. The recipient address is user-supplied and is embedded in the MPC-signed payload, so it cannot be corrected after signing.

### Recommendation

1. **Implement a pull-payment (claimable) pattern for native ETH**: instead of pushing ETH to `payload.recipient` in `finTransfer`, credit an internal balance mapping and let the recipient call a separate `claimETH()` function.
2. **Alternatively**, add an admin-accessible rescue function that can redirect a failed delivery to a user-specified recovery address, gated by the original recipient's signature or an admin role.
3. At minimum, emit an event on failed delivery and do not revert the nonce, so that off-chain tooling can detect and surface stuck transfers.

### Proof of Concept

1. Alice holds tokens on NEAR and initiates a NEAR-to-EVM transfer of 1 ETH, specifying `recipient = 0xDeadContract` — a contract whose `fallback()` always reverts.
2. The MPC signs a `TransferMessagePayload` with `tokenAddress = address(0)`, `amount = 1e18`, `recipient = 0xDeadContract`.
3. A relayer calls `finTransfer(signatureData, payload)` on EVM.
4. Line 287 sets `completedTransfers[nonce] = true`.
5. Line 319 executes `0xDeadContract.call{value: 1e18}("")` — the fallback reverts.
6. Line 322 executes `revert FailedToSendEther()` — the entire transaction reverts, including the nonce assignment.
7. Every subsequent retry of `finTransfer` with the same payload hits the same revert.
8. The 1 ETH is permanently locked in the `OmniBridge` contract. Alice's NEAR tokens are already consumed. No recovery path exists. [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-367)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

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

        finTransferExtension(payload);

        emit BridgeTypes.FinTransfer(
            payload.originChain,
            payload.originNonce,
            payload.tokenAddress,
            payload.amount,
            payload.recipient,
            payload.feeRecipient
        );
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```
