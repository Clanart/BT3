### Title
ERC1155 `safeTransferFrom` to user-controlled recipient in `finTransfer` permanently locks bridged tokens — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` uses `IERC1155.safeTransferFrom` to deliver ERC1155 tokens to `payload.recipient`. Because `safeTransferFrom` invokes `onERC1155Received` on the recipient contract, any recipient contract that does not implement the callback (or deliberately reverts it) causes `finTransfer` to revert unconditionally. The MPC signature commits to the exact recipient address, so no alternative delivery is possible, and the ERC1155 tokens locked on the source chain are permanently irrecoverable.

---

### Finding Description

In `finTransfer`, after signature verification, the ERC1155 branch executes:

```solidity
IERC1155(multiToken.tokenAddress).safeTransferFrom(
    address(this),
    payload.recipient,   // user-controlled
    multiToken.tokenId,
    payload.amount,
    ""
);
``` [1](#0-0) 

Per EIP-1155, `safeTransferFrom` calls `onERC1155Received` on the recipient and reverts if the selector is not returned. `payload.recipient` is the address the user specified when calling `initTransfer1155` on the source chain. If that address is a contract that lacks or reverts in `onERC1155Received`, every call to `finTransfer` for that transfer will revert.

Because the entire transaction reverts, `completedTransfers[payload.destinationNonce]` is also rolled back: [2](#0-1) 

So the nonce is never consumed and the transfer can be retried — but every retry will revert for the same reason. The MPC signature encodes `payload.recipient` in the signed Borsh blob: [3](#0-2) 

No valid signature for a different recipient can be produced without a new MPC signing round, which requires a new source-chain `initTransfer1155`. The original ERC1155 tokens are already locked in the source bridge from the `initTransfer1155` call: [4](#0-3) 

There is no admin rescue, no `transferFrom` fallback, and no mechanism to redirect the delivery to a different address. The tokens are permanently frozen.

The same structural issue exists in the native-ETH branch of `finTransfer`, where `FailedToSendEther` is reverted if the recipient contract rejects the ETH call: [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

The ERC1155 tokens deposited into the bridge via `initTransfer1155` are locked in the source-chain `OmniBridge` contract. Because `finTransfer` can never succeed for the affected transfer, those tokens can never be released or returned. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**High.** The recipient address is freely chosen by the user at `initTransfer1155` time. A user may legitimately specify a multisig, DAO treasury, or smart-wallet contract that does not implement `onERC1155Received`. A malicious actor can also deliberately target another user's funds by front-running or social-engineering them into specifying such an address. No privileged access is required.

---

### Recommendation

Replace `safeTransferFrom` with `transferFrom` (which does not invoke the receiver callback) in the `finTransfer` ERC1155 delivery path:

```solidity
// Before
IERC1155(multiToken.tokenAddress).safeTransferFrom(
    address(this), payload.recipient, multiToken.tokenId, payload.amount, ""
);

// After
IERC1155(multiToken.tokenAddress).transferFrom(
    address(this), payload.recipient, multiToken.tokenId, payload.amount, ""
);
```

Alternatively, wrap the `safeTransferFrom` in a try/catch and, on failure, store the tokens in a claimable escrow keyed by `(destinationNonce, recipient)` so the recipient can pull them later once their contract is fixed.

For the native-ETH branch, replace the hard revert on `!success` with a similar pull-payment pattern.

---

### Proof of Concept

1. Alice holds ERC1155 token `T` on chain A and calls `initTransfer1155(T, id, amount, 0, 0, "alice.near", "")`. The bridge locks `amount` of `T`.
2. The MPC signs a `TransferMessagePayload` with `recipient = BadContract` (a contract on chain B that has no `onERC1155Received` implementation, e.g. a plain multisig).
3. A relayer calls `finTransfer(sig, payload)` on chain B.
4. Execution reaches line 324; `IERC1155.safeTransferFrom` calls `BadContract.onERC1155Received` → reverts with no return value → entire transaction reverts.
5. `completedTransfers[nonce]` is rolled back; the relayer retries — same result every time.
6. Alice's `amount` of token `T` is permanently locked in chain A's `OmniBridge` with no recovery path. [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-330)
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
