The code path is fully traceable. Let me lay out the exact flow:

**In `OmniBridge.sol` `initTransfer` (native ETH path):** [1](#0-0) 

When `tokenAddress == address(0)`, `extensionValue` is computed as `msg.value - amount - nativeFee` with **no upper-bound check**. If the user sends `msg.value = amount + nativeFee + messageFee + excess`, then `extensionValue = messageFee + excess`.

**In `OmniBridgeWormhole.sol` `initTransferExtension`:** [2](#0-1) 

The full `value` (i.e., `extensionValue`) is forwarded to `_wormhole.publishMessage{value: value}(...)`. There is **no check** that `value == _wormhole.messageFee()`. The Wormhole core contract accepts excess ETH without reverting and without refunding it. The excess is permanently locked in the Wormhole contract.

**Contrast with the base `OmniBridge` `initTransferExtension`:** [3](#0-2) 

The base implementation reverts if `value != 0`, but `OmniBridgeWormhole` overrides this without adding an equivalent upper-bound guard.

---

### Title
Silent Consumption of Excess ETH via Unchecked `extensionValue` Forwarded to Wormhole — (`evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

### Summary
`initTransfer` with `tokenAddress=address(0)` computes `extensionValue = msg.value - amount - nativeFee` and passes it directly to `_wormhole.publishMessage{value: extensionValue}`. No check enforces `extensionValue == _wormhole.messageFee()`. Any ETH beyond `messageFee()` is permanently forwarded to and locked in the Wormhole core contract.

### Finding Description
In `OmniBridge.initTransfer`, when `tokenAddress == address(0)`:

```solidity
// OmniBridge.sol:391
extensionValue = msg.value - amount - nativeFee;
```

This value is passed to `initTransferExtension`, which in `OmniBridgeWormhole` does:

```solidity
// OmniBridgeWormhole.sol:143
_wormhole.publishMessage{value: value}(wormholeNonce, payload, _consistencyLevel);
```

The Wormhole core contract only requires `msg.value >= messageFee()` and does not refund excess. There is no guard in either `initTransfer` or `initTransferExtension` that enforces `extensionValue == _wormhole.messageFee()`.

### Impact Explanation
Any ETH sent beyond `amount + nativeFee + messageFee()` is permanently locked in the Wormhole contract. There is no recovery path: the Wormhole contract has no sweep or refund mechanism for overpayments. This constitutes an irrecoverable loss of user funds matching the **Critical** impact category: permanent freezing/irrecoverable lock of user funds in bridge flows.

### Likelihood Explanation
The call is fully permissionless. Any user who miscalculates `msg.value` (e.g., adds a buffer, uses a stale `messageFee()` value, or is misled by a frontend) will silently lose the excess. The Wormhole `messageFee()` can also change over time, making exact calculation non-trivial for callers.

### Recommendation
Add an exact equality check before forwarding to Wormhole:

```solidity
require(value == _wormhole.messageFee(), "InvalidValue: excess ETH");
```

Or refund the excess after the call:

```solidity
uint256 fee = _wormhole.messageFee();
_wormhole.publishMessage{value: fee}(...);
if (value > fee) {
    (bool ok,) = sender.call{value: value - fee}("");
    require(ok);
}
```

### Proof of Concept
```solidity
uint256 messageFee = wormhole.messageFee(); // e.g., 0.001 ether
uint128 amount = 1 ether;
uint128 nativeFee = 0;
uint256 excess = 0.5 ether;

uint256 userBalanceBefore = user.balance;
bridge.initTransfer{value: amount + nativeFee + messageFee + excess}(
    address(0), amount, 0, nativeFee, "recipient.near", ""
);
// Assert: user lost amount + messageFee + excess (not just amount + messageFee)
// Assert: wormhole.balance increased by messageFee + excess (not just messageFee)
assert(userBalanceBefore - user.balance == amount + messageFee + excess);
```

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L387-391)
```text
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L502-506)
```text
    ) internal virtual {
        if (value != 0) {
            revert InvalidValue();
        }
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L143-147)
```text
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );
```
