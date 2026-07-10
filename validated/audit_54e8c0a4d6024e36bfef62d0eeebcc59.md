### Title
`fin_transfer_send_tokens_callback` Emits `FinTransferEvent` and Permanently Finalizes Transfer When Token Delivery Fails — (`near/omni-bridge/src/lib.rs`)

---

### Summary

In the NEAR bridge contract, `fin_transfer_send_tokens_callback` uses `is_refund_required` to decide whether to clean up state after a failed token delivery. However, `is_refund_required` unconditionally returns `false` when `is_ft_transfer_call` is `false` (i.e., when the delivery used `ft_transfer` or `mint` without a message). If the underlying token transfer promise fails, the callback still takes the "success" branch: it emits `FinTransferEvent`, sends fees with `.detach()`, and leaves the transfer permanently in `finalised_transfers` with the locked-token accounting already decremented. The user's funds are irrecoverably locked in the bridge with no retry path.

---

### Finding Description

**Vulnerability class**: Callback/state desync — analogous to the PoolTogether `awardExternalERC721` bug where a failed operation is not removed from the success-event list.

**Root cause** — `is_refund_required` in `near/omni-bridge/src/lib.rs`:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => { ... amount.0 == 0 }
            Err(_) => false,   // ← promise panicked: no cleanup
        }
    } else {
        false   // ← ft_transfer / mint path: NEVER checks promise result
    }
}
``` [1](#0-0) 

When `is_ft_transfer_call` is `false`, the function returns `false` without ever inspecting `env::promise_result_checked`. This covers every `ft_transfer` and every `mint` call dispatched without a message.

**State committed before the cross-contract call** — `process_fin_transfer_to_near`:

1. `add_fin_transfer` inserts the transfer ID into `finalised_transfers` (line 1875) — prevents any future replay/retry.
2. `unlock_tokens_if_needed` decrements the locked-token counter (lines 1881–1885).
3. `send_tokens` dispatches `ft_transfer` or `mint` as a cross-contract call (lines 1957–1966).
4. `.then(fin_transfer_send_tokens_callback(...))` chains the callback (lines 1967–1977). [2](#0-1) [3](#0-2) 

In NEAR, the `.then` callback is **always invoked** regardless of whether the preceding promise succeeded or panicked. The callback must inspect `env::promise_result` to detect failure.

**Callback takes the wrong branch on failure** — `fin_transfer_send_tokens_callback`:

```rust
if Self::is_refund_required(is_ft_transfer_call) {
    // correct cleanup: revert_lock_actions, remove_fin_transfer, FailedFinTransferEvent
} else {
    // fee sent with .detach(), FinTransferEvent emitted  ← taken even on failure
}
``` [4](#0-3) 

When `is_ft_transfer_call` is `false` and the token transfer promise failed:
- `remove_fin_transfer` is never called → transfer ID stays in `finalised_transfers` forever.
- `revert_lock_actions` is never called → locked-token counter stays decremented.
- `FinTransferEvent` is emitted claiming success.
- Fee is dispatched with `.detach()` (fire-and-forget). [5](#0-4) 

`add_fin_transfer` uses `require!(self.finalised_transfers.insert(transfer_id), ...)`, so the entry is permanent once inserted and `remove_fin_transfer` is not called. [6](#0-5) 

**Secondary gap**: Even when `is_ft_transfer_call` is `true`, the `Err(_)` arm returns `false`. If `ft_transfer_call` itself panics (e.g., out-of-gas before `ft_on_transfer` is reached), the same desync occurs. [7](#0-6) 

---

### Impact Explanation

When `ft_transfer` or `mint` fails (promise panics):

| State | Expected | Actual |
|---|---|---|
| `finalised_transfers` | Removed (retry allowed) | Permanently set (no retry) |
| Locked-token counter | Restored | Stays decremented (accounting corruption) |
| Event emitted | `FailedFinTransferEvent` | `FinTransferEvent` (false success) |
| User tokens | Returned or retried | Permanently locked in bridge |

This matches **Critical: Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows** and **High: Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value**.

Off-chain indexers and relayers that observe `FinTransferEvent` will treat the transfer as complete and will not attempt recovery, compounding the loss.

---

### Likelihood Explanation

The `ft_transfer` path (non-deployed tokens, no message) is the standard path for locked native tokens — the highest-value assets in the bridge. Failure can occur due to:

- **Out-of-gas**: `FT_TRANSFER_GAS` is a static constant. If the token contract's `ft_transfer` consumes more gas than allocated (e.g., a complex token with hooks), the promise panics.
- **Token contract panic**: Any panic in the token contract during `ft_transfer` (e.g., arithmetic overflow, storage issue) triggers this path.
- **Mint failure**: For deployed bridge tokens, if `mint` panics (e.g., out-of-gas, contract bug), the same desync occurs.

The `msg`-empty path is the default for most bridge transfers (no receiver contract interaction), making this the most commonly exercised code path.

---

### Recommendation

In `fin_transfer_send_tokens_callback`, check the promise result for **all** token delivery paths, not only `ft_transfer_call`:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
        Err(_) => true,  // promise panicked → always require cleanup
        Ok(value) if is_ft_transfer_call => {
            near_sdk::serde_json::from_slice::<U128>(&value)
                .map(|a| a.0 == 0)
                .unwrap_or(false)
        }
        Ok(_) => false,  // ft_transfer / mint succeeded
    }
}
```

