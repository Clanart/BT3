### Title
`fin_transfer_send_tokens_callback` Ignores `ft_transfer` / `mint` Failures, Permanently Freezing Bridged Funds — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`fin_transfer_send_tokens_callback` only detects a failed token delivery when `is_ft_transfer_call` is `true`. For every other delivery path (`ft_transfer`, `mint` with empty `msg`, `near_withdraw`), the callback unconditionally takes the "success" branch regardless of the underlying promise result. Because `add_fin_transfer` was already committed to state in the parent callback, the transfer is permanently recorded as finalised while the recipient never receives their tokens.

---

### Finding Description

The NEAR-side finalization flow is:

```
fin_transfer()
  └─ fin_transfer_callback()          ← commits add_fin_transfer() to state
       └─ process_fin_transfer_to_near()
            └─ send_tokens()          ← cross-contract call (ft_transfer / mint / near_withdraw)
                 └─ fin_transfer_send_tokens_callback()   ← checks is_refund_required()
```

`process_fin_transfer_to_near` calls `add_fin_transfer` first, then returns a promise. Because `fin_transfer_callback` returns that promise successfully, the `finalised_transfers` insertion is committed before `send_tokens` executes.

`send_tokens` selects the delivery mechanism based on the token type and `msg`:

| Condition | Call used | `is_ft_transfer_call` |
|---|---|---|
| `token == wnear && msg.is_empty()` | `near_withdraw` → `near_withdraw_callback` | `false` |
| `is_deployed_token && msg.is_empty()` | `mint(recipient, amount, None)` | `false` |
| `!is_deployed_token && msg.is_empty()` | `ft_transfer` | `false` |
| `msg` non-empty | `ft_transfer_call` / `mint(…, Some(msg))` | `true` |

`fin_transfer_send_tokens_callback` delegates the failure check entirely to `is_refund_required`:

```rust
// near/omni-bridge/src/lib.rs  line 1784-1803
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => { ... amount.0 == 0 }
            Err(_) => false,
        }
    } else {
        false   // ← always false; promise result is never inspected
    }
}
```

When `is_ft_transfer_call` is `false`, the function returns `false` unconditionally — the promise result is never read. The callback therefore always executes the "success" branch:

```rust
// near/omni-bridge/src/lib.rs  line 1719-1746
} else {
    // Send fee to the fee recipient
    ...
    env::log_str(&OmniBridgeEvent::FinTransferEvent { transfer_message }.to_log_string());
}
```

No call to `remove_fin_transfer` or `revert_lock_actions` is made. The transfer ID remains in `finalised_transfers` permanently, blocking any retry, while the recipient's tokens are never delivered.

---

### Impact Explanation

**Critical — Permanent, irrecoverable freeze of bridged user funds.**

- The transfer is recorded in `finalised_transfers`; `add_fin_transfer` enforces uniqueness with `require!(self.finalised_transfers.insert(transfer_id), ...)`, so no relayer can ever re-submit the same proof.
- For locked (non-deployed) tokens: the bridge holds the underlying ERC-20/NEP-141 balance, which is now permanently stranded.
- For deployed (bridge-minted) tokens: the mint never occurred; the user's source-chain tokens were already burned/locked, and no NEAR-side tokens are ever created.
- There is no admin escape hatch to remove a finalised transfer or force a re-delivery.

---

### Likelihood Explanation

**Medium.** The three affected delivery paths (`ft_transfer`, `mint` with empty `msg`, `near_withdraw`) can fail under realistic conditions:

1. **Token contract paused** — many NEP-141 implementations include a pause mechanism. If the token is paused between the storage-check promise and the `ft_transfer` promise, the transfer fails silently.
2. **Insufficient bridge balance** — if `locked_tokens` accounting drifts (e.g., due to a separate bug or an admin action), the bridge may not hold enough tokens to satisfy `ft_transfer`, causing it to revert.
3. **Out-of-gas on `ft_transfer`** — `FT_TRANSFER_GAS` is only 5 TGas; a token contract with non-trivial transfer logic could exhaust it.
4. **wNEAR `near_withdraw` failure** — if the wNEAR contract is paused or the bridge's wNEAR balance is insufficient, `near_withdraw` reverts and the same silent-success path is taken.

None of these require privileged access; a paused token contract is the most realistic trigger and is entirely outside the bridge's control.

---

### Recommendation

Extend `is_refund_required` (or add a separate check) to inspect the promise result for all delivery paths, not only `ft_transfer_call`. Concretely:

```rust
fn is_refund_required(is_ft_transfer_call: bool) -> bool {
    if is_ft_transfer_call {
        match env::promise_result_checked(0, MAX_FT_TRANSFER_CALL_RESULT) {
            Ok(value) => serde_json::from_slice::<U128>(&value)
                .map(|a| a.0 == 0)
                .unwrap_or(false),
            Err(_) => false,
        }
    } else {
        // NEW: treat a failed promise as a refund trigger
        env::promise_result_checked(0, 0).is_err()
    }
}
```

