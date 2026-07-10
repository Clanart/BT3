### Title
ERC1155 `safeTransferFrom` Callback in `finTransfer` Permanently Locks Bridged Funds When Recipient Contract Rejects `onERC1155Received` — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge.finTransfer`, when finalizing an ERC1155 token transfer from NEAR to EVM, `IERC1155.safeTransferFrom` is called to deliver tokens to the recipient. This triggers the `onERC1155Received` callback on the recipient contract. If the recipient contract rejects the callback (returns an invalid selector or reverts), the entire `finTransfer` call reverts. Because the tokens are already locked/burned on the NEAR side and there is no on-chain recovery path, the user's funds are permanently frozen.

---

### Finding Description

In `OmniBridge.sol`, `finTransfer` handles finalization of cross-chain transfers from NEAR to EVM. For ERC1155 tokens, the delivery path is:

```solidity
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

Per the ERC1155 standard, `safeTransferFrom` calls `onERC1155Received` on the recipient if it is a contract. If the recipient contract:
- Does not implement `IERC1155Receiver`,
- Returns an invalid selector from `onERC1155Received`, or
- Reverts inside `onERC1155Received`,

then `safeTransferFrom` reverts, causing the entire `finTransfer` transaction to revert.

The destination nonce is marked used at line 287 **before** the token transfer:

```solidity
completedTransfers[payload.destinationNonce] = true;
```

Because the transaction reverts atomically, the nonce is **not** permanently consumed. However, the tokens are already locked/burned on the NEAR side via `initTransfer`. There is no on-chain mechanism to:
1. Redirect the transfer to a different recipient, or
2. Refund the tokens on NEAR.

The MPC signature covers the recipient address directly in the Borsh-encoded payload:

```solidity
Borsh.encodeAddress(payload.recipient),
```

A new signature with a different recipient would require off-chain coordination with the MPC signer — not a standard protocol mechanism. The NEAR side has no awareness of EVM transaction failures and will never issue a refund autonomously.

The same push-to-untrusted-address pattern also exists for native ETH transfers:

```solidity
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();
```

If the recipient is a contract that explicitly rejects ETH, the same permanent lock occurs.

---

### Impact Explanation

**Critical — Permanent freezing of user funds in bridge flows.**

ERC1155 tokens locked/burned on NEAR cannot be recovered if the EVM recipient contract rejects the `onERC1155Received` callback. Every subsequent relay attempt with the same signed payload will revert identically. The user's funds are irrecoverably locked in the bridge with no user-facing on-chain recovery path. Recovery requires DAO-level intervention via `transfer_token_as_dao`, which is not a standard protocol mechanism and is not guaranteed.

---

### Likelihood Explanation

**Medium.** Any user who specifies a contract address as recipient that does not properly implement `IERC1155Receiver` will have their funds permanently locked. This is a realistic scenario: multisigs (e.g., Gnosis Safe without ERC1155 module), DeFi protocols, and smart contract wallets commonly do not implement `onERC1155Received`. The user initiating the transfer on NEAR may not be aware that their EVM-side contract address lacks this interface. No privileged access or colluding party is required — the user's own choice of recipient address is sufficient to trigger the lock.

---

### Recommendation

Replace the push delivery with a **pull-over-push** pattern for ERC1155 tokens. Store the pending claim on-chain and let the recipient pull the tokens:

```solidity
// Add storage for pending ERC1155 claims
mapping(address recipient => mapping(address token => mapping(uint256 id => uint256 amount)))
    public pendingERC1155Claims;

// In finTransfer, replace safeTransferFrom with:
pendingERC1155Claims[payload.recipient][multiToken.tokenAddress][multiToken.tokenId] += payload.amount;

// Add a claim function:
function claimERC1155(address tokenAddress, uint256 tokenId) external {
    uint256 amount = pendingERC1155Claims[msg.sender][tokenAddress][tokenId];
    require(amount > 0, "NoPendingClaim");
    pendingERC1155Claims[msg.sender][tokenAddress][tokenId] = 0;
    IERC1155(tokenAddress).safeTransferFrom(address(this), msg.sender, tokenId, amount, "");
}
```

This mirrors the mitigation confirmed in H-06: approve/store rather than push, ensuring finalization always succeeds regardless of recipient contract behavior.

---

### Proof of Concept

**Attack path:**

1. Alice bridges ERC1155 tokens from NEAR to EVM via `initTransfer1155`. Tokens are locked in the bridge on NEAR.
2. Alice specifies `MaliciousRecipient` (below) as the EVM recipient.
3. The MPC signer issues a valid `finTransfer` signature covering `payload.recipient = address(MaliciousRecipient)`.
4. Relayer calls `OmniBridge.finTransfer(signatureData, payload)`.
5. Inside `finTransfer`, `IERC1155.safeTransferFrom(address(this), MaliciousRecipient, tokenId, amount, "")` is called.
6. The ERC1155 contract calls `MaliciousRecipient.onERC1155Received(...)`, which returns `bytes4(0xdeadbeef)`.
7. The ERC1155 standard reverts because the returned selector does not match `IERC1155Receiver.onERC1155Received.selector`.
8. The entire `finTransfer` transaction reverts. The nonce is not consumed.
9. Every subsequent relay attempt with the same payload reverts identically.
10. Alice's tokens are permanently locked on NEAR with no user-facing recovery path.

```solidity
contract MaliciousRecipient {
    function onERC1155Received(
        address, address, uint256, uint256, bytes calldata
    ) external pure returns (bytes4) {
        // Returns invalid selector — causes safeTransferFrom to revert
        return bytes4(0xdeadbeef);
    }
}
``` [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L295-298)
```text
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L323-330)
```text
        } else if (multiToken.tokenAddress != address(0)) {
            IERC1155(multiToken.tokenAddress).safeTransferFrom(
                address(this),
                payload.recipient,
                multiToken.tokenId,
                payload.amount,
                ""
            );
```