This ensures that any promise failure — regardless of whether it was `ft_transfer`, `mint`, or `ft_transfer_call` — triggers the correct cleanup path: `revert_lock_actions`, `remove_fin_transfer`, and `FailedFinTransferEvent`.

---

### Proof of Concept

1. User initiates a transfer from EVM to NEAR for a non-deployed (locked) token with an empty `msg` field. The EVM `InitTransfer` event is emitted and the proof is submitted to the NEAR bridge via `fin_transfer`.

2. `fin_transfer_callback` calls `process_fin_transfer_to_near`:
   - `add_fin_transfer` inserts the transfer ID into `finalised_transfers`.
   - `unlock_tokens_if_needed` decrements the locked-token counter.
   - `send_tokens` dispatches `ft_transfer` with `FT_TRANSFER_GAS`.

3. The token contract's `ft_transfer` panics (e.g., out-of-gas, or a storage edge case). The promise result is `PromiseResult::Failed`.

4. `fin_transfer_send_tokens_callback` is invoked. `is_ft_transfer_call` is `false` (empty `msg`). `is_refund_required(false)` returns `false` unconditionally.

5. The callback takes the `else` branch:
   - `FinTransferEvent` is emitted with the full transfer details.
   - Fee is dispatched with `.detach()`.
   - `remove_fin_transfer` is **not** called.
   - `revert_lock_actions` is **not** called.

6. Result: The transfer ID is permanently in `finalised_transfers`. Any subsequent `fin_transfer` call with the same proof reverts with `TransferAlreadyFinalised`. The locked-token counter is permanently decremented. The user's tokens are locked in the bridge with no recovery path. Off-chain systems observe `FinTransferEvent` and consider the transfer complete. [1](#0-0) [8](#0-7) [9](#0-8) [6](#0-5) [10](#0-9)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1692-1746)
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
```

**File:** near/omni-bridge/src/lib.rs (L1784-1804)
```rust
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
    }
```

**File:** near/omni-bridge/src/lib.rs (L1867-1978)
```rust
    #[allow(clippy::too_many_lines, clippy::ptr_arg)]
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

**File:** near/omni-types/src/near_events.rs (L21-26)
```rust
    FinTransferEvent {
        transfer_message: TransferMessage,
    },
    FailedFinTransferEvent {
        transfer_message: TransferMessage,
    },
```
