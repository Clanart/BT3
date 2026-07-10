### Title
Native ETH `finTransfer` Permanently Locks User Funds When Recipient Cannot Receive ETH — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

`OmniBridge.finTransfer` marks the destination nonce as used **before** attempting to deliver native ETH to the recipient. When the ETH delivery fails (recipient is a contract that rejects ETH), the function reverts with `FailedToSendEther()`, rolling back the nonce mark. However, the NEAR side has already burned/locked the user's tokens and stored the transfer in `pending_transfers` with no cancellation path. Because the recipient address is immutably encoded in the MPC-signed payload, every retry of `finTransfer` will fail identically, permanently locking the user's bridged assets.

---

### Finding Description

In `OmniBridge.finTransfer`, the nonce is marked used before any external call:

```solidity
completedTransfers[payload.destinationNonce] = true;   // line 287
// ... signature verification ...
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();           // line 322
}
``` [1](#0-0) 

When `revert FailedToSendEther()` fires, Solidity rolls back all state changes including the nonce mark, so `completedTransfers[payload.destinationNonce]` remains `false`. The relayer can retry, but the recipient address is fixed inside the MPC-signed `TransferMessagePayload` — it cannot be changed without a new MPC signature, and no such re-signing mechanism exists in the protocol.

On the NEAR side, `init_transfer_internal` burns or locks the user's tokens and inserts the transfer into `pending_transfers` before any EVM confirmation is awaited:

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,
);
``` [2](#0-1) 

The `pending_transfers` entry is only removed by `claim_fee_callback` (which requires proof of EVM finalization) or by internal storage-failure paths — there is no user-callable cancellation function: [3](#0-2) 

Because EVM finalization can never succeed when the recipient rejects ETH, `claim_fee` can never be called, and the `pending_transfer` — along with the user's burned/locked tokens — is irrecoverable under the current code.

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

A user who specifies a smart contract address as the EVM recipient (e.g., a multisig, DAO treasury, or DeFi protocol without a `receive()` function) will have their NEAR-side tokens permanently burned/locked. The ETH remains in the bridge contract indefinitely. No admin function, timeout, or user-callable escape hatch exists to recover the funds without a contract upgrade.

This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** Smart contract addresses that cannot receive plain ETH are common (multisigs, DAOs, protocol treasuries, contracts compiled without a `receive()` function). A user bridging native ETH to such an address — a realistic and unsurprising action — triggers the lock with no warning. No special privilege or attacker cooperation is required; the unprivileged bridge user is the sole actor.

---

### Recommendation

Add a fallback delivery mechanism analogous to the `withdrawTo` pattern suggested in the external report. Two options:

1. **Pull-based escrow:** On ETH delivery failure, instead of reverting, record the amount in a `pendingWithdrawals[recipient]` mapping and emit an event. Provide a `withdrawTo(address payable to)` function that lets the original recipient redirect funds to an address of their choice (authenticated by `msg.sender == original_recipient`).

2. **Recipient override at delivery time:** Allow the relayer (or the user via a separate signed message) to supply an alternative `deliverTo` address when the primary recipient is known to be non-receivable, verified against the original MPC signature via an additional field.

Either approach must ensure the NEAR-side `pending_transfer` is only removed after confirmed EVM delivery, or a symmetric cancellation path is added to the NEAR bridge so users can reclaim tokens when EVM delivery is permanently blocked.

---

### Proof of Concept

1. User on NEAR calls `init_transfer` targeting a NEAR→EVM native ETH transfer, specifying `recipient = address(SomeContractWithNoReceive)`.
2. NEAR bridge burns the user's wNEAR and stores the entry in `pending_transfers`.
3. MPC signs `TransferMessagePayload` with `tokenAddress = address(0)`, `recipient = SomeContractWithNoReceive`, `amount = X`.
4. Relayer calls `OmniBridge.finTransfer(sig, payload)` on EVM.
5. `completedTransfers[nonce] = true` executes; then `SomeContractWithNoReceive.call{value: X}("")` returns `success = false`.
6. `revert FailedToSendEther()` fires — all EVM state rolls back; nonce remains unused.
7. Every subsequent retry of step 4 produces the same revert.
8. NEAR `pending_transfers` entry is never removed; user's tokens remain burned/locked with no recovery path. [4](#0-3) [5](#0-4)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-322)
```text
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

**File:** near/omni-bridge/src/lib.rs (L1094-1094)
```rust
        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
```

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
    }
```
