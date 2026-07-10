### Title
Permanent Fund Freeze in `process_fin_transfer_to_near` When `ft_transfer` Fails for Blacklisted Recipient — (`near/omni-bridge/src/lib.rs`)

---

### Summary

When finalizing a cross-chain transfer to a NEAR recipient, the NEAR bridge permanently marks the transfer as finalized **before** the token transfer is attempted. If the subsequent `ft_transfer` call fails (e.g., because the NEP-141 token contract implements a blacklist and the recipient is blacklisted), the callback silently ignores the failure and takes no refund action. The transfer nonce is permanently consumed, the tokens remain stuck in the bridge contract, and there is no recovery path.

---

### Finding Description

In `process_fin_transfer_to_near`, the sequence is:

1. `add_fin_transfer` is called at line 1875, permanently inserting the transfer ID into `finalised_transfers`. Any subsequent call with the same transfer ID will panic with `ERR_TRANSFER_ALREADY_FINALISED`. [1](#0-0) 

2. After storage checks, `send_tokens` is called at line 1957 to deliver tokens to the recipient. For a non-deployed token with no `msg`, this resolves to a plain `ft_transfer` call: [2](#0-1) 

3. The callback `fin_transfer_send_tokens_callback` is chained. It receives `is_ft_transfer_call = !msg.is_empty()`, which is `false` for a plain transfer. [3](#0-2) 

4. Inside `fin_transfer_send_tokens_callback`, the refund path is gated on `is_refund_required(is_ft_transfer_call)`: [4](#0-3) 

5. `is_refund_required` for `is_ft_transfer_call = false` **unconditionally returns `false`**, without ever inspecting the promise result of the `ft_transfer` call: [5](#0-4) 

This means: if `ft_transfer` fails (promise result is `Failed`), the callback proceeds to the "success" branch, sends the fee to the relayer, and logs a `FinTransferEvent`. The transfer ID remains permanently in `finalised_transfers`, the tokens remain in the bridge contract, and there is no mechanism to recover them.

`add_fin_transfer` enforces uniqueness: [6](#0-5) 

There is no user-callable cancel or refund function in the contract. `remove_fin_transfer` is only called internally from the refund branch of `fin_transfer_send_tokens_callback`, which is never reached for `ft_transfer` failures.

---

### Impact Explanation

**Critical — Permanent, irrecoverable lock of user funds in the bridge contract.**

- The transfer nonce is permanently consumed; the same proof cannot be replayed.
- The tokens remain in the NEAR bridge contract with no withdrawal path.
- The relayer receives its fee (success path), so there is no external signal of failure.
- No admin or DAO function exists to cancel a finalized transfer and return funds.

---

### Likelihood Explanation

**Moderate.** The trigger requires a NEP-141 token that implements a blacklist (analogous to USDC's `blacklist` on EVM). Such tokens exist and are explicitly in scope per the audit README (Blocklists of ERC20/NEP-141 tokens are in scope). A user whose NEAR address is blacklisted by the token contract after initiating a cross-chain transfer, or who is blacklisted before the relayer finalizes the transfer, will have their funds permanently frozen. The relayer has no incentive to avoid submitting the finalization (it earns a fee regardless), and the user has no recourse.

---

### Recommendation

In `fin_transfer_send_tokens_callback`, check the promise result for **all** token transfer types, not only `ft_transfer_call`. Specifically:

- For `ft_transfer` (no msg), read `env::promise_result_checked(0, ...)` and, if the result is `Failed`, execute the same refund path that is already implemented for `ft_transfer_call` failures: burn deployed tokens if needed, revert lock actions, remove the finalized transfer record, and return the amount to the relayer/caller.

Alternatively, move `add_fin_transfer` to occur only after a confirmed successful token delivery (inside the success branch of the callback), so that a failed delivery leaves the nonce unconsumed and retryable.

---

### Proof of Concept

1. A USDC-equivalent NEP-141 token (with blacklisting) is registered in the Omni Bridge on NEAR.
2. A user on EVM initiates a transfer of this token to a NEAR recipient address.
3. The NEAR bridge relayer calls `fin_transfer` → `fin_transfer_callback` → `process_fin_transfer_to_near`.
4. `add_fin_transfer` permanently records the transfer ID in `finalised_transfers`.
5. `send_tokens` issues `ft_transfer(recipient, amount, None)` to the token contract.
6. The token contract's blacklist check rejects the transfer; the `ft_transfer` promise result is `Failed`.
7. `fin_transfer_send_tokens_callback` is invoked with `is_ft_transfer_call = false`.
8. `is_refund_required(false)` returns `false`; the callback enters the success branch.
9. The relayer fee is paid; `FinTransferEvent` is emitted.
10. The transfer ID is permanently finalized; the tokens remain in the bridge contract; the user's funds are irrecoverably frozen.

### Citations

**File:** near/omni-bridge/src/lib.rs (L1702-1718)
```rust
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
