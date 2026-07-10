### Title
`msg.value` Double-Spent in `finTransfer` for Native ETH — `OmniBridgeWormhole` Native ETH Finalization Permanently Broken - (File: evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol)

---

### Summary

`OmniBridgeWormhole.finTransferExtension` forwards the raw `msg.value` to Wormhole as the publishing fee. However, when `finTransfer` is called for a native ETH transfer (`tokenAddress == address(0)`), the contract first disburses `payload.amount` ETH to the recipient, reducing the contract's balance. The subsequent call to `_wormhole.publishMessage{value: msg.value}` then attempts to forward the original full `msg.value` — which the contract no longer holds — causing every native ETH finalization to revert. Native ETH bridging from NEAR to EVM via the Wormhole variant is permanently broken.

---

### Finding Description

`OmniBridge.finTransfer` is `payable` and handles two distinct ETH obligations in sequence when `payload.tokenAddress == address(0)`:

1. **Pay the recipient** — `payload.amount` ETH is sent via `.call{value: payload.amount}("")`.
2. **Publish to Wormhole** — `finTransferExtension` is called, which forwards `msg.value` to `_wormhole.publishMessage`. [1](#0-0) [2](#0-1) [3](#0-2) 

After step 1, the contract's ETH balance has been reduced by `payload.amount`. Step 2 then tries to forward the original `msg.value` (not the remaining balance) to Wormhole. This creates an irreconcilable accounting conflict:

- If the caller sends `msg.value = wormholeFee` (the correct Wormhole fee), step 1 reverts because the contract has insufficient ETH to pay `payload.amount` to the recipient.
- If the caller sends `msg.value = payload.amount + wormholeFee`, step 1 succeeds, but step 2 tries to forward `payload.amount + wormholeFee` to Wormhole. Wormhole's `publishMessage` enforces `msg.value == messageFee()` exactly (as confirmed by the test stub), so it reverts.

There is no value of `msg.value` that satisfies both obligations simultaneously.

By contrast, `initTransferExtension` correctly avoids this problem by accepting a pre-computed `value` parameter (the residual after deducting `nativeFee` and `amount`), rather than using `msg.value` directly: [4](#0-3) 

`finTransferExtension`, `deployTokenExtension`, and `logMetadataExtension` all use `msg.value` directly instead of a passed-in residual, but only `finTransferExtension` is called after ETH has already been disbursed from the contract's balance.

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

Any user who initiates a NEAR→EVM bridge transfer of native ETH (i.e., `tokenAddress == address(0)` on the EVM side) will have their NEAR-side assets burned/locked, but the corresponding ETH on EVM can never be released. Every call to `finTransfer` for native ETH on `OmniBridgeWormhole` reverts unconditionally, making the settlement permanently unclaimable.

---

### Likelihood Explanation

**High.** Native ETH is a first-class bridgeable asset in the protocol (the `address(0)` path is explicitly handled in `finTransfer`). Any relayer or user attempting to finalize a native ETH cross-chain transfer on the Wormhole-variant deployment will trigger this revert deterministically. No special attacker action is required — normal protocol usage is sufficient to trigger the freeze.

---

### Recommendation

Pass the Wormhole fee as a separate parameter to `finTransferExtension` (analogous to how `initTransferExtension` receives `value`), computed before any ETH disbursement:

```solidity
// In OmniBridge.finTransfer, capture the wormhole fee before disbursing ETH:
uint256 wormholeFee = msg.value - payload.amount; // for native ETH path
// ...send payload.amount to recipient...
finTransferExtension(payload, wormholeFee);
```

```solidity
// In OmniBridgeWormhole.finTransferExtension:
function finTransferExtension(
    BridgeTypes.TransferMessagePayload memory payload,
    uint256 wormholeFee
) internal override {
    // ...
    _wormhole.publishMessage{value: wormholeFee}(...);
}
```

For non-native-ETH transfers, `wormholeFee` equals `msg.value` directly (no prior ETH disbursement occurs), preserving existing behavior.

---

### Proof of Concept

1. A user bridges 1 ETH from NEAR to EVM via `OmniBridgeWormhole`. The NEAR-side contract burns the user's wrapped ETH.
2. A relayer calls `finTransfer(signatureData, payload)` where `payload.tokenAddress = address(0)` and `payload.amount = 1 ether`.
3. **Scenario A**: Relayer sends `msg.value = wormholeFee` (e.g., 10000 wei).
   - Line 319: `.call{value: 1 ether}("")` — contract only has 10000 wei → **REVERT** (`FailedToSendEther`).
4. **Scenario B**: Relayer sends `msg.value = 1 ether + 10000 wei`.
   - Line 319: `.call{value: 1 ether}("")` — succeeds, contract balance is now 10000 wei.
   - Line 109: `_wormhole.publishMessage{value: 1 ether + 10000 wei}(...)` — contract only has 10000 wei → **REVERT** (out-of-funds or Wormhole fee mismatch).
5. In both scenarios the transaction reverts. The user's NEAR assets are permanently lost with no recourse. [1](#0-0) [5](#0-4)

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

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L142-147)
```text
        // slither-disable-next-line reentrancy-eth
        _wormhole.publishMessage{value: value}(
            wormholeNonce,
            payload,
            _consistencyLevel
        );
```
