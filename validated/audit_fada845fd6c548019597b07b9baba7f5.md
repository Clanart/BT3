### Title
ETH Delivery to Non-Payable Recipient Contract Permanently Freezes Bridged Native Funds - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge.finTransfer`, when the bridged token is native ETH (`tokenAddress == address(0)`), the contract delivers ETH to `payload.recipient` via a raw `call{value}`. If the recipient is a smart contract that lacks a `receive`/`fallback` function or has one that reverts, the delivery permanently fails. Because the recipient address is fixed inside an MPC-signed payload and there is no admin override or alternative delivery path, the bridged ETH becomes permanently unclaimable.

---

### Finding Description

`finTransfer` handles native ETH delivery at lines 317–322:

```solidity
if (payload.tokenAddress == address(0)) {
    // slither-disable-next-line arbitrary-send-eth
    (bool success, ) = payload.recipient.call{value: payload.amount}(
        ""
    );
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

The `payload.recipient` is an arbitrary EVM address embedded in the MPC-signed `TransferMessagePayload` struct: [2](#0-1) 

The nonce guard is set before the ETH transfer:

```solidity
completedTransfers[payload.destinationNonce] = true;
``` [3](#0-2) 

When the `call{value}` fails, the entire transaction reverts (including the nonce assignment). This means the nonce is not consumed and `finTransfer` can be retried — but every retry will produce the same revert because the recipient address is immutably encoded in the signed payload. There is no admin function to redirect delivery to a different address, and no WETH fallback path.

---

### Impact Explanation

A user who bridges native ETH from NEAR/Solana/StarkNet to an EVM contract address that rejects ETH (e.g., a Gnosis Safe without a `receive` function, a DAO treasury, a proxy contract, or any contract whose `receive` reverts) will have their source-chain funds locked/burned while the destination-chain delivery permanently fails. The funds are irrecoverable: the signed payload cannot be altered, and no recovery mechanism exists in the contract.

**Impact: Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows.**

---

### Likelihood Explanation

Smart contract addresses are common bridge recipients: multisigs, DAO treasuries, protocol vaults, and smart contract wallets are all frequently used. Many such contracts do not implement `receive()` or implement it with access controls that cause reverts. A user who specifies such an address when initiating a cross-chain ETH transfer will permanently lose their funds. This requires no privileged access — any unprivileged bridge user can trigger it by specifying their own contract address as recipient.

---

### Recommendation

Apply the same pattern recommended in the original report: if the raw ETH `call` fails, wrap the amount in WETH and deliver WETH to the recipient instead. This is a well-known pattern (used by Uniswap V3, among others) that ensures delivery always succeeds regardless of the recipient's ETH acceptance behavior.

```solidity
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) {
        // Fallback: wrap as WETH and transfer
        IWETH(weth).deposit{value: payload.amount}();
        IERC20(weth).safeTransfer(payload.recipient, payload.amount);
    }
}
```

Alternatively, document that contract addresses must implement `receive()` and validate this on the source chain before locking funds, though this is harder to enforce cross-chain.

---

### Proof of Concept

1. User calls `initTransfer` on the source chain (NEAR/Solana) specifying `recipient = address(EthRejecter)` where `EthRejecter` is:
   ```solidity
   contract EthRejecter {
       receive() external payable { revert("NO ETH"); }
   }
   ```
2. Source-chain funds are locked/burned. MPC signs a `TransferMessagePayload` with `tokenAddress = address(0)`, `recipient = address(EthRejecter)`, `amount = X`.
3. Relayer calls `OmniBridge.finTransfer(signatureData, payload)`.
4. Execution reaches line 319: `payload.recipient.call{value: payload.amount}("")` — the `EthRejecter.receive()` reverts.
5. `finTransfer` reverts with `FailedToSendEther()`. The nonce is not consumed.
6. Every subsequent retry of `finTransfer` with the same signed payload produces the same revert.
7. The user's source-chain funds are permanently lost; the ETH held by the bridge contract is permanently locked against this nonce.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
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
