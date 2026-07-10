### Title
Silent Swallow of Failed wNEAR Unwrap Permanently Freezes User Funds During `fin_transfer` - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When the Omni Bridge on NEAR finalizes a transfer of wNEAR tokens with an empty message, it attempts to unwrap wNEAR into native NEAR and deliver it to the recipient. If the `near_withdraw` call on the wNEAR contract fails, the subsequent `near_withdraw_callback` panics. However, the final callback in the chain — `fin_transfer_send_tokens_callback` — does **not** inspect the promise result for non-`ft_transfer_call` paths, silently treating the failure as success. The destination nonce is permanently consumed, the user never receives their NEAR, and the wNEAR remains locked in the bridge with no recovery mechanism.

---

### Finding Description

In `send_tokens()`, when the token is `wnear_account_id` and `msg` is empty, the bridge executes a two-step promise chain:

```
near_withdraw  →  near_withdraw_callback  →  fin_transfer_send_tokens_callback
``` [1](#0-0) 

`near_withdraw_callback` panics if `near_withdraw` fails: [2](#0-1) 

`fin_transfer_send_tokens_callback` is chained after `send_tokens()` and receives the promise result. It delegates to `is_refund_required(is_ft_transfer_call)` to decide whether to revert the transfer. For wNEAR with empty `msg`, `is_ft_transfer_call` is `false`: [3](#0-2) 

When `is_ft_transfer_call` is `false`, `is_refund_required` returns `false` **without inspecting the promise result at all**. This means even when `near_withdraw_callback` panicked (delivering a failed promise to `fin_transfer_send_tokens_callback`), the callback takes the success branch: [4](#0-3) 

The success branch:
1. Pays the fee to the fee recipient.
2. Emits `FinTransferEvent` (falsely signaling success).
3. Does **not** call `remove_fin_transfer`, leaving the fin-transfer record in storage.

Meanwhile, `process_fin_transfer_to_near` already called `add_fin_transfer` (consuming the destination nonce) and `unlock_tokens_if_needed` before `send_tokens` was even dispatched: [5](#0-4) 

The result: the destination nonce is permanently consumed, the wNEAR was never burned (since `near_withdraw` failed), the user never receives NEAR, and there is no on-chain path to recover the funds.

---

### Impact Explanation

This is a **permanent, irrecoverable freeze of user funds** in the bridge:

- The destination nonce is consumed by `add_fin_transfer` before `send_tokens` is called. Once the nonce is used, the same proof cannot be re-submitted.
- The wNEAR balance remains in the bridge contract (since `near_withdraw` failed, no wNEAR was burned).
- The user has no callable function to reclaim their wNEAR or retry the NEAR delivery.
- The relayer is paid the fee despite the user receiving nothing.

This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

The `near_withdraw` call can fail in the following realistic scenarios:

1. **Insufficient wNEAR balance**: If the bridge's wNEAR balance is less than the transfer amount due to any accounting discrepancy (e.g., a prior bug, direct wNEAR drain, or migration error), `near_withdraw` will fail.
2. **wNEAR contract paused or upgraded**: If the wNEAR contract is paused or its interface changes, `near_withdraw` will fail.
3. **Gas exhaustion**: `WNEAR_WITHDRAW_GAS` is a static allocation; if the wNEAR contract's `near_withdraw` requires more gas in a future upgrade, the call fails.

Any user bridging wNEAR to NEAR is exposed. The bridge is a public protocol and wNEAR is a primary bridged asset on NEAR.

---

### Recommendation

`fin_transfer_send_tokens_callback` must check the promise result for **all** token transfer paths, not only `ft_transfer_call`. For the wNEAR/plain-transfer path, add an explicit check:

```rust
// In fin_transfer_send_tokens_callback, before the success branch:
if !is_ft_transfer_call {
    // Check if the underlying send_tokens promise (including near_withdraw) succeeded
    if env::promise_result_checked(0, usize::MAX).is_err() {
        // treat as refund: revert lock actions, remove fin transfer, emit failure event
        self.revert_lock_actions(&lock_actions);
        self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);
        env::log_str(&OmniBridgeEvent::FailedFinTransferEvent { transfer_message }.to_log_string());
        return;
    }
}
```

Alternatively, restructure `is_refund_required` to always inspect the promise result and treat any `Err` as requiring a refund, regardless of `is_ft_transfer_call`.

---

### Proof of Concept

1. User on EVM initiates a transfer of wNEAR (mapped to the NEAR wNEAR account) with `msg = ""`.
2. Relayer calls `fin_transfer` on the NEAR bridge with a valid proof.
3. `fin_transfer_callback` → `process_fin_transfer_to_near` → `add_fin_transfer` (nonce consumed) → `send_tokens(wnear, recipient, amount, "")`.
4. `send_tokens` dispatches `near_withdraw` on the wNEAR contract. Suppose the bridge's wNEAR balance is 1 yoctoNEAR short — `near_withdraw` fails.
5. `near_withdraw_callback` receives `Err(_)` and panics with `NearWithdrawFailed`.
6. `fin_transfer_send_tokens_callback` receives the failed promise. `is_refund_required(false)` returns `false` without reading the promise result.
7. The callback takes the success branch: fee is minted to the relayer, `FinTransferEvent` is emitted.
8. The destination nonce is permanently consumed. The user's wNEAR is stuck in the bridge. No recovery is possible. [1](#0-0) [2](#0-1) [6](#0-5) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/lib.rs (L1046-1052)
```rust
    #[private]
    pub fn near_withdraw_callback(&self, recipient: AccountId, amount: NearToken) -> Promise {
        match env::promise_result_checked(0, usize::MAX) {
            Ok(_) => Promise::new(recipient).transfer(amount),
            Err(_) => env::panic_str(BridgeError::NearWithdrawFailed.to_string().as_str()),
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1700-1718)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L1719-1746)
```rust
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
