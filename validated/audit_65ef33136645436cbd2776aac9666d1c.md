### Title
ERC1155 `safeTransferFrom()` in `finTransfer()` Permanently Locks Tokens When Recipient Contract Lacks `onERC1155Received()` - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer()` uses `IERC1155.safeTransferFrom()` to deliver bridged ERC1155 tokens to the recipient. Per EIP-1155, if the recipient is a smart contract that does not implement `IERC1155Receiver`, the call unconditionally reverts. Because there is no recovery path and the ERC1155 tokens are already held by the bridge (deposited via `initTransfer1155()`), the tokens become permanently irrecoverable.

---

### Finding Description

In `finTransfer()`, the ERC1155 delivery branch calls:

```solidity
IERC1155(multiToken.tokenAddress).safeTransferFrom(
    address(this),
    payload.recipient,
    multiToken.tokenId,
    payload.amount,
    ""
);
``` [1](#0-0) 

The EIP-1155 standard mandates that if `payload.recipient` is a contract, it must implement `IERC1155Receiver` and return `bytes4(keccak256("onERC1155Received(address,address,uint256,uint256,bytes)"))`. If it does not, the OpenZeppelin ERC1155 implementation reverts with `ERC1155InvalidReceiver`. This is not a conditional check — it is a hard revert with no fallback.

The ERC1155 tokens that are to be delivered were previously deposited into the bridge by the user via `initTransfer1155()`:

```solidity
IERC1155(tokenAddress).safeTransferFrom(
    msg.sender,
    address(this),
    tokenId,
    amount,
    ""
);
``` [2](#0-1) 

Once deposited, the bridge holds the tokens. If `finTransfer()` always reverts for a given recipient, there is no admin rescue function, no refund path, and no alternative delivery mechanism in the contract. The tokens are permanently locked in the bridge.

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

Any ERC1155 tokens bridged to a smart contract recipient that does not implement `IERC1155Receiver` are permanently locked in the `OmniBridge` contract. The source-chain deposit is irreversible (the NEAR-side transfer has already been finalized), and the EVM-side delivery can never succeed. There is no admin recovery function in the contract.

---

### Likelihood Explanation

**Medium-High.** Many widely-used smart contract wallets, multisigs (e.g., Gnosis Safe without ERC1155 module), DAO treasuries, and DeFi vaults do not implement `IERC1155Receiver`. A user who bridges ERC1155 tokens to any such contract — a common and expected use case — will permanently lose their tokens. No special attacker knowledge is required; the loss can be triggered accidentally by any unprivileged user specifying a contract address as the EVM recipient.

---

### Recommendation

Since ERC1155 does not define a non-safe `transferFrom()` (unlike ERC721), the fix cannot simply swap to `transferFrom()`. Instead:

1. **Wrap the delivery in a `try/catch`** and store failed deliveries in a mapping, allowing the recipient to later pull their tokens via a separate `claimFailed()` function.
2. **Alternatively**, before calling `safeTransferFrom()`, check whether `payload.recipient` is a contract and whether it supports `IERC1155Receiver` via ERC165 `supportsInterface()`. If not, revert early with a clear error before the nonce is consumed, so the transfer can be retried with a corrected recipient.

---

### Proof of Concept

1. User holds ERC1155 token (tokenAddress=`T`, tokenId=`42`, amount=`100`).
2. User calls `initTransfer1155(T, 42, 100, 0, 0, "near_recipient", "")` on `OmniBridge`. The bridge receives the 100 tokens via `safeTransferFrom` at line 458. [2](#0-1) 
3. NEAR processes the cross-chain message and produces a signed `TransferMessagePayload` with `recipient = address(VaultContract)`, where `VaultContract` is a smart contract that does not implement `IERC1155Receiver`.
4. A relayer calls `finTransfer(sig, payload)`. Execution reaches line 324. [3](#0-2) 
5. `IERC1155(T).safeTransferFrom(bridge, VaultContract, 42, 100, "")` reverts because `VaultContract` returns no valid `onERC1155Received` selector.
6. The entire transaction reverts. The nonce is not consumed, but every future attempt to call `finTransfer()` for this payload will also revert for the same reason.
7. The 100 ERC1155 tokens remain locked in `OmniBridge` with no recovery path. [4](#0-3)

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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L458-464)
```text
        IERC1155(tokenAddress).safeTransferFrom(
            msg.sender,
            address(this),
            tokenId,
            amount,
            ""
        );
```
