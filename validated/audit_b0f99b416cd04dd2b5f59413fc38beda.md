### Title
`fin_transfer_send_tokens_callback` Does Not Detect `ft_transfer` / wNEAR Withdrawal Failure, Permanently Bricking NEAR-Bound Withdrawals — (`near/omni-bridge/src/lib.rs`)

---

### Summary

When finalizing a cross-chain transfer to a NEAR recipient, the NEAR omni-bridge marks the transfer as permanently finalised and decrements the locked-token accounting **before** the actual token delivery. If the subsequent `ft_transfer` (or wNEAR `near_withdraw`) call fails, the callback `fin_transfer_send_tokens_callback` does not detect the failure for non-`ft_transfer_call` paths and treats the transfer as successful. The transfer ID is irrevocably consumed, the locked-token balance is permanently decremented, and the user never receives their tokens.

---

### Finding Description

The finalization flow for NEAR-bound transfers is:

1. `fin_transfer` → `fin_transfer_callback` → `process_fin_transfer_to_near`
2. Inside `process_fin_transfer_to_near`:
   - **Line 1875**: `add_fin_transfer` inserts the `TransferId` into `finalised_transfers` (panics if already present — irreversible).
   - **Lines 1881–1885**: `unlock_tokens_if_needed` decrements `locked_tokens` accounting.
   - **Lines 1957–1977**: `send_tokens(...)` is called, chained with `.then(fin_transfer_send_tokens_callback(...))`. [1](#0-0) [2](#0-1) 

3. `send_tokens` dispatches one of three paths depending on the token:
   - **wNEAR + empty msg**: `near_withdraw` → `near_withdraw_callback` (returns a Promise chain, `is_ft_transfer_call = false`)
   - **Non-deployed token + empty msg**: `ft_transfer` (`is_ft_transfer_call = false`)
   - **Deployed token or non-empty msg**: `mint` or `ft_transfer_call` (`is_ft_transfer_call = true`) [3](#0-2) 

4. `fin_transfer_send_tokens_callback` decides whether to revert via `is_refund_required(is_ft_transfer_call)`: [4](#0-3) 

5. `is_refund_required` for `is_ft_transfer_call = false` **unconditionally returns `false`**, regardless of whether the preceding promise succeeded or failed: [5](#0-4) 

**Consequence**: If `ft_transfer` fails (e.g., token contract paused, blacklisted recipient, or actual locker balance below the `locked_tokens` accounting value), or if `near_withdraw` fails (locker holds less wNEAR than accounted), the `.then()` callback is still invoked. Because `is_refund_required(false)` always returns `false`:

- `revert_lock_actions` is **not** called — the locked-token decrement is permanent.
- `remove_fin_transfer` is **not** called — the `TransferId` stays in `finalised_transfers` forever.
- The `else` branch emits `FinTransferEvent` as if delivery succeeded.

The transfer is permanently consumed and the user's funds are irrecoverably lost.

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds** (matches allowed impact).

- The `TransferId` is in `finalised_transfers`; any retry of `fin_transfer` with the same proof panics with `ERR_TRANSFER_ALREADY_FINALISED`.
- The `locked_tokens` balance is permanently decremented, corrupting bridge collateralization accounting.
- No admin escape hatch exists to remove a `TransferId` from `finalised_transfers` or to re-credit `locked_tokens` without a privileged `set_locked_tokens` call. [6](#0-5) 

---

### Likelihood Explanation

The failure condition is reachable by any user whose withdrawal targets a non-deployed native token (e.g., USDC locked in the locker) or wNEAR, under any of the following realistic conditions:

- The token contract is paused or upgraded between the time the source-chain `InitTransfer` event is emitted and the NEAR `fin_transfer` is executed.
- The recipient address is blacklisted by the token contract (e.g., USDC/USDT compliance lists).
- The locker's actual token balance diverges from `locked_tokens` accounting (e.g., due to a direct token transfer into/out of the locker outside the bridge, or a prior accounting bug).
- The wNEAR contract's `near_withdraw` fails because the locker's wNEAR balance is less than the accounted amount.

None of these require attacker action; they are triggered by ordinary bridge usage under adverse but realistic conditions.

---

### Recommendation

In `fin_transfer_send_tokens_callback`, explicitly check the promise result for all paths, not only `ft_transfer_call`. If the preceding promise failed, revert the finalization:

```rust
pub fn fin_transfer_send_tokens_callback(
    &mut self,
    transfer_message: TransferMessage,
    fee_recipient: &AccountId,
    is_ft_transfer_call: bool,
    storage_owner: &AccountId,
    lock_actions: Vec<LockAction>,
) {
    let token = self.get_token_id(&transfer_message.token);

    // Check failure for ALL paths, not just ft_transfer_call
    let transfer_failed = if is_ft_transfer_call {
        Self::is_refund_required(true)
    } else {
        // For ft_transfer and near_withdraw paths, check if the promise failed
        env::promise_result_checked(0, usize::MAX).is_err()
    };

    if transfer_failed {
        self.burn_tokens_if_needed(...);
        self.revert_lock_actions(&lock_actions);
        self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);
        env::log_str(&OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string());
    } else {
        // existing success path
    }
}
```

---

### Proof of Concept

1. User initiates a transfer of a native token (e.g., USDC) from Ethereum to NEAR via `initTransfer` on the EVM bridge. The NEAR locker records the locked amount.
2. Between the EVM event and NEAR finalization, the USDC contract is paused.
3. A relayer calls `fin_transfer` on the NEAR locker with a valid proof.
4. `process_fin_transfer_to_near` runs:
   - `add_fin_transfer` inserts the `TransferId` into `finalised_transfers`. ✓
   - `unlock_tokens_if_needed` decrements `locked_tokens[Eth][usdc]`. ✓
   - `send_tokens` dispatches `ft_transfer(recipient, amount)` on the paused USDC contract. → **Promise fails.**
5. `fin_transfer_send_tokens_callback` is called with `is_ft_transfer_call = false`.
6. `is_refund_required(false)` returns `false`.
7. The `else` branch executes: `FinTransferEvent` is emitted. Transfer considered successful.
8. User retries `fin_transfer` → panics `ERR_TRANSFER_ALREADY_FINALISED`.
9. User's USDC is permanently lost. [7](#0-6) [5](#0-4)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L1875-1885)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());

        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```

**File:** near/omni-bridge/src/lib.rs (L1957-1977)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L2071-2117)
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
        } else if is_deployed_token {
            let deposit = if msg.is_empty() {
                NO_DEPOSIT
            } else {
                ONE_YOCTO
            };

            require!(
                ft_transfer_call_gas >= MIN_FT_TRANSFER_CALL_GAS,
                BridgeError::NotEnoughGasForTokenTransfer(ft_transfer_call_gas).as_ref()
            );

            ext_token::ext(token)
                .with_attached_deposit(deposit)
                .with_static_gas(MINT_TOKEN_GAS.saturating_add(ft_transfer_call_gas))
                .mint(
                    recipient,
                    amount,
                    (!msg.is_empty()).then(|| msg.to_string()),
                )
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        } else {
            require!(
                ft_transfer_call_gas >= MIN_FT_TRANSFER_CALL_GAS,
                BridgeError::NotEnoughGasForTokenTransfer(ft_transfer_call_gas).as_ref()
            );

            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(ft_transfer_call_gas)
                .ft_transfer_call(recipient, amount, None, msg.to_string())
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
