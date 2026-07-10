### Title
`fin_transfer_send_tokens_callback` Ignores Promise Failure for Non-`ft_transfer_call` Paths, Permanently Freezing User Funds When Underlying Token Transfer Fails - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

In the NEAR bridge contract, `process_fin_transfer_to_near` commits the transfer's finalization record to `finalised_transfers` synchronously inside `fin_transfer_callback`, then schedules the actual token delivery (`send_tokens`) as a subsequent async promise. The callback `fin_transfer_send_tokens_callback` is supposed to detect delivery failure and revert the finalization, but it only does so when `is_ft_transfer_call = true`. For wNEAR unwraps and plain `ft_transfer` paths (`is_ft_transfer_call = false`), the callback never inspects the promise result. If the underlying call panics (e.g., wNEAR is paused, or a non-deployed token's `ft_transfer` reverts), the transfer ID remains permanently in `finalised_transfers` while the user's tokens are never delivered — an irrecoverable lock with no admin escape hatch.

---

### Finding Description

**Step 1 — Finalization committed before delivery**

Inside `fin_transfer_callback` (line 700), when the recipient is a NEAR address, `process_fin_transfer_to_near` is called synchronously. The very first thing it does is:

```rust
let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
```

`add_fin_transfer` inserts the `TransferId` into `self.finalised_transfers` (line 2229). Because this runs inside the `fin_transfer_callback` execution frame, the insertion is committed to state when that frame completes — before any async promise runs. [1](#0-0) [2](#0-1) 

**Step 2 — Token delivery is async**

After committing the finalization, `process_fin_transfer_to_near` calls `send_tokens` and chains `fin_transfer_send_tokens_callback`:

```rust
self.send_tokens(token.clone(), recipient, amount, &msg)
    .then(
        Self::ext(env::current_account_id())
            .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
            .fin_transfer_send_tokens_callback(transfer_message, &fee_recipient,
                !msg.is_empty(), predecessor_account_id, lock_actions),
    )
``` [3](#0-2) 

For wNEAR (token == `wnear_account_id` and `msg` is empty), `send_tokens` returns the chain `near_withdraw → near_withdraw_callback`. If `near_withdraw` fails, `near_withdraw_callback` panics unconditionally:

```rust
Err(_) => env::panic_str(BridgeError::NearWithdrawFailed.to_string().as_str()),
``` [4](#0-3) 

For plain non-deployed tokens with empty `msg`, `send_tokens` issues a single `ft_transfer` call. If that panics, the result propagated to `fin_transfer_send_tokens_callback` is `Failed`. [5](#0-4) 

**Step 3 — Callback blindly succeeds for non-`ft_transfer_call` paths**

`fin_transfer_send_tokens_callback` delegates failure detection entirely to `is_refund_required`:

```rust
if Self::is_refund_required(is_ft_transfer_call) {
    // revert: remove_fin_transfer, burn, revert_lock_actions
} else {
    // success path: send fee, emit FinTransferEvent
}
```

`is_refund_required` is:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) { ... }
    } else {
        false   // ← always false; promise result never inspected
    }
}
``` [6](#0-5) [7](#0-6) 

When `is_ft_transfer_call = false` (wNEAR path, plain `ft_transfer` path), the function **never calls `env::promise_result_checked`**. Regardless of whether the delivery promise panicked or succeeded, it always falls into the `else` branch, emits `FinTransferEvent`, and returns. `remove_fin_transfer` is never called. [8](#0-7) 

**Result**: `finalised_transfers` permanently contains the `TransferId`. Any future retry of `fin_transfer` for the same origin nonce hits `BridgeError::TransferAlreadyFinalised` at line 2230. The user's tokens are locked in the bridge with no recovery path.

---

### Impact Explanation

This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows.**

- The user's source-chain tokens were already consumed (locked or burned) when `initTransfer` was called on the origin chain.
- On NEAR, the transfer is recorded as finalized, so no relayer can re-submit it.
- The bridge holds the locked tokens (or the wNEAR balance) indefinitely with no admin function to forcibly remove a `TransferId` from `finalised_transfers` and retry delivery.
- There is no user-callable escape hatch.

---

### Likelihood Explanation

The trigger is any condition that causes the `send_tokens` promise to panic after `fin_transfer_callback` has already committed the finalization:

1. **wNEAR paused**: The wNEAR contract on NEAR has an administrative pause mechanism. If it is paused (analogous to `daiJoin.cage` in the external report), `near_withdraw` panics, `near_withdraw_callback` panics, and `fin_transfer_send_tokens_callback` silently succeeds. Every in-flight NEAR-recipient transfer using wNEAR is permanently frozen.
2. **Non-deployed token paused or blacklisted**: Any ERC-20-style NEP-141 token that supports pausing (e.g., USDC on NEAR) can have its `ft_transfer` revert. Same outcome.
3. **Insufficient bridge balance**: If the bridge's token balance is somehow depleted (accounting bug, separate exploit), `ft_transfer` panics and the same freeze occurs.

Scenarios 1 and 2 require an external administrative action, but the Omni Bridge code's own failure to inspect the promise result is the necessary co-cause — without the callback bug, the finalization would be reverted and users could retry after the token is unpaused.

---

### Recommendation

`fin_transfer_send_tokens_callback` must inspect the promise result for **all** delivery paths, not only `ft_transfer_call`. A minimal fix:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => serde_json::from_slice::<U128>(&value)
                .map(|a| a.0 == 0)
                .unwrap_or(false),
            Err(_) => true,   // delivery failed → refund
        }
    } else {
        // For ft_transfer / near_withdraw paths, treat any promise failure as a refund
        env::promise_result_checked(0, 0).is_err()
    }
}
```

Additionally, `near_withdraw_callback` should not panic on failure; instead it should return a sentinel value so the downstream `fin_transfer_send_tokens_callback` can detect the failure cleanly.

---

### Proof of Concept

1. User bridges wNEAR from ETH → NEAR. Source-chain `initTransfer` locks/burns tokens.
2. wNEAR contract admin pauses wNEAR (e.g., emergency response).
3. Relayer calls `fin_transfer` on NEAR with the proof.
4. `fin_transfer_callback` executes:
   - `add_fin_transfer` inserts `TransferId{origin_chain: Eth, origin_nonce: N}` into `finalised_transfers`. **State committed.**
5. `near_withdraw` is called on the paused wNEAR contract → panics.
6. `near_withdraw_callback` receives `Err(_)` → `env::panic_str("NearWithdrawFailed")`.
7. `fin_transfer_send_tokens_callback` receives `Failed` result.
8. `is_refund_required(false)` → `false`. Callback takes the success branch, emits `FinTransferEvent`. **No tokens sent. No finalization reverted.**
9. wNEAR is later unpaused. Relayer retries `fin_transfer` → `BridgeError::TransferAlreadyFinalised`. **Permanently stuck.**
10. User's funds are irrecoverably locked in the bridge. [9](#0-8) [10](#0-9) [11](#0-10) [12](#0-11) [13](#0-12) [4](#0-3)

### Citations

**File:** near/omni-bridge/src/lib.rs (L700-746)
```rust
    pub fn fin_transfer_callback(
        &mut self,
        #[serializer(borsh)] storage_deposit_actions: &Vec<StorageDepositAction>,
        #[serializer(borsh)] predecessor_account_id: AccountId,
    ) -> PromiseOrValue<Nonce> {
        let Ok(ProverResult::InitTransfer(init_transfer)) = Self::decode_prover_result(0) else {
            env::panic_str(BridgeError::InvalidProofMessage.to_string().as_str())
        };
        require!(
            self.factories
                .get(&init_transfer.emitter_address.get_chain())
                == Some(init_transfer.emitter_address),
            BridgeError::UnknownFactory.as_ref()
        );

        let decimals = self
            .token_decimals
            .get(&init_transfer.token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

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
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1047-1051)
```rust
    pub fn near_withdraw_callback(&self, recipient: AccountId, amount: NearToken) -> Promise {
        match env::promise_result_checked(0, usize::MAX) {
            Ok(_) => Promise::new(recipient).transfer(amount),
            Err(_) => env::panic_str(BridgeError::NearWithdrawFailed.to_string().as_str()),
        }
```

**File:** near/omni-bridge/src/lib.rs (L1692-1747)
```rust
    pub fn fin_transfer_send_tokens_callback(
        &mut self,
        #[serializer(borsh)] transfer_message: TransferMessage,
        #[serializer(borsh)] fee_recipient: &AccountId,
        #[serializer(borsh)] is_ft_transfer_call: bool,
        #[serializer(borsh)] storage_owner: &AccountId,
        #[serializer(borsh)] lock_actions: Vec<LockAction>,
    ) {
        let token = self.get_token_id(&transfer_message.token);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.burn_tokens_if_needed(
                token.clone(),
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
            );

            self.revert_lock_actions(&lock_actions);

            self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);

            env::log_str(
                &OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string(),
            );
        } else {
            // Send fee to the fee recipient
            if transfer_message.fee.fee.0 > 0 {
                if self.is_deployed_token(&token) {
                    ext_token::ext(token)
                        .with_static_gas(MINT_TOKEN_GAS)
                        .mint(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                } else {
                    ext_token::ext(token)
                        .with_attached_deposit(ONE_YOCTO)
                        .with_static_gas(FT_TRANSFER_GAS)
                        .ft_transfer(fee_recipient.clone(), transfer_message.fee.fee, None)
                        .detach();
                }
            }

            if transfer_message.fee.native_fee.0 > 0 {
                let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

                ext_token::ext(native_token_id)
                    .with_static_gas(MINT_TOKEN_GAS)
                    .mint(fee_recipient.clone(), transfer_message.fee.native_fee, None)
                    .detach();
            }

            env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1783-1803)
```rust
impl Contract {
    fn is_refund_required(is_ft_transfer_call: bool) -> bool {
        if is_ft_transfer_call {
            match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
                Ok(value) => {
                    if let Ok(amount) = near_sdk::serde_json::from_slice::<U128>(&value) {
                        // Normal case: refund if the used token amount is zero
                        // The amount can be zero if the `ft_on_transfer` in the receiver contract returns an amount instead of `0`, or if it panics.
                        amount.0 == 0
                    } else {
                        // Unexpected case: don't refund
                        false
                    }
                }
                // Unexpected case: don't refund
                Err(_) => false,
            }
        } else {
            // Not ft_transfer_call: don't refund
            false
        }
```

**File:** near/omni-bridge/src/lib.rs (L1868-1978)
```rust
    fn process_fin_transfer_to_near(
        &mut self,
        recipient: AccountId,
        predecessor_account_id: &AccountId,
        transfer_message: TransferMessage,
        storage_deposit_actions: &Vec<StorageDepositAction>,
    ) -> Promise {
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());

        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];

        // If fast transfer happened, change recipient and fee recipient to the relayer that executed fast transfer
        let (recipient, msg, fee_recipient) = match fast_transfer_status {
            Some(status) => {
                require!(
                    !status.finalised,
                    BridgeError::FastTransferAlreadyFinalised.as_ref()
                );
                self.remove_fast_transfer(&fast_transfer.id());
                (status.relayer.clone(), String::new(), status.relayer)
            }
            None => (
                recipient,
                transfer_message.msg.clone(),
                predecessor_account_id.clone(),
            ),
        };

        let mut storage_deposit_action_index: usize = 0;
        require!(
            Self::check_storage_balance_result(
                (storage_deposit_action_index + 1)
                    .try_into()
                    .near_expect(BridgeError::Cast)
            ) && storage_deposit_actions[storage_deposit_action_index].account_id == recipient
                && storage_deposit_actions[storage_deposit_action_index].token_id == token,
            BridgeError::StorageRecipientOmitted.as_ref()
        );
        storage_deposit_action_index += 1;

        // One yoctoNear is required to send tokens to the recipient
        required_balance = required_balance.saturating_add(ONE_YOCTO);

        if transfer_message.fee.fee.0 > 0 {
            require!(
                Self::check_storage_balance_result(
                    (storage_deposit_action_index + 1)
                        .try_into()
                        .near_expect(BridgeError::Cast)
                ) && storage_deposit_actions[storage_deposit_action_index].account_id
                    == fee_recipient
                    && storage_deposit_actions[storage_deposit_action_index].token_id == token,
                BridgeError::StorageFeeRecipientOmitted.as_ref()
            );
            storage_deposit_action_index += 1;

            required_balance = required_balance.saturating_add(ONE_YOCTO);
        }

        if transfer_message.fee.native_fee.0 > 0 {
            let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

            require!(
                Self::check_storage_balance_result(
                    (storage_deposit_action_index + 1)
                        .try_into()
                        .near_expect(BridgeError::Cast)
                ) && storage_deposit_actions[storage_deposit_action_index].account_id
                    == fee_recipient
                    && storage_deposit_actions[storage_deposit_action_index].token_id
                        == native_token_id,
                BridgeError::StorageNativeFeeRecipientOmitted.as_ref()
            );
        }

        self.update_storage_balance(
            predecessor_account_id.clone(),
            required_balance,
            env::attached_deposit(),
        );

        self.send_tokens(
            token.clone(),
            recipient,
            U128(
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            ),
            &msg,
        )
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(SEND_TOKENS_CALLBACK_GAS)
                .fin_transfer_send_tokens_callback(
                    transfer_message,
                    &fee_recipient,
                    !msg.is_empty(),
                    predecessor_account_id,
                    lock_actions,
                ),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L2071-2081)
```rust
        if token == self.wnear_account_id && msg.is_empty() {
            // Unwrap wNEAR and transfer NEAR tokens
            ext_wnear_token::ext(self.wnear_account_id.clone())
                .with_static_gas(WNEAR_WITHDRAW_GAS)
                .with_attached_deposit(ONE_YOCTO)
                .near_withdraw(amount)
                .then(
                    Self::ext(env::current_account_id())
                        .with_static_gas(NEAR_WITHDRAW_CALLBACK_GAS)
                        .near_withdraw_callback(recipient, NearToken::from_yoctonear(amount.0)),
                )
```

**File:** near/omni-bridge/src/lib.rs (L2102-2106)
```rust
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
```

**File:** near/omni-bridge/src/lib.rs (L2226-2234)
```rust
    fn add_fin_transfer(&mut self, transfer_id: &TransferId) -> NearToken {
        let storage_usage = env::storage_usage();
        require!(
            self.finalised_transfers.insert(transfer_id),
            BridgeError::TransferAlreadyFinalised.as_ref()
        );
        env::storage_byte_cost()
            .saturating_mul((env::storage_usage().saturating_sub(storage_usage)).into())
    }
```
