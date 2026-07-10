### Title
Empty `receive()` Permanently Locks ETH Sent Directly to OmniBridge — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

`OmniBridge.sol` declares an empty, unconditional `receive() external payable {}`. Any ETH sent to the contract with no calldata is silently accepted and permanently locked: the contract exposes no withdrawal, rescue, or sweep function, and the only ETH egress path (`finTransfer` for native-token settlements) requires a valid MPC-signed message. This is a direct structural analog to M-18.

---

### Finding Description

`OmniBridge` is the central EVM-side bridge contract. It holds native ETH on behalf of users who call `initTransfer(address(0), ...)` to bridge ETH to NEAR/other chains, and it disburses ETH to recipients via `finTransfer` when `payload.tokenAddress == address(0)`.

At line 574 the contract declares:

```solidity
receive() external payable {}
``` [1](#0-0) 

This function body is completely empty. It accepts any ETH sent with no calldata and does nothing: no forwarding, no event emission, no accounting update, no revert.

The only ETH egress path in the entire contract is inside `finTransfer`:

```solidity
(bool success, ) = payload.recipient.call{value: payload.amount}("");
if (!success) revert FailedToSendEther();
``` [2](#0-1) 

That path is gated behind a valid ECDSA signature from `nearBridgeDerivedAddress` and a nonce check — it cannot be used to recover untracked ETH. No `withdraw`, `rescue`, `recover`, or `sweep` function exists anywhere in the contract.

`OmniBridgeWormhole` inherits `OmniBridge` and does not override `receive()`, so the same empty handler applies to the Wormhole deployment. [3](#0-2) 

---

### Impact Explanation

Any ETH sent to the bridge contract address with no calldata — whether by a user who mistakenly uses a plain ETH transfer instead of `initTransfer`, a wallet that auto-sends ETH to the "bridge address," or a contract that calls `address(bridge).transfer(amount)` — is accepted silently and becomes irrecoverable from the user's perspective. The ETH balance increases but is not credited to any bridge position, so it cannot be claimed via `finTransfer` without a fraudulent MPC signature. The funds are permanently frozen inside the contract.

This matches the allowed impact: **Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows.**

---

### Likelihood Explanation

The entry path requires no privilege: any EOA or contract can send ETH to the bridge address with no calldata. Common triggers include:

- Users sending ETH via MetaMask's "Send" UI to the bridge contract address, expecting the bridge to initiate a transfer (a reasonable but incorrect assumption given the contract is `payable`).
- Smart-contract integrators calling `bridge.transfer(amount)` or `bridge.send(amount)` instead of encoding `initTransfer`.
- Refund flows from other contracts that send ETH back to the bridge address with no calldata.

The `receive()` function emits no event, so the loss is silent and may go unnoticed until the user investigates on-chain.

---

### Recommendation

Replace the empty `receive()` with an explicit revert to prevent accidental ETH lock-up:

```solidity
receive() external payable {
    revert("OmniBridge: direct ETH transfers not supported; use initTransfer");
}
```

If the contract legitimately needs to accept ETH from specific callers (e.g., Wormhole fee refunds), add an allowlist check or a dedicated `deposit()` function with proper accounting. Additionally, add an admin-only ETH rescue function to recover any ETH already locked.

---

### Proof of Concept

1. Deploy `OmniBridgeWormhole` (or use the live `OmniBridge` proxy).
2. From any EOA, execute a plain ETH transfer:
   ```
   cast send <bridge_address> --value 1ether --private-key <key>
   ```
3. Observe the transaction succeeds (no revert), the bridge's ETH balance increases by 1 ETH.
4. Confirm there is no function callable by the sender to recover the ETH.
5. Attempt `finTransfer` with `tokenAddress == address(0)` and `amount == 1 ether` — it reverts with `InvalidSignature` because no valid MPC signature exists for this untracked deposit.
6. The 1 ETH is permanently locked. [1](#0-0) [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-313)
```text
    function finTransfer(
        bytes calldata signatureData,
        BridgeTypes.TransferMessagePayload calldata payload
    ) external payable whenNotPaused(PAUSED_FIN_TRANSFER) {
        if (completedTransfers[payload.destinationNonce]) {
            revert NonceAlreadyUsed(payload.destinationNonce);
        }

        completedTransfers[payload.destinationNonce] = true;

        bytes memory borshEncoded = bytes.concat(
            bytes1(uint8(BridgeTypes.PayloadType.TransferMessage)),
            Borsh.encodeUint64(payload.destinationNonce),
            bytes1(payload.originChain),
            Borsh.encodeUint64(payload.originNonce),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.tokenAddress),
            Borsh.encodeUint128(payload.amount),
            bytes1(omniBridgeChainId),
            Borsh.encodeAddress(payload.recipient),
            bytes(payload.feeRecipient).length == 0 // None or Some(String) in rust
                ? bytes("\x00")
                : bytes.concat(
                    bytes("\x01"),
                    Borsh.encodeString(payload.feeRecipient)
                ),
            bytes(payload.message).length == 0
                ? bytes("")
                : Borsh.encodeBytes(payload.message)
        );
        bytes32 hashed = keccak256(borshEncoded);

        if (ECDSA.recover(hashed, signatureData) != nearBridgeDerivedAddress) {
            revert InvalidSignature();
        }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L319-322)
```text
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L574-574)
```text
    receive() external payable {}
```

**File:** evm/src/omni-bridge/contracts/OmniBridgeWormhole.sol (L26-27)
```text
contract OmniBridgeWormhole is OmniBridge {
    IWormhole private _wormhole;
```
