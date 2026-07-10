### Title
Finalization State Removed on `ft_transfer_call` Refund Enables Transfer Replay — (`near/omni-bridge/src/lib.rs`)

### Summary

When a NEAR-destined `fin_transfer` uses a non-empty `msg` (triggering `ft_transfer_call`), the bridge first marks the transfer as finalized in `finalised_transfers`, then sends tokens. If the recipient's `ft_on_transfer` returns the full amount (a refund), the callback `fin_transfer_send_tokens_callback` **removes** the transfer ID from `finalised_transfers`. This clears the replay guard, allowing the same proof to be submitted again by a trusted relayer — who is expected to retry after seeing the emitted `FailedFinTransferEvent`.

### Finding Description

The flow for a NEAR-recipient transfer with a non-empty `msg` is:

**Step 1 — `process_fin_transfer_to_near` marks the transfer finalized and sends tokens:**

```
add_fin_transfer(&transfer_message.get_transfer_id())  // inserts into finalised_transfers
...
send_tokens(token, recipient, amount, &msg)            // ft_transfer_call because msg != ""
.then(fin_transfer_send_tokens_callback(...))
``` [1](#0-0) [2](#0-1) 

**Step 2 — `fin_transfer_send_tokens_callback` removes the finalization record on refund:**

```rust
if Self::is_refund_required(is_ft_transfer_call) {
    self.burn_tokens_if_needed(...);
    self.revert_lock_actions(&lock_actions);
    self.remove_fin_transfer(&transfer_message.get_transfer_id(), storage_owner);
    // emits FailedFinTransferEvent
}
``` [3](#0-2) 

**Step 3 — `remove_fin_transfer` deletes the entry from `finalised_transfers`:**

```rust
fn remove_fin_transfer(&mut self, transfer_id: &TransferId, storage_owner: &AccountId) {
    self.finalised_transfers.remove(transfer_id);
    ...
}
``` [4](#0-3) 

`is_refund_required` returns `true` when `amount.0 == 0`, meaning the `ft_on_transfer` receiver returned the full amount (used zero tokens), causing the NEP-141 token contract to refund all tokens back to the bridge: [5](#0-4) 

After `remove_fin_transfer`, `add_fin_transfer` will succeed again for the same `TransferId`: [6](#0-5) 

**Attacker-controlled entry path:**

1. Attacker initiates a transfer on the origin chain (e.g., EVM) with their own NEAR contract as recipient and a non-empty `msg`.
2. A trusted relayer calls `fin_transfer` with the valid proof.
3. Bridge marks transfer finalized, sends tokens via `ft_transfer_call`.
4. Attacker's `ft_on_transfer` returns the full amount → tokens refunded to bridge, `FailedFinTransferEvent` emitted, transfer ID removed from `finalised_transfers`.
5. A trusted relayer, observing `FailedFinTransferEvent`, re-submits the same proof (normal retry behavior).
6. Bridge marks transfer finalized again, sends tokens to attacker's contract.
7. Attacker's `ft_on_transfer` returns `0` (keeps tokens).
8. Attacker has received tokens from a single origin-chain lock.

The `fin_transfer` entry point requires `#[trusted_relayer]`: [7](#0-6) 

The relayer retry is the expected operational response to `FailedFinTransferEvent` — the relayer is not acting maliciously.

### Impact Explanation

For **non-deployed (native) tokens**: tokens were refunded back to the bridge in step 4, `burn_tokens_if_needed` is a no-op, so the bridge retains the full balance. On replay, the bridge transfers the same tokens again — direct double-spend from bridge reserves.

For **deployed (bridged) tokens**: tokens were minted, refunded, then burned. On replay, new tokens are minted from the same proof — unbacked supply inflation.

Both cases result in the attacker extracting assets backed by a single origin-chain lock, breaking bridge collateralization.

### Likelihood Explanation

The `FailedFinTransferEvent` is the protocol's standard signal for relayers to retry failed transfers. Any relayer following normal operational behavior will re-submit the proof. The attacker only needs to be the recipient of a transfer (a public protocol role) and deploy a contract that returns a refund from `ft_on_transfer`. The `msg` field is user-controlled at transfer initiation time on the origin chain.

### Recommendation

Do not remove the transfer from `finalised_transfers` on refund. Instead, keep the finalization record permanently. If a retry is needed for legitimate failures (e.g., storage not registered), use a separate `pending_retry` state that does not clear the replay guard. The `finalised_transfers` set must be append-only.

### Proof of Concept

1. Attacker deploys a NEAR contract `attacker.near` implementing `ft_on_transfer` that returns the full `amount` on first call and `0` on second call.
2. Attacker locks 1000 USDC on EVM with `recipient = attacker.near`, `msg = "trigger"`.
3. Relayer calls `fin_transfer` with the EVM proof → `process_fin_transfer_to_near` runs:
   - `add_fin_transfer` inserts `{Eth, nonce=N}` into `finalised_transfers`
   - `ft_transfer_call(attacker.near, 1000, "trigger")` is dispatched
4. `attacker.near::ft_on_transfer` returns `1000` → tokens refunded to bridge.
5. `fin_transfer_send_tokens_callback` sees `amount == 0`, calls `remove_fin_transfer({Eth, nonce=N})` → `finalised_transfers` no longer contains `{Eth, nonce=N}`. Emits `FailedFinTransferEvent`.
6. Relayer observes `FailedFinTransferEvent`, re-submits the same proof.
7. `add_fin_transfer({Eth, nonce=N})` succeeds (entry was removed).
8. `ft_transfer_call(attacker.near, 1000, "trigger")` dispatched again.
9. `attacker.near::ft_on_transfer` returns `0` → attacker keeps 1000 USDC.
10. Attacker has received 1000 USDC from a single 1000 USDC lock on EVM.

### Citations

**File:** near/omni-bridge/src/lib.rs (L670-696)
```rust
    #[payable]
    #[trusted_relayer]
    #[pause(except(roles(Role::DAO)))]
    pub fn fin_transfer(&mut self, #[serializer(borsh)] args: FinTransferArgs) -> Promise {
        require!(
            args.storage_deposit_actions.len() <= 3,
            BridgeError::InvalidStorageAccountsLen.as_ref()
        );
        let mut main_promise = self.verify_proof(args.chain_kind, args.prover_args);

        let mut attached_deposit = env::attached_deposit();

        for action in &args.storage_deposit_actions {
            main_promise =
                main_promise.and(Self::check_or_pay_ft_storage(action, &mut attached_deposit));
        }

        main_promise.then(
            Self::ext(env::current_account_id())
                .with_attached_deposit(attached_deposit)
                .with_static_gas(FIN_TRANSFER_CALLBACK_GAS)
                .fin_transfer_callback(
                    &args.storage_deposit_actions,
                    env::predecessor_account_id(),
                ),
        )
    }
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
