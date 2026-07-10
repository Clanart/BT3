### Title
Permanent Fund Lock via ETH-Rejecting Recipient Contract in `finTransfer` - (File: `evm/src/omni-bridge/contracts/OmniBridge.sol`)

---

### Summary

When bridging a token that maps to native ETH (`tokenAddress == address(0)`) from NEAR to EVM, `OmniBridge.finTransfer` sends ETH directly to `payload.recipient` via a low-level call. If the recipient is a contract that reverts on receiving ETH, every `finTransfer` attempt reverts permanently. Because NEAR has no cancel/refund path for pending cross-chain transfers, the user's funds are irrecoverably locked on NEAR.

---

### Finding Description

In `finTransfer`, when `payload.tokenAddress == address(0)`, the bridge delivers native ETH to the recipient:

```solidity
if (payload.tokenAddress == address(0)) {
    (bool success, ) = payload.recipient.call{value: payload.amount}("");
    if (!success) revert FailedToSendEther();
}
``` [1](#0-0) 

Because `revert FailedToSendEther()` unwinds the entire transaction, the `completedTransfers[payload.destinationNonce] = true` write at line 287 is also reverted — the nonce is never consumed. [2](#0-1) 

This means every subsequent call to `finTransfer` for this transfer will also revert (the nonce check passes, but the ETH send fails again). The transfer is permanently un-finalizable on EVM.

On the NEAR side, the user's tokens are stored in `pending_transfers` after `init_transfer`. The only code path that removes a pending transfer is `remove_transfer_message`, called exclusively from `claim_fee_callback`: [3](#0-2) 

`claim_fee_callback` requires a `ProverResult::FinTransfer` proof — i.e., proof that `finTransfer` succeeded on EVM: [4](#0-3) 

Since `finTransfer` always reverts for an ETH-rejecting recipient, this proof can never be produced. There is no public `cancel_transfer` or alternative recovery function in the NEAR bridge contract. The funds are permanently locked.

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

A user who initiates a NEAR→EVM transfer of a token that maps to native ETH, specifying a contract recipient that does not implement `receive()` or `fallback()` (or explicitly reverts), will have their tokens locked in the NEAR bridge's `pending_transfers` map with no recovery path. The locked tokens cannot be withdrawn, cancelled, or redirected.

---

### Likelihood Explanation

**Medium.** The scenario arises whenever:

1. A user bridges a token whose EVM representation is native ETH (`tokenAddress == address(0)`).
2. The specified EVM recipient is a smart contract without ETH-receive capability (e.g., a multisig, a DAO treasury, a proxy, or any contract that lacks `receive()`/`fallback()`).

This is a realistic user mistake — many protocol-controlled addresses on EVM are contracts that do not accept raw ETH. The user has no on-chain warning before the funds are locked. An adversary could also deliberately trigger this to permanently destroy their own bridged assets (e.g., to manipulate supply accounting).

---

### Recommendation

Mirror the Gearbox fix: instead of sending native ETH directly to the recipient, always deliver WETH (or the wrapped equivalent) when the recipient is a contract, or unconditionally deliver WETH and let the recipient unwrap it themselves. Concretely:

```solidity
if (payload.tokenAddress == address(0)) {
    // Wrap ETH and transfer WETH to recipient instead of raw ETH
    IWETH(wethAddress).deposit{value: payload.amount}();
    IERC20(wethAddress).safeTransfer(payload.recipient, payload.amount);
}
```

Alternatively, add a fallback: attempt the ETH send; on failure, wrap to WETH and transfer WETH. This eliminates the revert path entirely.

On the NEAR side, add a permissioned `cancel_transfer` function that allows the original sender to reclaim locked tokens if a transfer has been pending beyond a configurable timeout, providing a recovery path independent of EVM finalization.

---

### Proof of Concept

1. Deploy a contract `Blocker` on EVM with no `receive()` or `fallback()` — any ETH send to it reverts.
2. On NEAR, call `init_transfer` specifying a token whose EVM address mapping is `address(0)` (native ETH) and set `recipient = OmniAddress::Eth(blocker_address)`. Tokens are locked in NEAR `pending_transfers`.
3. The MPC signs the transfer; a relayer calls `OmniBridge.finTransfer` on EVM with `payload.tokenAddress = address(0)` and `payload.recipient = blocker_address`.
4. `payload.recipient.call{value: payload.amount}("")` returns `success = false`; `revert FailedToSendEther()` fires. The entire tx reverts; `completedTransfers[nonce]` is not set.
5. Every retry of step 3 produces the same revert.
6. On NEAR, `claim_fee` cannot be called (no EVM finalization proof exists). `remove_transfer_message` is never reached. The user's tokens remain locked in `pending_transfers` indefinitely with no recovery mechanism. [1](#0-0) [5](#0-4) [6](#0-5)

### Citations

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L283-287)
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

**File:** near/omni-bridge/src/lib.rs (L1075-1094)
```rust
        let Ok(ProverResult::FinTransfer(fin_transfer)) = call_result else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };

        let fee_recipient = fin_transfer.fee_recipient.unwrap_or_else(|| {
            env::panic_str(BridgeError::FeeRecipientNotSetOrEmpty.to_string().as_str());
        });

        require!(
            fee_recipient == *predecessor_account_id,
            BridgeError::OnlyFeeRecipientCanClaim.as_ref()
        );
        require!(
            self.factories
                .get(&fin_transfer.emitter_address.get_chain())
                == Some(fin_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
```

**File:** near/omni-bridge/src/lib.rs (L2194-2210)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
```
