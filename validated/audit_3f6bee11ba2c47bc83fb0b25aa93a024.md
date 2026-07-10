### Title
Failing Token Delivery in `fin_transfer_send_tokens_callback` Permanently Freezes User Funds — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When finalizing a cross-chain transfer to NEAR, the bridge marks the transfer as permanently finalized in `finalised_transfers` before the actual token delivery completes. If the token delivery call (`ft_transfer`, `mint`, or `ft_transfer_call`) panics, `fin_transfer_send_tokens_callback` does not detect the failure and proceeds to log `FinTransferEvent` as if the transfer succeeded. The transfer is irrecoverably marked as finalized, the tokens remain stuck in the bridge (or are never minted), and no retry is possible.

---

### Finding Description

**Step 1 — Finalization mark is set before delivery:**

In `process_fin_transfer_to_near`, `add_fin_transfer` is called first, inserting the transfer ID into `finalised_transfers` as replay protection. Only then is `send_tokens` dispatched as a cross-contract promise. [1](#0-0) [2](#0-1) 

**Step 2 — `send_tokens` dispatches to one of three paths:**

- `ft_transfer` for non-deployed tokens with empty `msg` (line 2103–2106)
- `mint` for deployed/bridge tokens (line 2094–2101)
- `ft_transfer_call` for non-empty `msg` (line 2113–2116) [3](#0-2) 

**Step 3 — The callback never checks the promise result for `ft_transfer` or `mint`:**

`fin_transfer_send_tokens_callback` calls `is_refund_required(is_ft_transfer_call)`. The `is_ft_transfer_call` flag is set to `!msg.is_empty()`, so it is `false` for plain `ft_transfer` and `mint`. When `false`, `is_refund_required` unconditionally returns `false` without reading the promise result at all. [4](#0-3) 

Even for `ft_transfer_call` (`is_ft_transfer_call = true`), if the call itself panics (not just `ft_on_transfer`), `env::promise_result_checked` returns `Err(_)` and the function also returns `false` — the same silent-success path. [5](#0-4) 

**Step 4 — The success path logs `FinTransferEvent` without tokens being delivered:**

When `is_refund_required` returns `false`, the callback goes to the `else` branch, sends fees, and logs `FinTransferEvent` — treating the transfer as complete even though the token delivery failed. [6](#0-5) 

**Step 5 — `remove_fin_transfer` is never called; retry is impossible:**

`remove_fin_transfer` is only called inside the `if Self::is_refund_required(...)` branch. Since that branch is never entered on a panicking delivery, the transfer ID stays in `finalised_transfers` forever. Any retry attempt hits the `require!` in `add_fin_transfer` and panics with `TransferAlreadyFinalised`. [7](#0-6) [8](#0-7) 

There is no DAO/admin function that calls `remove_fin_transfer` directly; it is a private helper only reachable through the refund branch.

---

### Impact Explanation

**Permanent freezing of user or protocol funds** in the bridge vault. For non-deployed (locked) tokens, the assets remain locked in the bridge contract with no mechanism to release them. For deployed (bridge-minted) tokens, the mint never occurs and the user receives nothing. Because the transfer ID is permanently in `finalised_transfers`, the proof cannot be re-submitted. Funds are irrecoverable without a contract upgrade.

---

### Likelihood Explanation

Any token contract with a pause mechanism (USDC, USDT, and many others) can trigger this path if the token is paused between the time the proof is submitted and the time `ft_transfer` executes. This is a realistic operational scenario: token issuers routinely pause contracts during security incidents. A relayer submitting `fin_transfer` during such a window would permanently freeze the user's funds. The window is a single NEAR block (~1 second), but given the volume of bridge activity, the probability over time is non-negligible.

---

### Recommendation

In `fin_transfer_send_tokens_callback`, check the promise result of `send_tokens` unconditionally, regardless of `is_ft_transfer_call`. If the promise failed (panicked), call `remove_fin_transfer` to undo the finalization and allow the transfer to be retried. Concretely:

```rust
// Check promise result for ALL cases, not just ft_transfer_call
let transfer_failed = match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
    Err(_) => true,  // promise panicked — delivery did not happen
    Ok(value) if is_ft_transfer_call => {
        serde_json::from_slice::<U128>(&value).map_or(false, |a| a.0 == 0)
    }
    Ok(_) => false,
};
if transfer_failed {
    self.remove_fin_transfer(...);
    ...
}
```

Additionally, consider adding a DAO-gated `remove_finalised_transfer` function as a last-resort recovery path for cases where the token contract issue cannot be resolved.

---

### Proof of Concept

1. User initiates a transfer of 10,000 USDC from Ethereum to NEAR.
2. USDC token contract on NEAR is paused (e.g., Circle responds to a security incident).
3. Relayer calls `fin_transfer` with a valid EVM proof.
4. `fin_transfer_callback` decodes the proof, calls `process_fin_transfer_to_near`.
5. `process_fin_transfer_to_near` calls `add_fin_transfer` — transfer ID is inserted into `finalised_transfers`. [9](#0-8) 
6. `send_tokens` dispatches `ft_transfer(recipient, 10000, None)` to the paused USDC contract. [10](#0-9) 
7. USDC contract panics — promise result is `Failed`.
8. NEAR runtime calls `fin_transfer_send_tokens_callback` with `is_ft_transfer_call = false`.
9. `is_refund_required(false)` returns `false` without reading the promise result. [11](#0-10) 
10. Callback logs `FinTransferEvent` — bridge considers the transfer complete.
11. 10,000 USDC remain locked in the bridge contract; user receives nothing.
12. USDC is later unpaused; relayer retries `fin_transfer` with the same proof.
13. `add_fin_transfer` panics: `TransferAlreadyFinalised`. [8](#0-7) 
14. User's 10,000 USDC are permanently frozen with no recovery path.

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L1875-1877)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
```

**File:** near/omni-bridge/src/lib.rs (L2069-2117)
```rust
        let is_deployed_token = self.is_deployed_token(&token);

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

**File:** near/omni-bridge/src/lib.rs (L2322-2333)
```rust
    fn remove_fin_transfer(&mut self, transfer_id: &TransferId, storage_owner: &AccountId) {
        let storage_usage = env::storage_usage();
        self.finalised_transfers.remove(transfer_id);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(storage_owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(storage_owner, &storage);
        }
    }
```
