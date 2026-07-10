### Title
Native ETH `finTransfer` Always Reverts in `OmniBridgeWormhole` Due to Incorrect Fee Forwarding - (File: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

### Summary

`OmniBridgeWormhole.finTransferExtension()` forwards the full `msg.value` to `IWormhole.publishMessage()`, but when the transfer involves native ETH (`payload.tokenAddress == address(0)`), `finTransfer()` has already spent `payload.amount` of that `msg.value` sending ETH to the recipient. The contract's remaining balance is `msg.value - payload.amount`, so the Wormhole publish call always reverts for native ETH finalizations, permanently freezing those user funds.

### Finding Description

In `OmniBridge.finTransfer()`, when `payload.tokenAddress == address(0)`, the contract sends `payload.amount` of native ETH to the recipient: [1](#0-0) 

After this transfer, the contract's ETH balance is reduced by `payload.amount`. The function then calls `finTransferExtension(payload)` with no value argument: [2](#0-1) 

In `OmniBridgeWormhole`, `finTransferExtension` forwards the **full original `msg.value`** to Wormhole: [3](#0-2) 

Since `msg.value` in an internal call still refers to the original external call's value, and the contract has already spent `payload.amount` of it, the Wormhole call attempts to send more ETH than the contract holds. The EVM reverts the entire transaction.

Contrast this with `initTransfer()`, which correctly computes the residual value before passing it to `initTransferExtension`: [4](#0-3) [5](#0-4) 

`initTransfer` correctly uses `extensionValue = msg.value - amount - nativeFee` and passes it as `value` to `publishMessage`. `finTransfer` performs no such subtraction.

### Impact Explanation

Any native ETH transfer initiated on NEAR and destined for EVM via `OmniBridgeWormhole` can never be finalized. Every call to `finTransfer` with `payload.tokenAddress == address(0)` will revert at the Wormhole publish step, regardless of how much ETH the caller provides. The user's bridged ETH is permanently unclaimable on the EVM side — a permanent freeze of user funds in the bridge flow.

This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

Any user who initiates a native ETH bridge transfer from NEAR to EVM triggers this path. The relayer who calls `finTransfer` has no way to supply a `msg.value` that satisfies both the ETH payout to the recipient and the Wormhole fee simultaneously, because `finTransferExtension` always uses the full `msg.value`. The bug is deterministic and affects 100% of native ETH `finTransfer` calls on `OmniBridgeWormhole`.

### Recommendation

Compute the residual value available for the Wormhole fee after the ETH payout, mirroring the pattern used in `initTransfer`. Pass this residual through `finTransferExtension`:

```solidity
// In OmniBridge.finTransfer():
uint256 extensionValue = msg.value;
if (payload.tokenAddress == address(0)) {
    extensionValue = msg.value - payload.amount;
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
} else if (...) {
    ...
}
finTransferExtension(payload, extensionValue);
```

```solidity
// In OmniBridgeWormhole.finTransferExtension():
function finTransferExtension(
    BridgeTypes.TransferMessagePayload memory payload,
    uint256 extensionValue
) internal override {
    ...
    _wormhole.publishMessage{value: extensionValue}(...);
}
```

### Proof of Concept

1. User bridges 1 ETH from NEAR to EVM via the NEAR omni-bridge contract.
2. NEAR MPC signs a `TransferMessagePayload` with `tokenAddress = address(0)`, `amount = 1 ETH`, `recipient = userEVMAddress`.
3. Relayer calls `OmniBridgeWormhole.finTransfer{value: 1 ETH + wormholeFee}(sig, payload)`.
4. `finTransfer` sends `1 ETH` to `userEVMAddress` — contract balance is now `wormholeFee`.
5. `finTransferExtension` calls `_wormhole.publishMessage{value: msg.value}(...)` where `msg.value = 1 ETH + wormholeFee`.
6. EVM reverts: contract only holds `wormholeFee` but tries to send `1 ETH + wormholeFee`.
7. The entire transaction reverts. The user receives nothing. No Wormhole message is published. The transfer cannot be retried with a different value because the signature is fixed. The user's ETH is permanently locked on NEAR with no valid finalization path on EVM. [6](#0-5) [7](#0-6)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L386-393)
```text
        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L96-116)
```text
    function finTransferExtension(
        BridgeTypes.TransferMessagePayload memory payload
    ) internal override {
        bytes memory messagePayload = bytes.concat(
            bytes1(uint8(MessageType.FinTransfer)),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            Borsh.encodeString(payload.feeRecipient)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            messagePayload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L143-147)
```text
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );
```
