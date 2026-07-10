### Title
ERC1155 `onERC1155Received` Callback Allows Recipient to Permanently Block Bridge Finalization - (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

### Summary

`OmniBridge.finTransfer` uses `IERC1155.safeTransferFrom` to deliver ERC1155 tokens to the recipient. Per the ERC1155 standard, `safeTransferFrom` mandatorily invokes `onERC1155Received` on any contract recipient. A recipient contract that reverts inside this callback causes the entire `finTransfer` transaction to revert. Because the nonce-marking state change is also reverted, the nonce is never permanently consumed, and the transfer can never be finalized. The ERC1155 tokens remain locked in the bridge forever with no rescue path.

### Finding Description

In `OmniBridge.finTransfer`, the nonce is marked used at line 287 before any external call (correct CEI ordering), but the ERC1155 transfer path at lines 323–330 calls `IERC1155(multiToken.tokenAddress).safeTransferFrom(address(this), payload.recipient, ...)`. The ERC1155 standard mandates that `safeTransferFrom` call `onERC1155Received` on the recipient if it is a contract, and revert if the selector is not returned correctly.

If `payload.recipient` is a contract that:
- does not implement `IERC1155Receiver` (e.g., a multisig, a DAO treasury, a smart contract wallet), or
- intentionally reverts inside `onERC1155Received`

then the `safeTransferFrom` call reverts, which unwinds the entire transaction including the `completedTransfers[payload.destinationNonce] = true` write. The nonce is never permanently consumed. Every subsequent attempt to call `finTransfer` with the same valid MPC-signed payload will also revert. There is no admin rescue function, no alternative finalization path, and no way to redirect the transfer to a different recipient (the recipient is encoded in the MPC-signed payload and cannot be changed without a new signature).

The `OmniBridgeWormhole` variant compounds this: `finTransferExtension` (which publishes the Wormhole acknowledgment back to NEAR) is called after the token transfer at line 357, so the Wormhole message is also never published, leaving the NEAR-side state inconsistent. [1](#0-0) [2](#0-1) [3](#0-2) 

### Impact Explanation

ERC1155 tokens locked in the bridge via `initTransfer1155` are permanently irrecoverable if the designated EVM recipient is a contract that does not correctly implement `onERC1155Received`. The NEAR-side tokens were already burned or locked when the user initiated the transfer. There is no admin escape hatch in `OmniBridge` to redirect or rescue stuck tokens. This matches the **Critical** allowed impact: "Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows." [4](#0-3) 

### Likelihood Explanation

The likelihood is moderate-to-high:

1. **Accidental**: Many widely-used contract wallets (Gnosis Safe prior to ERC1155 support, DAO treasuries, protocol vaults) do not implement `onERC1155Received`. A user who bridges ERC1155 tokens to such an address permanently loses them.
2. **Intentional griefing**: A NEAR-side sender can specify a known-reverting EVM contract as the recipient, permanently locking the ERC1155 tokens in the bridge. While this is self-inflicted for the sender, it can be used to grief a third party if the sender is acting on behalf of another user (e.g., a relayer or aggregator that constructs the recipient address).
3. **No validation**: `finTransfer` performs no check that `payload.recipient` is an EOA or a contract that supports ERC1155 before calling `safeTransferFrom`. [5](#0-4) 

### Recommendation

Apply one or more of the following mitigations:

1. **Use a pull-payment pattern**: Instead of pushing ERC1155 tokens directly to the recipient in `finTransfer`, credit the recipient's claimable balance in a mapping and let them call a separate `claim` function. This decouples the finalization from the delivery and prevents recipient-side reverts from blocking the nonce.

2. **Use `try/catch` with a fallback escrow**: Wrap the `safeTransferFrom` in a `try/catch`. On failure, store the tokens in a per-recipient escrow mapping and mark the nonce as used regardless. Emit a `TransferEscrowed` event so the recipient can claim later.

3. **Validate recipient ERC1155 support**: Before calling `safeTransferFrom`, check that the recipient either is an EOA (`payload.recipient.code.length == 0`) or returns the correct `onERC1155Received` selector via a static call.

### Proof of Concept

1. Alice holds ERC1155 token `T` (tokenId 42) on EVM and calls `initTransfer1155(T, 42, 100, 0, 0, "alice.near", "")`. The bridge locks 100 units of token 42 in the bridge contract. A `LogMetadata1155` event is emitted and the NEAR side registers the deterministic address.

2. Alice later initiates a return transfer from NEAR, specifying `MaliciousContract` as the EVM recipient. The NEAR MPC signs a `TransferMessagePayload` with `tokenAddress = deterministicAddress(T, 42)`, `recipient = MaliciousContract`, `amount = 100`.

3. `MaliciousContract` implements `onERC1155Received` as:
   ```solidity
   function onERC1155Received(...) external pure returns (bytes4) {
       revert("blocked");
   }
   ```

4. A relayer calls `OmniBridge.finTransfer(signature, payload)`. Execution reaches line 324: `IERC1155(T).safeTransferFrom(bridge, MaliciousContract, 42, 100, "")`. The ERC1155 contract calls `MaliciousContract.onERC1155Received(...)`, which reverts. The entire transaction reverts. `completedTransfers[nonce]` is reset to `false`.

5. Every subsequent call to `finTransfer` with the same valid payload reverts identically. The 100 units of token 42 remain in the bridge contract permanently. The NEAR-side tokens are already burned. [6](#0-5) [7](#0-6)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L439-464)
```text
    function initTransfer1155(
        address tokenAddress,
        uint256 tokenId,
        uint128 amount,
        uint128 fee,
        uint128 nativeFee,
        string calldata recipient,
        string calldata message
    ) external payable whenNotPaused(PAUSED_INIT_TRANSFER) {
        currentOriginNonce += 1;
        if (fee >= amount) {
            revert InvalidFee();
        }

        address deterministicToken = deriveDeterministicAddress(
            tokenAddress,
            tokenId
        );

        IERC1155(tokenAddress).safeTransferFrom(
            msg.sender,
            address(this),
            tokenId,
            amount,
            ""
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L576-584)
```text
    function deriveDeterministicAddress(
        address tokenAddress,
        uint256 tokenId
    ) public pure returns (address) {
        return
            address(
                bytes20(keccak256(abi.encodePacked(tokenAddress, tokenId)))
            );
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
