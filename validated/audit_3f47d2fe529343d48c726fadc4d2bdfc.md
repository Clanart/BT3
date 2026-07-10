### Title
Excess `msg.value` Permanently Lost in Wormhole Extension Calls — (`evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol`)

### Summary

`OmniBridgeWormhole` overrides several `payable` extension hooks and forwards `msg.value` (or a derived `extensionValue`) directly to `_wormhole.publishMessage{value: ...}(...)`. Wormhole's `publishMessage` only consumes exactly `messageFee()` wei; any surplus is retained by the Wormhole core contract and is irrecoverable. No upper-bound check or refund mechanism exists in any of the affected call paths.

### Finding Description

`OmniBridgeWormhole` overrides four internal extension hooks, each of which forwards ETH to Wormhole:

**`deployTokenExtension`** — called from the public `payable` `deployToken`: [1](#0-0) 

**`logMetadataExtension`** — called from the public `payable` `logMetadata` and `logMetadata1155`: [2](#0-1) 

**`finTransferExtension`** — called from the public `payable` `finTransfer`: [3](#0-2) 

In all three cases the full `msg.value` is forwarded verbatim. Wormhole's `publishMessage` reverts when `msg.value < messageFee()` but silently accepts and keeps any surplus above `messageFee()`.

**`initTransferExtension`** — called from `initTransfer` / `initTransfer1155`: [4](#0-3) 

Here `value` is `extensionValue`, computed in `OmniBridge.initTransfer` as: [5](#0-4) 

If the user sends `msg.value > amount + nativeFee + messageFee()` (ETH path) or `msg.value > nativeFee + messageFee()` (ERC20 path), the surplus `extensionValue` is forwarded to Wormhole and lost.

The base `OmniBridge.initTransferExtension` reverts on any non-zero `value`: [6](#0-5) 

This guard is completely bypassed by the Wormhole override, which accepts any `value ≥ messageFee()` without complaint.

### Impact Explanation

Any ETH sent above the exact Wormhole `messageFee()` is permanently transferred to the Wormhole core contract. There is no withdrawal path from Wormhole for overpaid fees. The funds are irrecoverable by the sender, the bridge, or any admin. This constitutes a permanent, irrecoverable loss of user funds in a bridge flow, matching the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

All four affected entry points (`deployToken`, `logMetadata`, `logMetadata1155`, `finTransfer`, `initTransfer`, `initTransfer1155`) are public and callable by any unprivileged user. The Wormhole `messageFee()` is a dynamic value that can change between the time a user estimates the fee off-chain and the time their transaction is mined. A user who adds a small buffer to avoid an underflow revert will silently lose the buffer. Wallets and front-ends that round up fee estimates will trigger this path routinely.

### Recommendation

For each Wormhole extension hook, enforce an exact fee match and refund any surplus, or revert on overpayment:

```solidity
uint256 fee = _wormhole.messageFee();
require(msg.value >= fee, "Insufficient Wormhole fee");
_wormhole.publishMessage{value: fee}(wormholeNonce, payload, _consistencyLevel);
// refund excess
if (msg.value > fee) {
    (bool ok, ) = msg.sender.call{value: msg.value - fee}("");
    require(ok, "Refund failed");
}
```

Apply the same pattern in `initTransferExtension` using `value` instead of `msg.value`, and refund `value - fee` back to `msg.sender`.

### Proof of Concept

1. Wormhole `messageFee()` returns `X` wei.
2. User calls `OmniBridgeWormhole.finTransfer{value: X + 1 ether}(sig, payload)`.
3. `finTransferExtension` executes: `_wormhole.publishMessage{value: X + 1 ether}(...)`.
4. Wormhole accepts the call (value ≥ fee), keeps all `X + 1 ether`.
5. No refund is issued; the user permanently loses `1 ether`.

The same sequence applies to `deployToken`, `logMetadata`, `logMetadata1155`, and `initTransfer` with an oversized `msg.value`.

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L62-64)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L86-88)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L108-110)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: msg.value}(
            wormholeNonce,
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L142-144)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L391-393)
```text
            extensionValue = msg.value - amount - nativeFee;
        } else {
            extensionValue = msg.value - nativeFee;
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L502-505)
```text
    ) internal virtual {
        if (value != 0) {
            revert InvalidValue();
        }
```
