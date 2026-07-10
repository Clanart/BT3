### Title
Native ETH Delivery Revert in `finTransfer` Permanently Freezes User Funds — (`File: evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge.finTransfer()`, when the destination token is native ETH (`payload.tokenAddress == address(0)`), the contract attempts to push ETH to `payload.recipient` via `.call{value: payload.amount}("")`. If that call fails (e.g., recipient is a contract with no `receive`/`fallback`, or one whose fallback consumes too much gas), the function immediately reverts with `FailedToSendEther()`. Because the `completedTransfers[payload.destinationNonce] = true` write at line 287 is part of the same transaction, the revert rolls it back. The nonce is never consumed, so the transfer can never be finalized. The user's NEAR-side tokens are already burned/locked, and no recovery path exists in the EVM contract, permanently freezing the bridged value.

---

### Finding Description

`finTransfer` marks the destination nonce as used at line 287, then attempts to deliver assets. For native ETH transfers the delivery path is:

```solidity
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();
``` [1](#0-0) 

The `revert FailedToSendEther()` unwinds the entire transaction, including the `completedTransfers` write: [2](#0-1) 

Because the recipient address is embedded in the MPC-signed `TransferMessagePayload` (line 298), no relayer can substitute a different recipient. Every subsequent call to `finTransfer` with the same signed payload will attempt the same failing ETH push and revert again. The nonce is never durably consumed, the ETH stays locked inside the bridge, and the user has no on-chain mechanism to redirect or reclaim the funds. [3](#0-2) 

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.**

A user who bridges native ETH from NEAR to an EVM chain and specifies a contract address as recipient (e.g., a multisig, a DeFi vault, or any contract whose `receive` function reverts or exceeds the gas forwarded by a plain `.call`) will have their NEAR-side tokens burned while the corresponding ETH on the EVM side becomes permanently undeliverable. The bridge holds the ETH but can never release it to the intended recipient, and there is no fallback claim or redirect function in the contract.

---

### Likelihood Explanation

Contract addresses are common bridge recipients: multisigs (Gnosis Safe), smart-contract wallets, yield vaults, and protocol treasuries are all standard targets. Any of these that lack a `receive` function, or whose `receive` function reverts, or whose fallback logic exceeds the gas available in a plain ETH call, will trigger this freeze. The condition is reachable by any unprivileged user who simply specifies such an address as the `recipient` field when initiating a transfer on the NEAR side.

---

### Recommendation

Replace the hard revert on failed ETH delivery with a pull-payment (claimable balance) pattern:

1. If the `.call` fails, do **not** revert. Instead, record the owed amount in a `mapping(address => uint256) public claimable` storage variable and keep `completedTransfers[payload.destinationNonce] = true` (already written before the transfer attempt).
2. Expose a `claim()` function that lets the original recipient withdraw their claimable balance.

This ensures the nonce is durably consumed (preventing replay), the ETH is accounted for, and the recipient can retrieve funds once they fix their contract or use an EOA.

```solidity
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) {
        claimable[payload.recipient] += payload.amount;
        emit NativeTransferFailed(payload.recipient, payload.amount);
    }
}
``` [1](#0-0) 

---

### Proof of Concept

1. **Source chain (NEAR):** User calls the NEAR bridge to transfer 1 ETH to EVM, specifying `recipient = address(NoReceiveContract)` — a deployed contract with no `receive` or `fallback` function.
2. NEAR burns the user's wrapped ETH and the MPC signs a `TransferMessagePayload` with `tokenAddress = address(0)`, `amount = 1 ETH`, `recipient = address(NoReceiveContract)`.
3. **Destination chain (EVM):** Relayer calls `OmniBridge.finTransfer(signatureData, payload)`.
4. Line 287 writes `completedTransfers[nonce] = true`.
5. Line 319 executes `NoReceiveContract.call{value: 1 ETH}("")` → returns `success = false`.
6. Line 322 executes `revert FailedToSendEther()` → entire transaction reverts, including the line-287 write.
7. Relayer retries indefinitely; every attempt reverts identically.
8. **Result:** User's NEAR tokens are burned. 1 ETH sits in `OmniBridge` forever. No claim function exists. Funds are permanently frozen. [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-322)
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
```
