### Title
`OmniBridgeWormhole::finTransferExtension` Forwards Entire `msg.value` to Wormhole After ETH Already Sent to Recipient, Permanently Blocking Native ETH Finalization - (File: `evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole.finTransferExtension` unconditionally forwards `msg.value` to `_wormhole.publishMessage`. For native ETH transfers (`tokenAddress == address(0)`), `msg.value` must cover both `payload.amount` (sent to the recipient) and the Wormhole fee. After the ETH transfer to the recipient consumes `payload.amount` from the contract's balance, the contract no longer holds enough ETH to forward the full `msg.value` to Wormhole. The call reverts, and because the entire transaction reverts, the nonce is never consumed — meaning the transfer can never be finalized. Native ETH transfers via the Wormhole variant are permanently unclaimable.

---

### Finding Description

In `OmniBridge.finTransfer`, when `payload.tokenAddress == address(0)`, the contract first sends `payload.amount` of ETH to the recipient:

```solidity
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();
```

Then it calls `finTransferExtension(payload)`, which in `OmniBridgeWormhole` does:

```solidity
_wormhole.publishMessage{value: msg.value}(
    wormholeNonce,
    messagePayload,
    _consistencyLevel
);
```

`msg.value` here is the **original full value** sent by the relayer (e.g., `payload.amount + wormholeFee`). After the `.call{value: payload.amount}` in step one, the contract's balance is reduced by `payload.amount`. When `finTransferExtension` then attempts to forward the full `msg.value` to Wormhole, the contract has only `wormholeFee` remaining — insufficient to cover `msg.value`. The transaction reverts.

By contrast, `initTransferExtension` correctly computes a residual `extensionValue = msg.value - amount - nativeFee` and passes only that to `publishMessage{value: value}`, not `msg.value`. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) 

---

### Impact Explanation

Any relayer attempting to finalize a native ETH transfer (NEAR → EVM) via `OmniBridgeWormhole.finTransfer` will always revert. Because the revert unwinds all state changes (nonce mark, ETH send), the nonce is never consumed and the transfer can never be completed by any relayer. The user's ETH locked on the source chain (NEAR) is permanently unclaimable on the EVM destination. This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows**.

---

### Likelihood Explanation

Any user who initiates a native ETH bridge transfer from NEAR to an EVM chain using the Wormhole variant triggers this path. The `tokenAddress == address(0)` branch is the documented native ETH path. No special conditions are required beyond a non-zero transfer amount, which is the normal case. Likelihood is **High**.

---

### Recommendation

In `finTransferExtension`, do not forward `msg.value` to Wormhole. Instead, query `_wormhole.messageFee()` and forward only that amount, or pass a pre-computed fee value (analogous to how `initTransferExtension` receives `value` as a parameter rather than using `msg.value` directly):

```solidity
function finTransferExtension(
    BridgeTypes.TransferMessagePayload memory payload
) internal override {
    // ...build messagePayload...
    uint256 wormholeFee = _wormhole.messageFee();
    _wormhole.publishMessage{value: wormholeFee}(
        wormholeNonce,
        messagePayload,
        _consistencyLevel
    );
    wormholeNonce++;
}
```

The same fix applies to `deployTokenExtension` and `logMetadataExtension`, which also use `{value: msg.value}` and could be affected if their callers ever send ETH for other purposes. [5](#0-4) 

---

### Proof of Concept

1. Deploy `OmniBridgeWormhole` with a real Wormhole contract that charges `messageFee = F`.
2. Register a native ETH token mapping (NEAR → EVM, `tokenAddress = address(0)`).
3. Obtain a valid MPC signature for a transfer of `A` ETH to recipient `R` with `destinationNonce = N`.
4. Call `OmniBridgeWormhole.finTransfer(sig, payload)` with `msg.value = A + F`.
5. Execution path:
   - `completedTransfers[N] = true` ✓
   - `payload.recipient.call{value: A}("")` succeeds, contract balance = `F`
   - `finTransferExtension` calls `_wormhole.publishMessage{value: A + F}(...)` — contract only has `F`, **reverts**
   - Entire transaction reverts; nonce `N` is NOT consumed
6. Retry with any `msg.value` — the same revert occurs for any `A > 0`.
7. The transfer is permanently unclaimable. [6](#0-5) [1](#0-0)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L48-94)
```text
    function deployTokenExtension(
        string memory token,
        address tokenAddress,
        uint8 decimals,
        uint8 originDecimals
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.DeployToken)),
            Borsh.encodeString(token),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            bytes1(decimals),
            bytes1(originDecimals)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }

    function logMetadataExtension(
        address tokenAddress,
        string memory name,
        string memory symbol,
        uint8 decimals
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.LogMetadata)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeString(name),
            Borsh.encodeString(symbol),
            bytes1(decimals)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L118-150)
```text
    function initTransferExtension(
        address sender,
        address tokenAddress,
        uint64 originNonce,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message,
        uint256 value
    ) internal override {
        bytes memory payload = bytes.concat(
            bytes1(uint8(MessageType.InitTransfer)),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(sender),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(tokenAddress),
            Borsh.encodeUint64(originNonce),
            Borsh.encodeUint128(amount),
            Borsh.encodeUint128(fee),
            Borsh.encodeUint128(nativeFee),
            Borsh.encodeString(recipient),
            Borsh.encodeString(message)
        );
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );

        wormholeNonce++;
    }
```

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
