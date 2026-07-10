### Title
Native ETH `finTransfer` to Non-Payable Contract Recipient Permanently Locks Cross-Chain User Funds - (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

`OmniBridge.finTransfer` attempts a low-level ETH transfer to a recipient address that is embedded in an MPC-signed payload. If the recipient is a contract without a `payable` fallback/receive function, the call always reverts. Because the recipient is fixed in the signed payload and no refund path exists in the EVM contracts, the corresponding NEAR-side tokens become permanently locked.

---

### Finding Description

In `OmniBridge.finTransfer`, when `payload.tokenAddress == address(0)` (native ETH transfer), the function performs:

```solidity
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();
``` [1](#0-0) 

The nonce is marked used **before** this call:

```solidity
completedTransfers[payload.destinationNonce] = true;
``` [2](#0-1) 

When the ETH transfer fails, the entire transaction reverts, which also reverts the `completedTransfers` state change. The nonce is therefore not permanently consumed — but this provides no relief, because:

1. The `recipient` address is embedded in the MPC-signed `TransferMessagePayload` and cannot be altered without a new MPC signature.
2. Every subsequent call to `finTransfer` with the same payload will fail identically.
3. There is no refund function, no alternative settlement path, and no admin escape hatch in the EVM contracts that would allow recovery of the locked NEAR-side tokens. [3](#0-2) 

The `TransferMessagePayload` struct shows that `recipient` is a plain `address` field, set once by the originating NEAR-side message: [4](#0-3) 

No pre-validation is performed anywhere in `finTransfer` to verify that `payload.recipient` can accept ETH before the NEAR-side tokens are already committed.

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

When a user initiates a NEAR→EVM transfer of native ETH and specifies a non-payable contract as the EVM recipient:

- The NEAR-side tokens are locked/burned at initiation time.
- The EVM `finTransfer` will revert on every attempt.
- The ETH held in the bridge contract is inaccessible to the user.
- No recovery mechanism exists in the EVM contracts.

The user's funds are irrecoverably frozen with no protocol-level remedy.

---

### Likelihood Explanation

**Medium-High.** Smart contract wallets (e.g., Gnosis Safe, multisigs) are common EVM recipients and frequently lack a `payable` fallback. A user bridging from NEAR to EVM who specifies such a wallet as the recipient — a completely normal and expected use case — will trigger this condition. The protocol performs no validation of the recipient's ability to receive ETH at any point in the flow.

---

### Recommendation

1. **Pre-validate the recipient on the EVM side** inside `finTransfer` before marking the transfer as in-flight: perform a zero-value probe call to `payload.recipient` and revert early with a clear error if it fails, before the NEAR-side commitment is irreversible.
2. **Alternatively, implement a pull-payment pattern**: instead of pushing ETH to `payload.recipient` directly, credit the amount to a claimable balance mapping and let the recipient withdraw. This eliminates the failure mode entirely.
3. **Add a protocol-level refund path**: if EVM finalization fails permanently, the MPC/NEAR bridge should be able to issue a signed refund message that unlocks the NEAR-side tokens.

---

### Proof of Concept

```
1. Attacker/user deploys or uses an existing non-payable contract C on EVM
   (e.g., a Gnosis Safe with no ETH receive function, or any contract A {}).

2. User initiates a NEAR→EVM transfer of native ETH, specifying address(C) as recipient.
   NEAR-side tokens are locked/burned.

3. MPC signs a TransferMessagePayload with:
     tokenAddress = address(0)
     amount       = X ETH
     recipient    = address(C)   ← fixed, cannot be changed

4. Relayer calls OmniBridge.finTransfer(signatureData, payload).
   - completedTransfers[nonce] = true  (line 287)
   - Signature verified OK
   - payload.recipient.call{value: X}("") → FAILS (C has no payable fallback)
   - revert FailedToSendEther()         → entire tx reverts, nonce un-set

5. Every subsequent finTransfer attempt with the same payload fails identically.

6. NEAR-side tokens remain permanently locked.
   X ETH remains in the OmniBridge contract, inaccessible to the user.
   No refund function exists. Funds are frozen forever.
```

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

**File:** evm/src/omni-bridge/contracts/BridgeTypes.sol (L5-14)
```text
    struct TransferMessagePayload {
        uint64 destinationNonce;
        uint8 originChain;
        uint64 originNonce;
        address tokenAddress;
        uint128 amount;
        address recipient;
        string feeRecipient;
        bytes message;
    }
```
