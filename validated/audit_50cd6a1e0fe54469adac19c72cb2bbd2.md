### Title
Silent `ft_transfer` Failure in `fin_transfer_send_tokens_callback` Causes Permanent Irrecoverable Lock of Bridged Funds — (`near/omni-bridge/src/lib.rs`)

---

### Summary

The NEAR bridge's `fin_transfer_send_tokens_callback` function does not check whether the underlying `ft_transfer` (or `mint`) call succeeded when `msg` is empty (`is_ft_transfer_call = false`). If a registered NEP-141 token contract reverts `ft_transfer` during finalization, the bridge silently treats the transfer as successful: it emits `FinTransferEvent`, permanently marks the transfer ID as finalised in `finalised_transfers`, and decrements `locked_tokens` — all without the recipient ever receiving their tokens. Because the transfer ID is consumed, no retry is possible. The funds are permanently locked in the bridge contract with no recovery path.

---

### Finding Description

The vulnerability class from the external report is: **a malicious/compromised token contract reverting a critical external call during a protocol operation, permanently blocking that operation with no recovery**.

The analog in Omni Bridge is in `fin_transfer_send_tokens_callback` and `is_refund_required`.

**`is_refund_required` logic:**

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => { ... amount.0 == 0 }
            Err(_) => false,   // ← ft_transfer_call panic treated as success
        }
    } else {
        // Not ft_transfer_call: don't refund
        false   // ← ft_transfer failure NEVER checked
    }
}
``` [1](#0-0) 

**`send_tokens` dispatches based on token type and `msg`:**

- Non-deployed token, `msg` empty → `ft_transfer` → `is_ft_transfer_call = false`
- Non-deployed token, `msg` non-empty → `ft_transfer_call` → `is_ft_transfer_call = true`
- Deployed token (any `msg`) → `mint` → `is_ft_transfer_call = false` [2](#0-1) 

**`process_fin_transfer_to_near` pre-commits irreversible state before the external call:**

1. `add_fin_transfer` inserts the transfer ID into `finalised_transfers` (a `LookupSet`) — any future `fin_transfer` with the same proof panics with `TransferAlreadyFinalised`.
2. `unlock_tokens_if_needed` decrements `locked_tokens`.
3. Only then is `send_tokens` called, followed by `fin_transfer_send_tokens_callback`. [3](#0-2) 

**`fin_transfer_send_tokens_callback` success path (taken when `is_refund_required` returns `false`):**

```rust
} else {
    // Send fee to the fee recipient (detached, no error check)
    ...
    env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
}
``` [4](#0-3) 

The refund/revert path — which calls `revert_lock_actions` and `remove_fin_transfer` — is only taken when `is_refund_required` returns `true`. [5](#0-4) 

`remove_fin_transfer` removes the ID from `finalised_transfers` and refunds storage. It is never called in the success path. [6](#0-5) 

`add_fin_transfer` permanently inserts the transfer ID, making any retry impossible. [7](#0-6) 

**Two concrete failure modes:**

**Mode A — `ft_transfer` panics (non-deployed token, no message):**
`is_ft_transfer_call = false` → `is_refund_required` returns `false` unconditionally. The promise result is never read. The bridge emits `FinTransferEvent` and the tokens remain in the bridge contract.

**Mode B — `ft_transfer_call` panics entirely (non-deployed token, with message):**
`is_ft_transfer_call = true`, but `env::promise_result_checked` returns `Err(_)` (the entire call panicked, not just `ft_on_transfer`). The `Err(_) => false` branch treats this as success. Same outcome. [8](#0-7) 

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows.**

For non-deployed tokens (native NEAR tokens bridged to EVM and back): the tokens are physically held in the bridge contract. If `ft_transfer` fails, they remain there forever. The transfer ID is consumed; `locked_tokens` is decremented (accounting desync); no admin function exists to recover the specific transfer.

For deployed tokens: `mint` failing means the recipient never receives tokens, while the origin-chain tokens were already burned/locked. The bridge emits `FinTransferEvent` as if delivery succeeded.

In both cases the transfer is permanently finalised with no retry or rescue path available to any party (user, relayer, or DAO).

---

### Likelihood Explanation

Any NEP-141 token registered on the bridge (via `logMetadata` on EVM) is eligible. Realistic trigger conditions:

- A token contract that is **paused** (many NEP-141 tokens implement a pause mechanism that reverts `ft_transfer`).
- A token contract that is **upgraded** to be malicious after registration.
- A token contract with a **bug** that causes `ft_transfer` to panic under certain conditions (e.g., insufficient gas forwarded — `FT_TRANSFER_GAS` is only 5 TGas).
- A **deliberately malicious token** deployed by an attacker who registers it on the bridge.

The entry point is fully unprivileged: any user can initiate a cross-chain transfer of any registered token. The relayer calling `fin_transfer` triggers the vulnerable path.

---

### Recommendation

In `fin_transfer_send_tokens_callback`, check the promise result for **all** token dispatch paths, not only `ft_transfer_call`. Specifically:

1. For `is_ft_transfer_call = false` (i.e., `ft_transfer` or `mint`), read `env::promise_result_checked(0, ...)` and treat a failed promise as a refund condition — call `revert_lock_actions` and `remove_fin_transfer`.
2. For `is_ft_transfer_call = true`, treat `Err(_)` (entire `ft_transfer_call` panicked) as a refund condition rather than silently succeeding.

The refund path already exists and is correct; it just needs to be reachable for all failure modes:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
        Err(_) => true,  // any panic → refund
        Ok(value) if is_ft_transfer_call => {
            near_sdk::serde_json::from_slice::<U128>(&value)
                .map_or(false, |amount| amount.0 == 0)
        }
        Ok(_) => false,  // ft_transfer / mint succeeded
    }
}
```

---

### Proof of Concept

1. Deploy a malicious NEP-141 token on NEAR whose `ft_transfer` always panics.
2. Register it on the EVM bridge via `logMetadata`.
3. Lock some amount of the token on EVM via `initTransfer` targeting a NEAR recipient with no `message` field.
4. A relayer calls `fin_transfer` on the NEAR bridge with the proof.
5. `fin_transfer_callback` → `process_fin_transfer_to_near`:
   - `add_fin_transfer` inserts the transfer ID into `finalised_transfers`. [9](#0-8) 
   - `unlock_tokens_if_needed` decrements `locked_tokens`. [10](#0-9) 
   - `send_tokens` dispatches `ft_transfer` (no message → `is_ft_transfer_call = false`). [11](#0-10) 
6. The malicious token panics in `ft_transfer`. The NEAR runtime marks the promise as failed but still executes the callback.
7. `fin_transfer_send_tokens_callback` runs with `is_ft_transfer_call = false`:
   - `is_refund_required(false)` returns `false` unconditionally. [12](#0-11) 
   - Success path taken: `FinTransferEvent` emitted.
8. The transfer ID is permanently in `finalised_transfers`. Any retry of `fin_transfer` with the same proof panics with `ERR_TRANSFER_ALREADY_FINALISED`. The tokens are permanently locked in the bridge contract.

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

**File:** near/omni-bridge/src/lib.rs (L1719-1745)
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
