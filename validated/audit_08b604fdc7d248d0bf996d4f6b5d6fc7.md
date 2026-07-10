### Title
`fin_transfer_send_tokens_callback` Does Not Handle `send_tokens` Promise Failure, Permanently Freezing Recipient Funds - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

In the NEAR omni-bridge contract, when finalizing an inbound transfer to a NEAR recipient, the transfer is permanently recorded as finalized in `finalised_transfers` **before** tokens are sent. If the `send_tokens` promise subsequently fails, the callback `fin_transfer_send_tokens_callback` does not detect the failure for non-`ft_transfer_call` paths and proceeds as if the transfer succeeded. The transfer ID is irrevocably consumed, the source-chain locked-token accounting is permanently decremented, and the recipient never receives their tokens — a permanent fund freeze with no recovery path.

---

### Finding Description

The `fin_transfer` flow for NEAR-destined transfers proceeds as follows:

**Step 1 — `process_fin_transfer_to_near` (synchronous, before any promise)**

`add_fin_transfer` inserts the `TransferId` into `finalised_transfers` and `unlock_tokens_if_needed` decrements `locked_tokens`. Both mutations are committed to state immediately. [1](#0-0) 

**Step 2 — `send_tokens` (async promise)**

Depending on the token type and `msg`, `send_tokens` dispatches one of:
- `ft_transfer` (native token, empty msg) — can fail if bridge balance is insufficient
- `mint` (deployed token, empty msg) — can fail if token contract is paused
- `ft_transfer_call` (any token, non-empty msg) — can fail at the promise level [2](#0-1) 

**Step 3 — `fin_transfer_send_tokens_callback` (callback)**

The callback calls `is_refund_required` to decide whether to revert state: [3](#0-2) 

`is_refund_required` returns `true` **only** when `is_ft_transfer_call = true` AND the promise result deserializes to `U128(0)`. For every other outcome — including:
- `is_ft_transfer_call = false` (ft_transfer or mint path, empty msg) regardless of promise success/failure
- `is_ft_transfer_call = true` but the promise itself failed (`Err(_)` branch, line 1798)

…the function returns `false`, and the callback falls through to the `else` branch that emits `FinTransferEvent` as if the transfer succeeded: [4](#0-3) 

The revert path (`remove_fin_transfer` + `revert_lock_actions`) is never reached: [5](#0-4) 

`remove_fin_transfer` is the only function that removes an entry from `finalised_transfers`. It is private and only called from this revert path. There is no admin escape hatch. [6](#0-5) 

---

### Impact Explanation

When `send_tokens` fails on the non-`ft_transfer_call` path:

1. The `TransferId` is permanently in `finalised_transfers` — any attempt to replay `fin_transfer` with the same proof panics with `TransferAlreadyFinalised`.
2. `locked_tokens` for the origin chain has already been decremented — the bridge's collateral accounting is permanently corrupted.
3. The recipient receives nothing.
4. The source-chain user's tokens were already burned or locked when `init_transfer` was called on the origin chain.

This is **permanent, irrecoverable freezing of user funds** — matching the Critical impact class.

---

### Likelihood Explanation

The `ft_transfer` path (native token, empty msg) fails whenever the bridge contract's token balance is less than the transfer amount. This can occur due to:

- A prior accounting bug that left `locked_tokens` overstated relative to actual holdings.
- A token contract that temporarily pauses transfers (e.g., during an upgrade).
- A `mint` call on a deployed token whose contract is paused.

The `ft_transfer_call` promise-failure path (`Err(_) => false`) is also reachable if the recipient contract panics during `ft_on_transfer` in a way that causes the entire promise to fail rather than returning a value.

Likelihood: **Medium** — the bridge is a live production system; token balance mismatches or temporary token pauses are realistic operational conditions.

---

### Recommendation

`fin_transfer_send_tokens_callback` must check whether the `send_tokens` promise succeeded before deciding to revert. Replace the `is_refund_required` logic with a check that covers all promise outcomes:

```rust
fn fin_transfer_send_tokens_callback(...) {
    let promise_failed = env::promise_results_count() > 0
        && matches!(env::promise_result(0), PromiseResult::Failed);

    let refund = promise_failed || Self::is_refund_required(is_ft_transfer_call);

    if refund {
        // existing revert path
        self.revert_lock_actions(&lock_actions);
        self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);
        ...
    } else {
        // existing success path
    }
}
```

Additionally, the `Err(_) => false` branch in `is_refund_required` should be changed to `Err(_) => true` so that a failed `ft_transfer_call` promise also triggers a revert.

---

### Proof of Concept

1. User bridges 1000 USDC from Ethereum to NEAR. On Ethereum, 1000 USDC is locked in the EVM bridge contract. An `InitTransfer` event is emitted.
2. A relayer calls `fin_transfer` on the NEAR bridge with a valid proof. `fin_transfer_callback` → `process_fin_transfer_to_near` runs:
   - `add_fin_transfer` inserts the `TransferId` into `finalised_transfers`. [7](#0-6) 
   - `unlock_tokens_if_needed` decrements `locked_tokens[Eth][usdc]` by 1000. [8](#0-7) 
   - `send_tokens` dispatches `ft_transfer(recipient, 1000)` to the USDC token contract. [9](#0-8) 
3. The USDC token contract is paused (or the bridge holds insufficient balance). The `ft_transfer` promise **fails**.
4. `fin_transfer_send_tokens_callback` runs with `is_ft_transfer_call = false` (empty msg). `is_refund_required` returns `false`. The callback emits `FinTransferEvent` and exits. [10](#0-9) 
5. The `TransferId` is permanently in `finalised_transfers`. A second `fin_transfer` call with the same proof panics. The recipient has received nothing. The Ethereum-side tokens remain locked. Funds are permanently frozen.

### Citations

**File:** near/omni-bridge/src/lib.rs (L1712-1718)
```rust
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
