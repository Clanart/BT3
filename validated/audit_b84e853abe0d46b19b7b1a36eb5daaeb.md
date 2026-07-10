### Title
ETH Transfer to Reverting Smart Contract Recipient Causes Permanent Fund Lock — (File: evm/src/omni-bridge/contracts/OmniBridge.sol)

---

### Summary

In `OmniBridge.finTransfer`, when bridging native ETH from NEAR to EVM, the ETH is delivered to `payload.recipient` via a bare `.call{value: payload.amount}("")`. If the recipient is a smart contract that cannot accept a plain ETH transfer (no `payable` fallback, or a fallback that reverts), the call fails, the function reverts, and the ETH is permanently undeliverable with no user-accessible recovery path.

---

### Finding Description

`finTransfer` handles the ETH case at lines 317–322:

```solidity
if (payload.tokenAddress == address(0)) {
    // slither-disable-next-line arbitrary-send-eth
    (bool success, ) = payload.recipient.call{value: payload.amount}(
        ""
    );
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

The nonce is marked used at line 287 **before** the ETH transfer:

```solidity
completedTransfers[payload.destinationNonce] = true;
``` [2](#0-1) 

When `success == false`, the function reverts with `FailedToSendEther()`. Because the entire transaction reverts, the `completedTransfers` write is also rolled back — the nonce is **never consumed**. This means:

1. The relayer can retry indefinitely, but every attempt will revert if the recipient always rejects plain ETH.
2. The ETH remains locked inside the bridge contract.
3. There is no user-accessible function to redirect the ETH to a different address or claim it back.
4. The recipient address is fixed in the MPC-signed payload originating from NEAR; the user cannot change it unilaterally on the EVM side.

Problematic recipient classes (mirroring the external report's examples):
- A smart contract with no `payable` fallback.
- A smart contract whose `receive`/`fallback` consumes more gas than the caller's remaining budget (e.g., a proxy-wrapped wallet).
- A multisig or account-abstraction wallet that performs storage writes in its `receive` hook. [3](#0-2) 

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds.**

ETH that a user bridged from EVM → NEAR and is now bridging back (NEAR → EVM) becomes permanently unclaimable if the designated EVM recipient is a smart contract that reverts on plain ETH receipt. The bridge holds the ETH, the nonce is never finalized, and no user-facing escape hatch exists. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** Smart contract recipients are common in DeFi: multisig treasuries (Gnosis Safe variants), account-abstraction wallets, protocol vaults, and any contract deployed without a `payable` fallback. A user who specifies such an address as their EVM recipient when initiating a NEAR → EVM ETH bridge transfer will have their funds permanently locked. No attacker action is required; the user's own choice of recipient is sufficient to trigger the condition.

---

### Recommendation

1. **Pull-payment pattern**: Instead of pushing ETH to the recipient in `finTransfer`, record the claimable balance in a mapping (`pendingWithdrawals[recipient] += amount`) and expose a `withdraw()` function. This decouples delivery failure from finalization.
2. **Configurable gas stipend with a safe minimum**: If push delivery is retained, forward a configurable gas amount (e.g., 6 000–10 000) rather than all remaining gas, and on failure store the amount for later pull-claim rather than reverting.
3. **Rescue / redirect mechanism**: Allow the original NEAR-side initiator (proven via signature) to redirect a stuck ETH transfer to a new EVM recipient.

---

### Proof of Concept

1. Alice holds ETH in the OmniBridge (deposited via `initTransfer` with `tokenAddress == address(0)`).
2. Alice initiates a NEAR → EVM transfer specifying `recipient = address(MyVault)`, where `MyVault` has no `payable` fallback.
3. The NEAR MPC signs a `TransferMessagePayload` with `tokenAddress = address(0)`, `recipient = address(MyVault)`, `amount = X`.
4. A relayer calls `finTransfer(sig, payload)`.
5. Line 287 sets `completedTransfers[nonce] = true`.
6. Line 319 executes `MyVault.call{value: X}("")` → `MyVault` reverts → `success = false`.
7. Line 322 executes `revert FailedToSendEther()` — the entire transaction reverts, including the nonce write.
8. Every subsequent relay attempt produces the same revert.
9. `X` ETH remains in the bridge contract indefinitely; Alice has no function to recover or redirect it. [4](#0-3)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L279-322)
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

        MultiTokenInfo memory multiToken = multiTokens[payload.tokenAddress];

        if (payload.tokenAddress == address(0)) {
            // slither-disable-next-line arbitrary-send-eth
            (bool success, ) = payload.recipient.call{value: payload.amount}(
                ""
            );
            if (!success) revert FailedToSendEther();
```
