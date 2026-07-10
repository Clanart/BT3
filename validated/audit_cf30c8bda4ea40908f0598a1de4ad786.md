### Title
Unguarded Native ETH Delivery in `finTransfer` Permanently Locks Funds When Recipient Contract Rejects ETH — (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary
`OmniBridge.finTransfer()` delivers native ETH to the recipient via an unconstrained low-level call. If the recipient is a contract whose `receive()` reverts (or is absent), the call fails, the entire transaction reverts, and the ETH deposited into the bridge on the source side is permanently unclaimable. No admin rescue path or fallback-recipient mechanism exists.

---

### Finding Description
When `payload.tokenAddress == address(0)`, `finTransfer` executes:

```solidity
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();
``` [1](#0-0) 

The nonce is marked used at line 287 before this call, but because `revert FailedToSendEther()` unwinds the entire transaction, that state write is also rolled back. [2](#0-1) 

The result is:
- The `destinationNonce` is **never consumed** in storage.
- The ETH deposited into the bridge (via a prior `initTransfer` on the source chain) **remains locked** in the contract.
- `finTransfer` will revert on every retry attempt for that signed payload.
- The contract has no admin withdrawal, no rescue function, and no fallback-recipient override. [3](#0-2) 

The only `receive()` in the contract accepts ETH in but provides no path to release it outside of a successful `finTransfer`.

In the Wormhole variant, `finTransferExtension` publishes the acknowledgement message **after** the ETH delivery call: [4](#0-3) [5](#0-4) 

Because the transaction reverts before `finTransferExtension` runs, the NEAR side never receives the acknowledgement Wormhole VAA. Without that signal, the NEAR bridge cannot issue a refund or re-route the transfer. The funds are stranded with no on-chain or cross-chain recovery path.

---

### Impact Explanation
**Critical — Permanent, irrecoverable lock of user funds in the bridge vault.**

ETH locked in the bridge via `initTransfer` on the source chain can never be released if the MPC-signed `payload.recipient` is a contract that cannot accept plain ETH. There is no admin escape hatch, no fallback address field, and no cross-chain refund signal. The ETH is permanently frozen in `OmniBridge`.

---

### Likelihood Explanation
**Low-to-Medium.** The scenario is realistic:
- Smart-contract wallets (Gnosis Safe, account-abstraction wallets) commonly lack a plain `receive()`.
- A user bridging from NEAR to EVM may specify their EVM smart-contract wallet address without knowing it rejects plain ETH.
- The source-chain `initTransfer` call gives no feedback about EVM-side deliverability.
- No validation of recipient ETH-receivability exists anywhere in the bridge flow.

---

### Recommendation
1. **Remove the hard revert on failed ETH delivery.** Instead of `revert FailedToSendEther()`, record the failed delivery in a claimable mapping so the recipient (or an alternate address they control) can pull the funds later.
2. **Add a `nonReentrant` guard** to `finTransfer` since the ETH call forwards all remaining gas with no cap.
3. **Add an admin rescue function** (time-locked or governance-gated) for ETH that has been stuck for longer than a configurable timeout.
4. **Optionally accept a `fallbackRecipient`** field in the transfer payload so the MPC can re-sign with an alternate address if the primary recipient is unreachable.

---

### Proof of Concept
1. Alice holds 1 ETH on NEAR and calls `initTransfer(address(0), 1 ETH, ...)` specifying her EVM Gnosis Safe address (which has no `receive()`).
2. The NEAR bridge locks Alice's funds and the MPC signs a `TransferMessagePayload` with `tokenAddress = 0x0`, `amount = 1 ETH`, `recipient = <GnosisSafe>`.
3. A relayer calls `OmniBridge.finTransfer(sig, payload)` on EVM.
4. Line 287 sets `completedTransfers[nonce] = true`.
5. Line 319 executes `GnosisSafe.call{value: 1 ETH}("")` — the Safe has no `receive()`, so it reverts.
6. `revert FailedToSendEther()` unwinds the entire transaction; `completedTransfers[nonce]` is rolled back.
7. Every subsequent relay attempt for this payload hits the same revert.
8. No Wormhole VAA is ever published; NEAR never learns the transfer failed.
9. Alice's 1 ETH is permanently locked in `OmniBridge` with no recovery path.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-288)
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

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L357-357)
```text
        finTransferExtension(payload);
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
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
