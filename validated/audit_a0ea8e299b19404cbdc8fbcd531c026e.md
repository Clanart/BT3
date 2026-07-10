### Title
Permanent Freezing of Bridged Native ETH When Recipient Contract Lacks `receive()` — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge.finTransfer`, when the bridged asset is native ETH (`payload.tokenAddress == address(0)`), the contract delivers ETH to `payload.recipient` via a low-level `.call`. If the recipient is a contract without a `receive()` or `fallback()` function, the call fails and the entire transaction reverts. Because the recipient address is immutably encoded in the MPC-signed payload, there is no mechanism to redirect the ETH, permanently locking the user's funds.

---

### Finding Description

In `finTransfer`, the native-ETH delivery branch is:

```solidity
if (payload.tokenAddress == address(0)) {
    // slither-disable-next-line arbitrary-send-eth
    (bool success, ) = payload.recipient.call{value: payload.amount}(
        ""
    );
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

`payload.recipient` is an `address` field in `TransferMessagePayload`, set by the user on the NEAR side when initiating the cross-chain transfer. [2](#0-1) 

The entire payload — including `recipient` — is signed by the MPC-derived key and verified before any state change:

```solidity
if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
    revert InvalidSignature();
}
``` [3](#0-2) 

Once the NEAR-side transfer is finalized (tokens burned/locked on NEAR), the signed payload is fixed. If `payload.recipient` is a contract without a `receive()` function, every call to `finTransfer` with that nonce will revert with `FailedToSendEther()`. The nonce is not permanently consumed (the revert rolls back `completedTransfers[payload.destinationNonce] = true`), but the recipient cannot be changed — so no valid `finTransfer` call can ever succeed for that transfer.

---

### Impact Explanation

**Critical — Permanent irrecoverable lock of user funds.**

- The user's tokens are burned or locked on NEAR when the origin-side transfer is finalized.
- The ETH held in `OmniBridge` (locked from the original EVM→NEAR direction) can never be delivered to the specified recipient.
- There is no admin rescue function, no fallback recipient, and no pull-payment mechanism in the contract.
- The ETH remains permanently stranded in `OmniBridge`.

---

### Likelihood Explanation

**Medium.** Users routinely specify smart contract addresses as bridge recipients: multisig wallets (e.g., Gnosis Safe variants), protocol treasury contracts, DAO vaults, and smart contract wallets. Many such contracts do not implement a plain `receive()` function. A user who specifies such an address on the NEAR side — a fully unprivileged action — triggers the freeze with no warning and no recovery path.

---

### Recommendation

Implement a pull-payment (escrow) pattern for native ETH delivery:

1. Instead of pushing ETH directly to `payload.recipient` in `finTransfer`, record the claimable amount in a mapping (e.g., `pendingWithdrawals[recipient] += amount`).
2. Expose a separate `claimNativeETH()` function that lets the recipient pull their ETH.

Alternatively, if push semantics must be preserved, fall back to wrapping the ETH as WETH and transferring the ERC-20 token to the recipient when the raw ETH push fails. This ensures funds are never permanently locked regardless of the recipient's contract code.

---

### Proof of Concept

1. **On NEAR:** User calls the NEAR bridge to transfer native ETH back to EVM, specifying as recipient a Solidity contract address `0xDeadBeef...` that has no `receive()` or `fallback()` function (e.g., a simple storage contract).
2. **NEAR side finalizes:** The user's wNEAR/native token is burned; the MPC signs a `TransferMessagePayload` with `tokenAddress = address(0)`, `recipient = 0xDeadBeef...`, `amount = X`.
3. **Relayer calls `finTransfer`** on EVM with the signed payload.
4. Execution reaches:
   ```solidity
   (bool success, ) = payload.recipient.call{value: payload.amount}("");
   if (!success) revert FailedToSendEther();
   ``` [4](#0-3) 
5. `0xDeadBeef...` has no `receive()`, so `success == false`. The transaction reverts.
6. The relayer retries — same result every time. The signed payload cannot be altered. The ETH is permanently locked in `OmniBridge`. The user's NEAR-side tokens are already gone.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L311-313)
```text
        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L317-322)
```text
        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
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
