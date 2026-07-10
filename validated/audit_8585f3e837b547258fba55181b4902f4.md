### Title
Excess `msg.value` Passed to `_wormhole.publishMessage` Is Permanently Lost — (`evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

---

### Summary

`OmniBridgeWormhole` overrides four extension hooks (`deployTokenExtension`, `logMetadataExtension`, `finTransferExtension`, `initTransferExtension`) and in every case forwards ETH to `_wormhole.publishMessage` without validating that the forwarded amount equals exactly `_wormhole.messageFee()`. The `IWormhole` interface already declares `messageFee()` but it is never called for validation. Any ETH sent above the required Wormhole fee is permanently irrecoverable: Wormhole does not refund excess, and `OmniBridge` has no ETH-withdrawal function.

---

### Finding Description

`OmniBridgeWormhole.sol` inherits from `OmniBridge.sol`. The parent exposes four `payable` public entry points:

| Entry point | `msg.value` forwarded as |
|---|---|
| `deployToken` | `msg.value` (full) |
| `logMetadata` / `logMetadata1155` | `msg.value` (full) |
| `finTransfer` | `msg.value` (full) |
| `initTransfer` (native ETH) | `msg.value - amount - nativeFee` |
| `initTransfer` (ERC-20) | `msg.value - nativeFee` |

In every Wormhole override the forwarded value is passed verbatim:

```solidity
// deployTokenExtension – line 63
_wormhole.publishMessage{value: msg.value}(wormholeNonce, payload, _consistencyLevel);

// logMetadataExtension – line 87
_wormhole.publishMessage{value: msg.value}(wormholeNonce, payload, _consistencyLevel);

// finTransferExtension – line 109
_wormhole.publishMessage{value: msg.value}(wormholeNonce, messagePayload, _consistencyLevel);

// initTransferExtension – line 143
_wormhole.publishMessage{value: value}(wormholeNonce, payload, _consistencyLevel);
```

Wormhole's `publishMessage` requires exactly `messageFee()` wei. It does **not** refund any surplus. The `IWormhole` interface already declares `messageFee()` (line 15 of `OmniBridgeWormhole.sol`) but it is never queried to enforce an exact-match check. `OmniBridge` has no ETH-withdrawal function; its only ETH sink is the `receive()` fallback and the native-ETH `finTransfer` path. Excess ETH forwarded to Wormhole is therefore permanently lost.

---

### Impact Explanation

Any caller of `initTransfer`, `finTransfer`, `deployToken`, or `logMetadata` who sends more ETH than `_wormhole.messageFee()` (plus `amount + nativeFee` for native-ETH `initTransfer`) loses the surplus permanently. The funds cannot be recovered from Wormhole, and `OmniBridge` has no sweep/rescue function for ETH. This constitutes an irrecoverable lock of user funds in the bridge flow, matching the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

The Wormhole message fee is not a fixed, well-known constant; it is governance-controlled and can change. Users and integrators who query `messageFee()` off-chain and then submit a transaction with a small buffer (a common defensive pattern) will silently lose the buffer. The entry points are all unprivileged (`initTransfer`, `logMetadata`) or callable by any relayer (`finTransfer`), so the affected population is the entire bridge user base.

---

### Recommendation

In each Wormhole extension, assert that the forwarded value equals exactly `_wormhole.messageFee()` before calling `publishMessage`, and revert otherwise:

```solidity
uint256 fee = _wormhole.messageFee();
require(msg.value == fee, "incorrect wormhole fee");
_wormhole.publishMessage{value: fee}(...);
```

For `initTransferExtension`, apply the same check against the `value` parameter:

```solidity
require(value == _wormhole.messageFee(), "incorrect wormhole fee");
_wormhole.publishMessage{value: value}(...);
```

---

### Proof of Concept

1. Wormhole governance sets `messageFee()` to `0.001 ether`.
2. A user calls `initTransfer` with a native-ETH bridge of `1 ether`, `nativeFee = 0`, and sends `msg.value = 1.002 ether` (intending a small buffer).
3. `extensionValue = 1.002e18 - 1e18 - 0 = 0.002e18`.
4. `initTransferExtension` calls `_wormhole.publishMessage{value: 0.002 ether}(...)`.
5. Wormhole consumes `0.001 ether` as its fee and retains the remaining `0.001 ether` — it does not refund the surplus.
6. The `0.001 ether` surplus is permanently lost; `OmniBridge` has no mechanism to recover it.

The same scenario applies to `finTransfer`, `deployToken`, and `logMetadata` where the full `msg.value` is forwarded.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4) [6](#0-5) [7](#0-6)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L8-16)
```text
interface IWormhole {
    function publishMessage(
        uint32 nonce,
        bytes memory payload,
        uint8 consistencyLevel
    ) external payable returns (uint64 sequence);

    function messageFee() external view returns (uint256);
}
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L62-67)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L86-91)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L108-115)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
            messagePayload,
            _consistencyLevel
        );

        wormholeNonce++;
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L142-147)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L386-393)
```text
        uint256 extensionValue;
        if (tokenAddress == address(0)) {
            if (fee != 0) {
                revert InvalidFee();
            }
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```
