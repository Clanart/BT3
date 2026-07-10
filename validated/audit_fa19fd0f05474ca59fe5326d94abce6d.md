### Title
`fin_transfer_send_tokens_callback` Ignores `ft_transfer`/`mint` Promise Failures, Permanently Locking User Funds — (`near/omni-bridge/src/lib.rs`)

---

### Summary

In the NEAR bridge contract, `fin_transfer_send_tokens_callback` never checks whether the underlying `ft_transfer` or `mint` promise succeeded for the common (no-message) transfer path. The transfer is already marked finalized in `finalised_transfers` before the token delivery attempt. If the token delivery fails, the transfer cannot be retried and the user's funds are permanently locked in the bridge with no recovery path.

---

### Finding Description

The inbound finalization flow for NEAR-recipient transfers is:

1. `fin_transfer_callback` → `process_fin_transfer_to_near`
2. `add_fin_transfer` inserts the transfer ID into `finalised_transfers` (replay guard)
3. `unlock_tokens_if_needed` decrements the locked-token accounting
4. `send_tokens` dispatches an async `ft_transfer` (native token, no message) or `mint` (deployed token, no message)
5. `fin_transfer_send_tokens_callback` is chained as the result handler [1](#0-0) 

Inside `fin_transfer_send_tokens_callback`, the only mechanism that detects a failed delivery is `is_refund_required`: [2](#0-1) 

`is_refund_required` is gated entirely on `is_ft_transfer_call`: [3](#0-2) 

`is_ft_transfer_call` is set to `!msg.is_empty()` at the call site: [4](#0-3) 

For the common case — a plain `ft_transfer` (native token, no message) or a plain `mint` (deployed token, no message) — `msg` is empty, so `is_ft_transfer_call = false`. `is_refund_required` unconditionally returns `false` without ever reading the promise result. The callback always takes the success branch, sends the fee, and emits `FinTransferEvent`, regardless of whether the token delivery actually succeeded.

The recovery path (`remove_fin_transfer` + `revert_lock_actions`) is only reachable when `is_refund_required` returns `true`: [2](#0-1) 

Because `is_refund_required` is always `false` for non-`ft_transfer_call` paths, `remove_fin_transfer` is never called on failure. The transfer ID stays in `finalised_transfers` permanently, blocking any retry.

The `send_tokens` dispatch for the two affected paths:

```rust
// native token, no message → ft_transfer, 5 TGas
ext_token::ext(token)
    .with_attached_deposit(ONE_YOCTO)
    .with_static_gas(FT_TRANSFER_GAS)          // 5 TGas
    .ft_transfer(recipient, amount, None)

// deployed token, no message → mint, 5 TGas
ext_token::ext(token)
    .with_static_gas(MINT_TOKEN_GAS.saturating_add(ft_transfer_call_gas))
    .mint(recipient, amount, None)
``` [5](#0-4) 

`FT_TRANSFER_GAS = 5 TGas` and `MINT_TOKEN_GAS = 5 TGas` are the static allocations: [6](#0-5) 

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

When `ft_transfer` or `mint` fails:
- The transfer ID is already in `finalised_transfers`; any retry of `fin_transfer` with the same proof reverts with `ERR_TRANSFER_ALREADY_FINALISED`.
- `locked_tokens` accounting has already been decremented by `unlock_tokens_if_needed`, so the bridge's internal bookkeeping is corrupted.
- For native tokens: the tokens remain in the bridge contract with no withdrawal path.
- For deployed tokens: no tokens are minted; the user receives nothing and cannot re-trigger minting.
- There is no admin recovery function visible in the contract that can un-finalize a transfer.

---

### Likelihood Explanation

Multiple realistic triggers exist, reachable without any privileged access:

1. **Token contract paused independently of the bridge.** Many NEP-141 tokens implement their own pause mechanism. If the token is paused between proof submission and callback execution, `ft_transfer` panics and the promise fails.
2. **Gas exhaustion.** `FT_TRANSFER_GAS = 5 TGas` is the minimum for a simple transfer. Tokens with fee-on-transfer hooks, blacklist checks, or other custom logic require more gas. The promise fails silently.
3. **Recipient storage not registered.** Although `process_fin_transfer_to_near` checks storage balance results, the check only verifies that a storage-deposit call was made — it does not guarantee the deposit succeeded or that the token contract will accept the transfer.
4. **Token contract upgrade or bug.** Any panic in the token contract's `ft_transfer` or `mint` during the callback window triggers the issue.

---

### Recommendation

In `fin_transfer_send_tokens_callback`, check the promise result for **all** delivery paths, not only `ft_transfer_call`. Use `env::promise_result_checked(0, ...)` unconditionally and treat a failed promise as a refund trigger:

```rust
pub fn fin_transfer_send_tokens_callback(...) {
    let delivery_failed = if is_ft_transfer_call {
        Self::is_refund_required(true)
    } else {
        // NEW: check whether ft_transfer / mint promise succeeded
        env::promise_result_checked(0, 0).is_err()
    };

    if delivery_failed {
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

**Scenario: native token with a pausable `ft_transfer`**

1. Token `T` (non-deployed, native NEP-141) is registered on the NEAR bridge.
2. User locks `T` on EVM and the EVM bridge emits `InitTransfer`.
3. Relayer calls `fin_transfer` on the NEAR bridge with a valid proof.
4. `fin_transfer_callback` executes:
   - `add_fin_transfer` → transfer ID inserted into `finalised_transfers`. [7](#0-6) 
   - `unlock_tokens_if_needed` → locked-token counter decremented.
   - `send_tokens` → `ft_transfer(recipient, amount)` dispatched with 5 TGas.
5. Token contract `T` is paused (or requires >5 TGas); `ft_transfer` promise fails.
6. `fin_transfer_send_tokens_callback` fires with `is_ft_transfer_call = false`.
7. `is_refund_required` returns `false` without reading the promise result. [8](#0-7) 
8. Callback takes the success branch, emits `FinTransferEvent`.
9. User attempts to retry `fin_transfer` with the same proof → `ERR_TRANSFER_ALREADY_FINALISED`. [9](#0-8) 
10. Tokens are permanently locked in the bridge; user has no recourse.

### Citations

**File:** near/omni-bridge/src/lib.rs (L64-73)
```rust
const FT_TRANSFER_GAS: Gas = Gas::from_tgas(5);
const UPDATE_CONTROLLER_GAS: Gas = Gas::from_tgas(250);
const WNEAR_WITHDRAW_GAS: Gas = Gas::from_tgas(5);
const NEAR_WITHDRAW_CALLBACK_GAS: Gas = Gas::from_tgas(5);
const STORAGE_BALANCE_OF_GAS: Gas = Gas::from_tgas(3);
const STORAGE_DEPOSIT_GAS: Gas = Gas::from_tgas(3);
const DEPLOY_TOKEN_CALLBACK_GAS: Gas = Gas::from_tgas(75);
const DEPLOY_TOKEN_GAS: Gas = Gas::from_tgas(50);
const BURN_TOKEN_GAS: Gas = Gas::from_tgas(3);
const MINT_TOKEN_GAS: Gas = Gas::from_tgas(5);
```

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

**File:** near/omni-bridge/src/lib.rs (L1875-1877)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
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

**File:** near/omni-bridge/src/lib.rs (L2082-2106)
```rust
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
