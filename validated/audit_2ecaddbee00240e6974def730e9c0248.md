### Title
`fin_transfer_send_tokens_callback` Ignores `send_tokens` Failure for Non-`ft_transfer_call` Cases, Permanently Freezing User Funds — (File: `near/omni-bridge/src/lib.rs`)

### Summary
When finalizing a transfer to NEAR where the token is wNEAR (unwrap path) or a plain non-deployed token (`ft_transfer` path), `fin_transfer_send_tokens_callback` does not check whether the underlying `send_tokens` call succeeded. If the external token transfer fails — wNEAR contract paused, token contract paused, or recipient blacklisted — the bridge permanently marks the transfer as finalized and corrupts locked-token accounting, but never delivers the tokens to the recipient. The funds are irrecoverably frozen in the bridge contract with no retry mechanism.

### Finding Description
`process_fin_transfer_to_near` commits two critical state changes **before** dispatching `send_tokens`:

1. `add_fin_transfer` (line 1875) — inserts the transfer ID into `finalised_transfers`, preventing any future replay or retry.
2. `unlock_tokens_if_needed` (line 1881–1885) — decrements `locked_tokens` accounting. [1](#0-0) 

These state changes are committed to storage when `process_fin_transfer_to_near` returns its Promise. Token delivery is then attempted asynchronously via `send_tokens`, with `fin_transfer_send_tokens_callback` as the resolution callback. [2](#0-1) 

The callback uses `is_refund_required` to decide whether to revert state:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        // checks promise result and may return true
    } else {
        false  // always "success" — never reverts
    }
}
``` [3](#0-2) 

For the wNEAR unwrap path and the plain `ft_transfer` path, `is_ft_transfer_call` is `false` (because `msg` is empty). So `is_refund_required` always returns `false`, and `fin_transfer_send_tokens_callback` always takes the "success" branch — even when the underlying promise failed. [4](#0-3) 

For the wNEAR path specifically, `near_withdraw_callback` panics on failure:

```rust
Err(_) => env::panic_str(BridgeError::NearWithdrawFailed.to_string().as_str()),
``` [5](#0-4) 

This panic propagates to `fin_transfer_send_tokens_callback` as a failed promise result. Since `is_ft_transfer_call` is `false`, the callback ignores the failure and logs `FinTransferEvent` as if the transfer succeeded.

For the plain `ft_transfer` path (non-deployed token, empty `msg`): [6](#0-5) 

If `ft_transfer` fails (token paused, recipient blacklisted, etc.), the same logic applies: `fin_transfer_send_tokens_callback` takes the success path.

### Impact Explanation
When `send_tokens` fails for a non-`ft_transfer_call` case:
1. The transfer is permanently marked as finalized — any retry attempt fails with `TransferAlreadyFinalised`.
2. `locked_tokens` accounting is decremented — bridge collateralization is broken.
3. The tokens (wNEAR or native FT) remain in the bridge contract — irrecoverably frozen.
4. The recipient receives nothing.

This matches the **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds** impact category.

### Likelihood Explanation
Triggerable whenever the external token contract (wNEAR or any non-deployed FT) rejects the transfer:
- wNEAR contract paused or upgraded.
- Token contract (e.g., USDC, USDT) paused due to a security incident — both have on-chain pause mechanisms.
- Recipient address blacklisted by the token contract (USDC/USDT maintain blacklists; the recipient address is user-controlled and specified in the original cross-chain transfer).
- Insufficient gas allocated to `near_withdraw` or `ft_transfer` causing the call to fail.

The recipient address is specified by the originating user on the source chain. A user whose recipient is later blacklisted, or who bridges to a chain where the token is paused, would have their funds permanently frozen with no recourse.

### Recommendation
`fin_transfer_send_tokens_callback` must check the promise result for **all** cases, not only `ft_transfer_call`. When the promise fails (regardless of `is_ft_transfer_call`), the callback should:
1. Call `revert_lock_actions` to restore `locked_tokens` accounting.
2. Call `remove_fin_transfer` to remove the finalization record and allow retry.
3. Log `FailedFinTransferEvent`.

Concretely, `is_refund_required` should be extended to also handle the non-`ft_transfer_call` failure case by checking `env::promise_result_checked` when `is_ft_transfer_call` is `false`.

### Proof of Concept
1. User initiates a transfer from EVM to NEAR for wNEAR (or any non-deployed token with empty `msg`).
2. Relayer calls `fin_transfer` with valid proof.
3. `fin_transfer_callback` → `process_fin_transfer_to_near`:
   - `add_fin_transfer` marks transfer as finalized (committed to state).
   - `unlock_tokens_if_needed` decrements locked tokens (committed to state).
   - `send_tokens` dispatches `near_withdraw` (wNEAR path).
4. wNEAR contract fails (paused/bug) → `near_withdraw_callback` panics with `NearWithdrawFailed`.
5. `fin_transfer_send_tokens_callback` is called with a failed promise result.
6. `is_ft_transfer_call = false` → `is_refund_required` returns `false` without inspecting the promise result.
7. Callback takes the success branch, logs `FinTransferEvent`.
8. Transfer is permanently finalized, locked tokens are decremented, but recipient receives 0 tokens.
9. Any retry attempt fails: `TransferAlreadyFinalised`. [7](#0-6)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1047-1052)
```rust
    pub fn near_withdraw_callback(&self, recipient: AccountId, amount: NearToken) -> Promise {
        match env::promise_result_checked(0, usize::MAX) {
            Ok(_) => Promise::new(recipient).transfer(amount),
            Err(_) => env::panic_str(BridgeError::NearWithdrawFailed.to_string().as_str()),
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1692-1718)
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

**File:** near/omni-bridge/src/lib.rs (L2071-2082)
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
```

**File:** near/omni-bridge/src/lib.rs (L2102-2107)
```rust
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        } else {
```