When a refund is triggered, `remove_fin_transfer` and `revert_lock_actions` must be called so the transfer can be re-submitted once the underlying issue is resolved.

---

### Proof of Concept

1. Alice initiates a transfer of a locked (non-deployed) ERC-20 token from Ethereum to NEAR with an empty `msg`. The EVM bridge locks her tokens.
2. A relayer calls `fin_transfer` on NEAR with a valid proof. `fin_transfer_callback` runs, calls `process_fin_transfer_to_near`, which calls `add_fin_transfer` — the transfer ID is inserted into `finalised_transfers` and committed.
3. `send_tokens` issues `ft_transfer` to the token contract with `FT_TRANSFER_GAS = 5 TGas`.
4. The token contract is paused (or runs out of gas); the `ft_transfer` promise fails.
5. `fin_transfer_send_tokens_callback` is invoked with `is_ft_transfer_call = false`.
6. `is_refund_required(false)` returns `false` without reading the promise result.
7. The callback logs `FinTransferEvent` and exits — no `remove_fin_transfer`, no `revert_lock_actions`.
8. Alice's transfer ID is permanently in `finalised_transfers`. No relayer can re-submit the proof (`TransferAlreadyFinalised`). Alice's tokens are permanently frozen in the bridge.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** near/omni-bridge/src/lib.rs (L1783-1803)
```rust
impl Contract {
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

**File:** near/omni-bridge/src/lib.rs (L1867-1978)
```rust
    #[allow(clippy::too_many_lines, clippy::ptr_arg)]
    fn process_fin_transfer_to_near(
        &mut self,
        recipient: AccountId,
        predecessor_account_id: &AccountId,
        transfer_message: TransferMessage,
        storage_deposit_actions: &Vec<StorageDepositAction>,
    ) -> Promise {
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

        let token = self.get_token_id(&transfer_message.token);
        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());

        let lock_actions = vec![self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        )];

        // If fast transfer happened, change recipient and fee recipient to the relayer that executed fast transfer
        let (recipient, msg, fee_recipient) = match fast_transfer_status {
            Some(status) => {
                require!(
                    !status.finalised,
                    BridgeError::FastTransferAlreadyFinalised.as_ref()
                );
                self.remove_fast_transfer(&fast_transfer.id());
                (status.relayer.clone(), String::new(), status.relayer)
            }
            None => (
                recipient,
                transfer_message.msg.clone(),
                predecessor_account_id.clone(),
            ),
        };

        let mut storage_deposit_action_index: usize = 0;
        require!(
            Self::check_storage_balance_result(
                (storage_deposit_action_index + 1)
                    .try_into()
                    .near_expect(BridgeError::Cast)
            ) && storage_deposit_actions[storage_deposit_action_index].account_id == recipient
                && storage_deposit_actions[storage_deposit_action_index].token_id == token,
            BridgeError::StorageRecipientOmitted.as_ref()
        );
        storage_deposit_action_index += 1;

        // One yoctoNear is required to send tokens to the recipient
        required_balance = required_balance.saturating_add(ONE_YOCTO);

        if transfer_message.fee.fee.0 > 0 {
            require!(
                Self::check_storage_balance_result(
                    (storage_deposit_action_index + 1)
                        .try_into()
                        .near_expect(BridgeError::Cast)
                ) && storage_deposit_actions[storage_deposit_action_index].account_id
                    == fee_recipient
                    && storage_deposit_actions[storage_deposit_action_index].token_id == token,
                BridgeError::StorageFeeRecipientOmitted.as_ref()
            );
            storage_deposit_action_index += 1;

            required_balance = required_balance.saturating_add(ONE_YOCTO);
        }

        if transfer_message.fee.native_fee.0 > 0 {
            let native_token_id = self.get_native_token_id(transfer_message.get_origin_chain());

            require!(
                Self::check_storage_balance_result(
                    (storage_deposit_action_index + 1)
                        .try_into()
                        .near_expect(BridgeError::Cast)
                ) && storage_deposit_actions[storage_deposit_action_index].account_id
                    == fee_recipient
                    && storage_deposit_actions[storage_deposit_action_index].token_id
                        == native_token_id,
                BridgeError::StorageNativeFeeRecipientOmitted.as_ref()
            );
        }

        self.update_storage_balance(
            predecessor_account_id.clone(),
            required_balance,
            env::attached_deposit(),
        );

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
    }
```

**File:** near/omni-bridge/src/lib.rs (L2056-2118)
```rust
    fn send_tokens(
        &self,
        token: AccountId,
        recipient: AccountId,
        amount: U128,
        msg: &str,
    ) -> Promise {
        let ft_transfer_call_gas = env::prepaid_gas()
            .saturating_sub(env::used_gas())
            .saturating_sub(SEND_TOKENS_CALLBACK_GAS) // TODO: not all send_tokens callbacks has the same gas.
            .saturating_sub(MINT_TOKEN_GAS)
            .min(FT_TRANSFER_CALL_GAS);

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
