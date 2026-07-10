### Title
`finTransferExtension` Forwards Full `msg.value` to Wormhole After Already Disbursing Native ETH to Recipient, Causing Permanent Revert for Native ETH Settlements — (`File: evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole.finTransferExtension` calls `_wormhole.publishMessage{value: msg.value}(...)` using the full `msg.value` as the Wormhole fee. However, when the transfer is for native ETH (`payload.tokenAddress == address(0)`), `OmniBridge.finTransfer` has already disbursed `payload.amount` ETH to the recipient before invoking `finTransferExtension`. The contract's remaining ETH balance is therefore `msg.value - payload.amount`, which is less than `msg.value`. The Wormhole call reverts due to insufficient contract balance, making every native ETH `finTransfer` permanently unexecutable.

---

### Finding Description

`OmniBridge.finTransfer` is `payable`. For native ETH transfers it first sends `payload.amount` ETH to the recipient:

```solidity
// OmniBridge.sol L317-L322
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
}
```

Immediately after, it calls `finTransferExtension(payload)` (line 357). In `OmniBridgeWormhole`, that override does:

```solidity
// OmniBridgeWormhole.sol L109
_wormhole.publishMessage{value: msg.value}(
    wormholeNonce,
    messagePayload,
    _consistencyLevel
);
```

`msg.value` is the total ETH the caller sent with the transaction. The caller must supply `payload.amount` (to release to the recipient) **plus** the Wormhole `messageFee()`. After the `call{value: payload.amount}` on line 319, the contract's balance is:

```
contractBalance_before + msg.value − payload.amount
```

Assuming no pre-existing ETH balance, this equals `msg.value − payload.amount`. The subsequent `publishMessage{value: msg.value}` attempts to forward `msg.value` ETH, but only `msg.value − payload.amount` is available. The EVM reverts with an out-of-balance error, and because `completedTransfers[payload.destinationNonce]` was already set to `true` on line 287 **before** the revert path is reached... 

Wait — actually the revert unwinds the entire transaction, so `completedTransfers` is not permanently set. However, the transaction will **always** revert for native ETH finalization, meaning the nonce can never be consumed and the ETH locked in the bridge from the original `initTransfer` can never be released to the recipient. Every retry also reverts for the same reason.

The correct Wormhole fee to forward is `msg.value − payload.amount` (the ETH remaining after paying the recipient), not the full `msg.value`.

---

### Impact Explanation

**Critical — Permanent irrecoverable lock of bridged native ETH.**

Any user who bridged native ETH from EVM to NEAR via `initTransfer(address(0), ...)` has their ETH locked in the `OmniBridgeWormhole` contract. When the NEAR side issues the corresponding `finTransfer` back to EVM, every attempt to execute it reverts. The ETH cannot be released to the recipient and cannot be recovered by any other protocol path. The bridge's native ETH vault is permanently frozen for all such transfers.

---

### Likelihood Explanation

Native ETH bridging (`tokenAddress == address(0)`) is an explicitly supported code path in both `initTransfer` and `finTransfer`. Any user who initiates a native ETH bridge from EVM to NEAR and then attempts to redeem on the EVM side will trigger this revert. No special attacker knowledge is required — the bug is deterministic and affects every native ETH `finTransfer` call on the Wormhole variant of the bridge.

---

### Recommendation

In `OmniBridgeWormhole.finTransferExtension`, the Wormhole fee must account for ETH already disbursed. Pass only the remaining balance as the fee:

```solidity
function finTransferExtension(
    BridgeTypes.TransferMessagePayload memory payload
) internal override {
    // ...build messagePayload...
    uint256 wormholeFee = payload.tokenAddress == address(0)
        ? msg.value - payload.amount   // ETH already sent to recipient
        : msg.value;
    _wormhole.publishMessage{value: wormholeFee}(
        wormholeNonce,
        messagePayload,
        _consistencyLevel
    );
    wormholeNonce++;
}
```

Alternatively, require callers to send the Wormhole fee separately from the transfer amount and validate `msg.value == payload.amount + _wormhole.messageFee()` at the `finTransfer` entry point for native ETH.

---

### Proof of Concept

1. Alice calls `initTransfer(address(0), 1 ether, 0, 0, "alice.near", "")` sending `1 ether` on EVM. The ETH is locked in `OmniBridgeWormhole`.
2. The NEAR side processes the transfer and issues a signed `TransferMessagePayload` with `tokenAddress = address(0)`, `amount = 1 ether`.
3. A relayer calls `finTransfer(sig, payload)` sending `msg.value = 1 ether + wormholeFee` (e.g., `1.001 ether`).
4. Line 319 of `OmniBridge.sol` sends `1 ether` to Alice's EVM address. Contract balance is now `0.001 ether`.
5. Line 357 calls `finTransferExtension`, which executes `_wormhole.publishMessage{value: 1.001 ether}(...)`.
6. The EVM reverts: contract only holds `0.001 ether` but tries to forward `1.001 ether`.
7. The entire transaction reverts. Alice's ETH remains locked in the bridge forever; no retry can succeed. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L108-113)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            messagePayload,
            _consistencyLevel
        );
```
