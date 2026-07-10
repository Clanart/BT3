### Title
ERC1155 Token Delivery via `safeTransferFrom` to Non-Receiver Contract Permanently Locks User Funds - (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` delivers ERC1155 tokens to the recipient using `IERC1155.safeTransferFrom`, which requires the recipient contract to implement `IERC1155Receiver`. If the recipient is a smart contract that does not implement this interface, every `finTransfer` call for that signed payload will revert. Because the recipient address is cryptographically bound inside the MPC-signed payload, it cannot be changed, and the user's tokens locked on NEAR have no recovery path.

---

### Finding Description

In `OmniBridge.finTransfer`, when the destination token is an ERC1155 multi-token, the bridge delivers it via:

```solidity
// OmniBridge.sol lines 323-330
} else if (multiToken.tokenAddress != address(0)) {
    IERC1155(multiToken.tokenAddress).safeTransferFrom(
        address(this),
        payload.recipient,
        multiToken.tokenId,
        payload.amount,
        ""
    );
}
```

Per the ERC1155 standard, `safeTransferFrom` calls `onERC1155Received` on the recipient if it is a contract. If the recipient contract does not implement `IERC1155Receiver`, the call reverts with `"ERC1155: transfer to non ERC1155Receiver implementer"`.

The `completedTransfers[payload.destinationNonce] = true` flag is set at line 287, **before** the transfer. However, because the revert unwinds the entire transaction, the nonce is not consumed and the call can be retried. The problem is that the `payload.recipient` field is part of the Borsh-encoded message that the MPC network signs:

```solidity
// OmniBridge.sol lines 289-313
bytes memory borshEncoded = bytes.concat(
    ...
    Borsh.encodeAddress(payload.recipient),
    ...
);
bytes32 hashed = keccak256(borshEncoded);
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
```

The recipient is immutably bound to the signature. No alternative recipient can be substituted without invalidating the MPC signature. Every retry with the same signed payload will hit the same revert. The user's source-chain tokens remain locked on NEAR with no cancel or refund mechanism exposed.

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.**

A user who bridges ERC1155 tokens from NEAR to EVM and specifies a smart contract recipient that does not implement `IERC1155Receiver` (e.g., a multisig, a DAO treasury, a DeFi vault, or any contract deployed before ERC1155 existed) will have their tokens permanently locked. The signed payload cannot be altered, `finTransfer` will always revert for that payload, and there is no on-chain cancel path visible in the NEAR bridge contract to release the locked tokens.

---

### Likelihood Explanation

ERC1155 bridging is a supported, documented feature (`initTransfer1155` / `finTransfer`). Smart contract recipients are a normal use case — DAOs, multisigs, and protocol vaults routinely receive tokens. Many widely-deployed contracts (Gnosis Safe versions prior to 1.3.0, older Compound/Aave contracts, custom vaults) do not implement `IERC1155Receiver`. A user who bridges ERC1155 tokens to any such address triggers the permanent lock with no warning or revert at initiation time.

---

### Recommendation

Replace `safeTransferFrom` with the non-safe variant `transferFrom` for ERC1155 delivery in `finTransfer`, or add a fallback that catches the revert and stores the tokens for manual claim:

```solidity
// Option A: use transferFrom (no callback check)
IERC1155(multiToken.tokenAddress).transferFrom(
    address(this),
    payload.recipient,
    multiToken.tokenId,
    payload.amount,
    ""
);

// Option B: try/catch with claimable escrow
try IERC1155(multiToken.tokenAddress).safeTransferFrom(...) {
} catch {
    // store in a claimable mapping keyed by destinationNonce
    pendingERC1155Claims[payload.destinationNonce] = ...;
}
```

Option B preserves the safety check while preventing permanent lock.

---

### Proof of Concept

1. User calls `initTransfer1155` on EVM (or equivalent on NEAR) to bridge ERC1155 token ID `42` of contract `0xABC` to recipient `0xDAO` (a DAO contract without `onERC1155Received`).
2. Tokens are locked in `OmniBridge` on EVM (or on NEAR side). MPC signs a `TransferMessagePayload` with `recipient = 0xDAO`.
3. Relayer calls `finTransfer(signatureData, payload)`.
4. Signature validates. `completedTransfers[nonce] = true` is set.
5. `IERC1155(0xABC).safeTransferFrom(bridge, 0xDAO, 42, amount, "")` is called.
6. `0xDAO` has no `onERC1155Received` → ERC1155 reverts → entire tx reverts → nonce not consumed.
7. Every subsequent `finTransfer` call with the same signed payload hits step 5→6 again.
8. No alternative signature can be obtained (recipient is bound). No cancel exists.
9. User's tokens are permanently locked. [1](#0-0) [2](#0-1)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-330)
```text
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
