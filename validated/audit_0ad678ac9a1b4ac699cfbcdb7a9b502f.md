The EVM contracts use `.call{value: ...}` (not `.transfer`), so the exact opcode issue from the report doesn't apply. However, there is a closely analogous permanent-freeze vulnerability in `finTransfer` when delivering native ETH to a contract recipient that cannot accept ETH.

### Title
Native ETH Delivery in `finTransfer` Permanently Freezes Funds When Recipient Cannot Accept ETH — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `OmniBridge.finTransfer`, when the bridged asset is native ETH (`tokenAddress == address(0)`), the contract delivers ETH to `payload.recipient` via a low-level `.call`. If the recipient is a contract that reverts on ETH receipt (no `receive`/`fallback`, or one that explicitly reverts), every `finTransfer` attempt for that nonce will revert. Because the source-chain funds are already irrevocably locked and no on-chain refund path exists, the user's ETH is permanently frozen.

---

### Finding Description

`finTransfer` handles native ETH delivery at lines 317–322:

```solidity
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

When `success` is `false`, the function reverts with `FailedToSendEther()`. Because Solidity reverts unwind all state changes, the `completedTransfers[payload.destinationNonce] = true` write at line 287 is also rolled back. [2](#0-1) 

This means the nonce is not permanently consumed — but the `finTransfer` call will revert on every future attempt for the same payload, because the recipient contract will always reject ETH. The signed payload is fixed by the MPC: `payload.recipient` is Borsh-encoded and covered by the ECDSA signature verified at line 311. [3](#0-2) 

There is no on-chain mechanism to redirect the delivery to a different address or issue a refund. Recovery requires the MPC/bridge operators to produce a new signed payload with a different recipient — a trusted, off-chain action with no protocol guarantee.

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

The user's ETH on the source chain (NEAR or another EVM chain) is already locked or burned at `initTransfer` time. If `finTransfer` can never succeed on the destination chain, those funds are irrecoverably frozen. The bridge contract holds the destination ETH (deposited via `receive() external payable {}`), but it can never be delivered to the intended recipient without operator intervention. [4](#0-3) 

---

### Likelihood Explanation

**Medium.**

Any user who specifies a contract address as their bridge recipient that lacks a `receive`/`fallback` function, or whose fallback explicitly reverts (e.g., certain multisig wallets, proxy contracts, or contracts under construction), will trigger this freeze. This is a realistic scenario: users bridging ETH to a smart contract wallet or a protocol contract are common in DeFi. The user controls `recipient` at `initTransfer` time on the source chain, making this reachable by any unprivileged bridge user.

---

### Recommendation

1. **Do not revert on failed ETH delivery.** Instead of reverting, record the unclaimed amount in a mapping and allow the recipient (or a designated address) to pull it later:

```solidity
mapping(address => uint256) public unclaimedEth;

// in finTransfer, ETH branch:
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) {
    unclaimedEth[payload.recipient] += payload.amount;
}

// new function:
function claimEth() external nonReentrant {
    uint256 amount = unclaimedEth[msg.sender];
    require(amount > 0, "nothing to claim");
    unclaimedEth[msg.sender] = 0;
    (bool ok, ) = msg.sender.call{value: amount}("");
    require(ok, "claim failed");
}
```

2. Mark `completedTransfers[payload.destinationNonce] = true` only after all asset delivery logic succeeds (or use the pull pattern above so the nonce is always consumed on first call).

3. Add a `nonReentrant` modifier to `finTransfer` if the pull pattern is not used, since `.call` forwards all gas and the recipient could re-enter.

---

### Proof of Concept

1. User on NEAR calls `initTransfer` specifying a destination EVM address that is a contract with no `receive` function (e.g., a Gnosis Safe with a custom fallback that reverts, or a contract mid-deployment).
2. Source-chain ETH is locked in the NEAR bridge contract.
3. MPC produces a signed `TransferMessagePayload` with `tokenAddress = address(0)` and `recipient = <contract>`.
4. Relayer calls `OmniBridge.finTransfer(signatureData, payload)`.
5. The `.call{value: payload.amount}("")` to the contract fails (recipient reverts).
6. `FailedToSendEther()` is thrown; the entire transaction reverts.
7. Steps 4–6 repeat indefinitely — every attempt reverts.
8. The user's ETH on NEAR is permanently locked; the destination ETH sits in the bridge contract forever without operator intervention.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
```text
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L309-312)
```text
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```
