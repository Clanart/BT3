### Title
ETH Permanently Locked in OmniBridge When Recipient Is a Non-Payable Contract — (`evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

In `finTransfer`, when `payload.tokenAddress == address(0)`, native ETH is pushed to `payload.recipient` via a low-level `call`. If the recipient is a non-payable contract, the call returns `success = false`, triggering `revert FailedToSendEther()`. Because Solidity reverts roll back **all** state changes, the `completedTransfers[destinationNonce] = true` write (line 287, set *before* the transfer) is also undone. The nonce is therefore never consumed. Since the MPC signature cryptographically binds the recipient address, no alternative delivery path exists, and the ETH locked in the OmniBridge contract is permanently unclaimable.

---

### Finding Description

The execution path in `finTransfer` is:

1. **Line 283–285** — guard: revert if nonce already used.
2. **Line 287** — `completedTransfers[payload.destinationNonce] = true` — nonce marked *before* any transfer.
3. **Lines 289–313** — Borsh-encode payload, verify MPC signature.
4. **Lines 317–322** — ETH branch:

```solidity
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

When `payload.recipient` is a contract without a `receive()` or `fallback()` function (or one that explicitly reverts), `success` is `false`. The `revert FailedToSendEther()` unwinds the entire transaction, including the nonce write at line 287. The nonce returns to `false`.

Because the MPC signature encodes `recipient` as part of the Borsh payload (line 298), no caller can substitute a different recipient without invalidating the signature. There is no admin escape hatch in `OmniBridge.sol` to override the recipient or force-consume the nonce. Every subsequent call with the same signed payload hits the same code path and reverts identically. [2](#0-1) 

On the NEAR side, `fin_transfer_callback` issues the destination nonce and stores the transfer message for the EVM leg via `process_fin_transfer_to_other_chain`. The wETH is burned and the destination nonce is incremented at that point. There is no cancellation or refund path for EVM-bound transfers whose EVM finalization permanently fails — `revert_lock_actions` only applies to NEAR-recipient finalization failures, not to cross-chain legs. [3](#0-2) [4](#0-3) 

---

### Impact Explanation

- The ETH is held in the OmniBridge contract on EVM (deposited during the original `initTransfer` from EVM → NEAR).
- The corresponding wETH was burned on NEAR when the user initiated the return transfer.
- `finTransfer` can never succeed for this nonce because the recipient rejects ETH unconditionally.
- The nonce is never consumed, so the transfer is in permanent limbo.
- No admin function exists to force-consume the nonce or redirect the ETH.
- Result: **permanent, irrecoverable loss of the bridged ETH amount** — matching the Critical impact category "Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds."

---

### Likelihood Explanation

The scenario is reachable through normal bridge usage:

- A user bridges ETH from EVM → NEAR, then initiates a return transfer specifying a contract address as the EVM recipient.
- The contract may have been payable at initiation time but upgraded to non-payable before finalization (proxy pattern), or the user may have made an honest mistake.
- No privileged access, leaked keys, or colluding MPC signers are required — the MPC signature is obtained through the standard bridge flow.
- The attack is fully local-testable: deploy a non-payable contract, obtain a valid MPC signature for it, call `finTransfer`, observe perpetual revert.

---

### Recommendation

Replace the push-payment pattern with a **pull-payment (withdrawal) pattern** for native ETH delivery:

1. On `FailedToSendEther`, instead of reverting, **consume the nonce** and credit the amount to a claimable balance mapping keyed by `(destinationNonce, recipient)`.
2. Expose a separate `claimEth(uint64 destinationNonce)` function that lets the recipient (or an admin-designated alternative) withdraw the ETH.

Alternatively, wrap ETH as WETH before delivery so the transfer is always an ERC-20 `transfer` call that cannot be rejected by a non-payable contract.

---

### Proof of Concept

```solidity
// NonPayable.sol — no receive() or fallback()
contract NonPayable {}

// Test sequence
address nonPayable = address(new NonPayable());

// 1. Obtain valid MPC signature from NEAR for:
//    tokenAddress = address(0), recipient = nonPayable, amount = 1 ether

// 2. Call finTransfer — always reverts with FailedToSendEther
vm.expectRevert(OmniBridge.FailedToSendEther.selector);
bridge.finTransfer{value: 0}(signature, payload);

// 3. Nonce is NOT consumed — can retry indefinitely, always reverts
assertFalse(bridge.completedTransfers(payload.destinationNonce));

// 4. ETH remains locked in OmniBridge; wETH already burned on NEAR
assertEq(address(bridge).balance, 1 ether); // locked forever
``` [5](#0-4)

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

**File:** near/omni-bridge/src/lib.rs (L720-744)
```rust
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer.recipient.get_chain());
        let transfer_message = TransferMessage {
            origin_nonce: init_transfer.origin_nonce,
            token: init_transfer.token,
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
            sender: init_transfer.sender,
            msg: init_transfer.msg,
            destination_nonce,
            origin_transfer_id: None,
        };

        if let OmniAddress::Near(recipient) = transfer_message.recipient.clone() {
            self.process_fin_transfer_to_near(
                recipient,
                &predecessor_account_id,
                transfer_message,
                storage_deposit_actions,
            )
            .into()
        } else {
            self.process_fin_transfer_to_other_chain(predecessor_account_id, transfer_message);
            PromiseOrValue::Value(destination_nonce)
```

**File:** near/omni-bridge/src/token_lock.rs (L122-142)
```rust
    pub fn revert_lock_actions(&mut self, lock_actions: &[LockAction]) {
        for lock_action in lock_actions {
            match lock_action {
                LockAction::Locked {
                    chain_kind,
                    token_id,
                    amount,
                } => {
                    self.unlock_tokens(*chain_kind, token_id, *amount);
                }
                LockAction::Unlocked {
                    chain_kind,
                    token_id,
                    amount,
                } => {
                    self.lock_tokens(*chain_kind, token_id, *amount);
                }
                LockAction::Unchanged => {}
            }
        }
    }
```
