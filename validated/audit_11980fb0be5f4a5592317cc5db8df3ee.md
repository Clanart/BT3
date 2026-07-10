### Title
Silent `ft_transfer` / `mint` Failure in `fin_transfer_send_tokens_callback` Permanently Freezes User Funds - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`fin_transfer_send_tokens_callback` never checks whether the preceding `send_tokens` promise actually succeeded when `msg` is empty (`is_ft_transfer_call = false`). Because the transfer is already inserted into `finalised_transfers` before `send_tokens` is dispatched, a failed `ft_transfer` or `mint` call silently marks the transfer as permanently finalized while the user receives nothing. There is no recovery path.

---

### Finding Description

The inbound finalization flow for NEAR-destined transfers is:

```
fin_transfer()
  → fin_transfer_callback()
    → process_fin_transfer_to_near()
        1. add_fin_transfer()          ← inserts into finalised_transfers (committed)
        2. unlock_tokens_if_needed()   ← decrements locked_tokens (committed)
        3. send_tokens()               ← cross-contract call (ft_transfer or mint)
        4. .then(fin_transfer_send_tokens_callback())
```

`add_fin_transfer` inserts the `TransferId` into the `finalised_transfers` `LookupSet` synchronously, before the cross-contract call returns. [1](#0-0) 

`send_tokens` dispatches either `ft_transfer` (for native tokens, `msg` empty) or `mint` (for deployed tokens, `msg` empty). [2](#0-1) 

The callback `fin_transfer_send_tokens_callback` decides whether to revert via `is_refund_required`: [3](#0-2) 

When `is_ft_transfer_call = false` (which is always the case when `msg` is empty, i.e., for plain `ft_transfer` or `mint`), `is_refund_required` **unconditionally returns `false`** without inspecting the promise result at all. The callback therefore always takes the "success" branch: [4](#0-3) 

In NEAR's async model, a callback is always invoked regardless of whether the preceding promise panicked. If `ft_transfer` panics (e.g., the token contract is paused, blacklisted, or otherwise rejects the transfer), the callback is still called, `is_refund_required(false)` returns `false`, and the "success" branch logs `FinTransferEvent` and sends the fee — while the user's tokens were never delivered.

The same flaw applies to the wNEAR unwrap path: if `near_withdraw` fails, `near_withdraw_callback` panics, and `fin_transfer_send_tokens_callback` is still invoked with `is_ft_transfer_call = false`, again silently treating the failure as success. [5](#0-4) 

The `remove_fin_transfer` recovery path (which would allow a retry) is only reachable when `is_refund_required` returns `true`, which requires `is_ft_transfer_call = true` **and** the `ft_transfer_call` returning a non-zero refund amount. It is structurally unreachable for plain `ft_transfer` or `mint`. [6](#0-5) 

A developer TODO comment in the UTXO fast-transfer path explicitly acknowledges the unresolved problem of failed `send_tokens` calls: [7](#0-6) 

---

### Impact Explanation

**Critical — Permanent, irrecoverable lock of user funds.**

- For **native (non-deployed) tokens**: the tokens remain held by the bridge contract (the `ft_transfer` never moved them), but `finalised_transfers` permanently records the nonce as used. Any retry of `fin_transfer` with the same proof reverts with `ERR_TRANSFER_ALREADY_FINALISED`. The tokens are locked in the bridge forever with no admin escape hatch.
- For **deployed bridge tokens**: the tokens were burned on the origin chain (EVM/Solana/StarkNet) before the bridge proof was submitted. If `mint` fails on NEAR, the tokens are destroyed on the source chain and never created on NEAR — a total loss.

There is no `emergencyWithdraw`, no admin function to remove a finalized transfer, and no way for the user to reclaim their funds.

---

### Likelihood Explanation

**Moderate.** The bridge supports arbitrary NEAR-native tokens (non-deployed). Many production NEP-141 tokens implement pause functionality (e.g., stablecoins, wrapped assets). If any such token is paused — even temporarily for a security incident — every in-flight `fin_transfer` for that token will silently finalize with no delivery. The user cannot retry. The window of exposure is any period during which the token contract rejects transfers.

The wNEAR path is also affected: if the wNEAR contract rejects `near_withdraw` for any reason, the same permanent lock occurs for all NEAR-denominated bridge transfers in flight.

---

### Recommendation

`fin_transfer_send_tokens_callback` must inspect the promise result regardless of `is_ft_transfer_call`. When the preceding `send_tokens` promise failed (i.e., `env::promise_result_checked(0, ...)` returns `Err`), the callback should execute the same revert logic as the existing refund path: call `revert_lock_actions`, call `remove_fin_transfer`, and emit `FailedFinTransferEvent`. This allows the relayer to retry `fin_transfer` once the token contract is operational again.

```rust
fn fin_transfer_send_tokens_callback(...) {
    let send_failed = env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT).is_err();
    if send_failed || Self::is_refund_required(is_ft_transfer_call) {
        // revert path: burn, revert locks, remove finalization record
        ...
        self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);
        ...
    } else {
        // success path
    }
}
```

---

### Proof of Concept

1. A NEAR-native token `token.near` (non-deployed, e.g., a pausable stablecoin) is registered in the bridge.
2. A user initiates a transfer of 1000 `token.near` from EVM → NEAR. The tokens are locked in the EVM bridge contract.
3. The token contract owner pauses `token.near`.
4. A relayer calls `fin_transfer` with a valid proof. Inside `process_fin_transfer_to_near`:
   - `add_fin_transfer` inserts the transfer ID into `finalised_transfers`. [8](#0-7) 
   - `send_tokens` dispatches `ft_transfer(recipient, 1000, None)` to `token.near`. [9](#0-8) 
5. `token.near` is paused; `ft_transfer` panics. The promise result is `Failed`.
6. NEAR runtime invokes `fin_transfer_send_tokens_callback` with `is_ft_transfer_call = false`.
7. `is_refund_required(false)` returns `false` unconditionally. [10](#0-9) 
8. The callback logs `FinTransferEvent` and exits — no revert, no `remove_fin_transfer`.
9. The user's 1000 tokens remain locked in the EVM bridge. The NEAR transfer ID is permanently finalized. Any retry of `fin_transfer` reverts with `ERR_TRANSFER_ALREADY_FINALISED`. Funds are permanently frozen.

### Citations

**File:** near/omni-bridge/src/lib.rs (L1047-1051)
```rust
    pub fn near_withdraw_callback(&self, recipient: AccountId, amount: NearToken) -> Promise {
        match env::promise_result_checked(0, usize::MAX) {
            Ok(_) => Promise::new(recipient).transfer(amount),
            Err(_) => env::panic_str(BridgeError::NearWithdrawFailed.to_string().as_str()),
        }
```

**File:** near/omni-bridge/src/lib.rs (L1702-1745)
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

**File:** near/omni-bridge/src/lib.rs (L2102-2107)
```rust
        } else if msg.is_empty() {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        } else {
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

**File:** near/omni-bridge/src/lib.rs (L2484-2485)
```rust
            // TODO: check how to deal with failed send_tokens
            return self.utxo_fin_transfer_fast(fast_transfer, status, utxo_fin_transfer_msg);
```
