### Title
`fin_transfer_send_tokens_callback` Ignores `ft_transfer` Failure for Non-Deployed Tokens, Permanently Locking User Funds - (File: near/omni-bridge/src/lib.rs)

### Summary

When finalizing a cross-chain transfer to a NEAR recipient for a **non-deployed (native) token** with no message, the bridge calls `ft_transfer` on the token contract. If that call fails (panics/reverts), the callback `fin_transfer_send_tokens_callback` does not detect the failure and proceeds as if the transfer succeeded. The transfer is already marked as finalized and locked tokens already decremented before `ft_transfer` is dispatched, so the user's funds are permanently locked in the bridge with no recovery path.

### Finding Description

The vulnerability class is identical to the XC20Wrapper report: an external token-delivery call can fail, but the failure is not caught, causing permanent fund loss.

**Root cause — `is_refund_required` blindly returns `false` for `ft_transfer`:** [1](#0-0) 

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => { ... }
            Err(_) => false,   // promise failed → no refund
        }
    } else {
        false   // ← never checks the promise result at all
    }
}
```

When `msg` is empty and the token is non-deployed, `send_tokens` dispatches `ft_transfer` (not `ft_transfer_call`): [2](#0-1) 

And `process_fin_transfer_to_near` passes `!msg.is_empty()` as `is_ft_transfer_call`: [3](#0-2) 

So when `msg` is empty, `is_ft_transfer_call = false`, and `is_refund_required` returns `false` **without ever reading the promise result**. A failed `ft_transfer` is silently treated as success.

**State mutations that happen before `ft_transfer` is dispatched and cannot be undone:**

1. `add_fin_transfer` inserts the transfer ID into `finalised_transfers` — preventing any future retry: [4](#0-3) 

2. `unlock_tokens_if_needed` decrements the locked-token counter: [5](#0-4) 

When `ft_transfer` fails, the callback's else-branch fires: [6](#0-5) 

It logs `FinTransferEvent` and optionally mints fees — treating the transfer as complete — while the user's tokens remain stranded in the bridge contract. The `remove_fin_transfer` call that would re-open the transfer for retry is only in the `is_refund_required == true` branch, which is never reached.

### Impact Explanation

**Critical — Permanent irrecoverable lock of user funds.**

- The transfer ID is in `finalised_transfers`; `fin_transfer` will revert with `TransferAlreadyFinalised` on any retry.
- The locked-token counter is already decremented; the accounting is corrupted.
- There is no admin rescue function that removes a finalized transfer and re-credits the user.
- Tokens remain in the bridge contract indefinitely with no on-chain recovery path.

### Likelihood Explanation

Non-deployed tokens are third-party contracts (e.g., USDC, USDT, wrapped assets). Many production fungible tokens implement a **pause mechanism** that causes `ft_transfer` to panic when paused. A token pause during the window between `fin_transfer_callback` and the `ft_transfer` execution is a realistic, non-adversarial scenario. Additionally, any bug or unexpected panic in the token contract triggers the same outcome. The bridge has no control over third-party token contract behavior.

### Recommendation

In `is_refund_required`, also check the promise result when `is_ft_transfer_call == false`:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => {
                near_sdk::serde_json::from_slice::<U128>(&value)
                    .map(|a| a.0 == 0)
                    .unwrap_or(false)
            }
            Err(_) => true,  // promise failed → refund
        }
    } else {
        // ft_transfer: treat a failed promise as requiring refund
        env::promise_result_checked(0, 0).is_err()
    }
}
```

This mirrors the XC20Wrapper mitigation: catch the failure and fall back to a safe state (revert lock actions, remove the finalized-transfer record, emit `FailedFinTransferEvent`) so the relayer can retry.

### Proof of Concept

1. User bridges 1000 USDC from Ethereum to NEAR (USDC is a non-deployed token on NEAR — the bridge holds the native USDC).
2. Relayer calls `fin_transfer` with valid proof and storage deposit actions.
3. `fin_transfer_callback` → `process_fin_transfer_to_near`:
   - `add_fin_transfer` marks transfer as finalized.
   - `unlock_tokens_if_needed` decrements locked USDC by 1000.
   - `send_tokens` dispatches `ft_transfer(recipient, 1000)` on the USDC contract.
4. USDC contract is paused at this moment → `ft_transfer` panics → promise result = Failed.
5. `fin_transfer_send_tokens_callback` is called with `is_ft_transfer_call = false`.
6. `is_refund_required(false)` returns `false` without checking the promise result.
7. Else-branch executes: logs `FinTransferEvent`, transfer considered complete.
8. User has received 0 USDC. The 1000 USDC sit in the bridge contract.
9. Any retry of `fin_transfer` fails with `TransferAlreadyFinalised`.
10. Funds are permanently locked. [7](#0-6) [1](#0-0) [8](#0-7)

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

**File:** near/omni-bridge/src/lib.rs (L1784-1803)
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
```

**File:** near/omni-bridge/src/lib.rs (L1875-1875)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());
```

**File:** near/omni-bridge/src/lib.rs (L1881-1885)
```rust
        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];
```

**File:** near/omni-bridge/src/lib.rs (L1967-1977)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L2102-2117)
```rust
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
