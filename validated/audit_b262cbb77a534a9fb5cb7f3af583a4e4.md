### Title
Unprivileged `update_transfer_fee` Allows Anyone to Front-Run and DOS `sign_transfer` - (File: `near/omni-bridge/src/lib.rs`)

### Summary

`update_transfer_fee` in the NEAR omni-bridge contract can be called by any unprivileged account to increase the `native_fee` of any pending transfer at a cost of as little as 1 yoctoNEAR. Because `sign_transfer` validates the stored fee against the caller-supplied fee parameter, an attacker can front-run every relayer signing attempt, causing it to revert with `ERR_INVALID_FEE` and permanently stalling the transfer.

---

### Finding Description

`update_transfer_fee` enforces only one caller restriction: if the **token fee** (`fee.fee`) is being increased, the caller must be the original sender. When the token fee is kept the same, the check passes for any caller:

```rust
require!(
    fee.fee == current_fee.fee          // ← anyone passes if token fee unchanged
        || OmniAddress::Near(env::predecessor_account_id())
            == transfer.message.sender,
    BridgeError::SenderCanUpdateTokenFeeOnly.as_ref()
);
```

The attacker only needs to attach `diff_native_fee` yoctoNEAR (minimum 1 yoctoNEAR) to raise `native_fee` by 1 yoctoNEAR, which overwrites the stored `TransferMessage` in `pending_transfers`:

```rust
transfer.message.fee = fee;
self.insert_raw_transfer(transfer.message.clone(), transfer.owner);
```

`sign_transfer` then reads the updated fee from storage and compares it against the fee the relayer supplied:

```rust
if let Some(fee) = &fee {
    require!(
        &transfer_message.fee == fee,
        BridgeError::InvalidFee.as_ref()
    );
}
```

Because the stored fee has changed, the relayer's call reverts. The attacker can repeat this for every retry, indefinitely, at 1 yoctoNEAR per block.

The same fee check pattern exists in `submit_transfer_to_utxo_chain_connector` in `near/omni-bridge/src/btc.rs`:

```rust
if let Some(fee) = &fee {
    require!(
        &transfer.message.fee == fee,
        BridgeError::InvalidFee.as_ref()
    );
}
```

---

### Impact Explanation

User tokens are locked in the bridge contract the moment `init_transfer` is called. The only path to release them to the destination chain is through `sign_transfer` (or `submit_transfer_to_utxo_chain_connector` for BTC). If the attacker continuously front-runs every signing attempt, the transfer is permanently stuck in `pending_transfers` and the user's tokens are irrecoverably locked in the bridge. This matches the allowed impact: **Critical — permanent freezing / irrecoverable lock of user funds in bridge flows**.

A partial mitigation exists: a relayer can pass `fee: None` to skip the fee equality check. However, this is not the standard relayer behavior (all integration tests pass an explicit fee), and it exposes the relayer to accepting an arbitrarily manipulated fee. If the attacker raises `native_fee` to a value close to the transfer amount before the relayer signs with `fee: None`, the signed payload encodes a fee that drains most of the transfer value to the fee recipient, corrupting the accounting of the bridge.

---

### Likelihood Explanation

- `update_transfer_fee` has no access control beyond the sender restriction for token-fee changes.
- The cost per front-run is 1 yoctoNEAR (≈ $0.000000000001).
- Any NEAR account can execute the attack; no special role, stake, or key is required.
- NEAR's deterministic transaction ordering makes front-running straightforward for any account watching the mempool.
- The attack is economically rational for a griever or a competitor who wants to block a specific cross-chain transfer.

---

### Recommendation

1. **Restrict `update_transfer_fee` to the original sender only** — remove the `fee.fee == current_fee.fee` bypass that allows any caller to update `native_fee`.
2. **Enforce a meaningful minimum increment** (e.g., 0.01 NEAR) to raise the economic cost of repeated front-running.
3. **Add a cooldown / modification interval** between successive fee updates for the same transfer.
4. **Validate that the sender remains solvent** (i.e., the new fee does not exceed the transfer amount) after any update.

---

### Proof of Concept

1. Alice calls `ft_transfer_call` → `init_transfer`, creating a pending transfer with `fee = {fee: 100, native_fee: 0}`.
2. Trusted relayer Bob observes the `InitTransferEvent` and submits `sign_transfer(transfer_id, fee_recipient, fee: Some({fee: 100, native_fee: 0}))`.
3. Attacker Eve front-runs Bob's transaction by calling `update_transfer_fee(transfer_id, UpdateFee::Fee({fee: 100, native_fee: 1}))` with 1 yoctoNEAR attached. The stored transfer message is overwritten with `native_fee = 1`.
4. Bob's `sign_transfer` executes and hits:
   ```
   require!(&transfer_message.fee == fee, BridgeError::InvalidFee)
   // stored: {fee:100, native_fee:1}  ≠  supplied: {fee:100, native_fee:0}
   ```
   → reverts with `ERR_INVALID_FEE`.
5. Eve repeats step 3 for every retry by Bob, incrementing `native_fee` by 1 yoctoNEAR each time.
6. Alice's tokens remain locked in the bridge contract indefinitely.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L386-436)
```rust
    #[payable]
    #[pause]
    pub fn update_transfer_fee(&mut self, transfer_id: TransferId, fee: UpdateFee) {
        match fee {
            UpdateFee::Fee(fee) => {
                let mut transfer = self.get_transfer_message_storage(transfer_id);

                require!(
                    transfer.message.origin_transfer_id.is_none(),
                    BridgeError::UpdateFeeNotAllowedForTransfer.as_ref()
                );

                let current_fee = transfer.message.fee;
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );

                require!(
                    fee.fee == current_fee.fee
                        || OmniAddress::Near(env::predecessor_account_id())
                            == transfer.message.sender,
                    BridgeError::SenderCanUpdateTokenFeeOnly.as_ref()
                );

                let diff_native_fee = fee
                    .native_fee
                    .0
                    .checked_sub(current_fee.native_fee.0)
                    .near_expect(BridgeError::LowerFee);

                require!(
                    NearToken::from_yoctonear(diff_native_fee) == env::attached_deposit(),
                    BridgeError::InvalidAttachedDeposit.as_ref()
                );

                transfer.message.fee = fee;
                self.insert_raw_transfer(transfer.message.clone(), transfer.owner);

                env::log_str(
                    &OmniBridgeEvent::UpdateFeeEvent {
                        transfer_message: transfer.message,
                    }
                    .to_log_string(),
                );
            }
            UpdateFee::Proof(_) => {
                env::panic_str(BridgeError::UnsupportedFeeUpdateProof.to_string().as_str())
            }
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L444-460)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn sign_transfer(
        &mut self,
        transfer_id: TransferId,
        fee_recipient: Option<AccountId>,
        fee: &Option<Fee>,
    ) -> Promise {
        let transfer_message = self.get_transfer_message(transfer_id);

        if let Some(fee) = &fee {
            require!(
                &transfer_message.fee == fee,
                BridgeError::InvalidFee.as_ref()
            );
        }
```

**File:** near/omni-bridge/src/btc.rs (L70-75)
```rust
        if let Some(fee) = &fee {
            require!(
                &transfer.message.fee == fee,
                BridgeError::InvalidFee.as_ref()
            );
        }
```
