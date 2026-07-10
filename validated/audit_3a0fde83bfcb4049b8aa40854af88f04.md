### Title
Locked-Token Accounting Updated Before Detached Token Transfer With No Revert Path in Fast-Transfer Finalization — (File: `near/omni-bridge/src/lib.rs`)

### Summary
In `process_fin_transfer_to_other_chain`, the `locked_tokens` accounting is permanently mutated before the actual token transfer to the relayer is dispatched as a detached (fire-and-forget) promise. Unlike the analogous `process_fin_transfer_to_near` path, there is no callback that can revert the lock actions if the token transfer fails. If the detached `send_tokens` promise fails, the bridge's collateralization accounting is permanently corrupted and the relayer's funds are irrecoverably lost.

### Finding Description

`process_fin_transfer_to_other_chain` executes the following sequence when a fast transfer is being finalized:

**Step 1 — Accounting updated (permanent, committed in current tx):**
```rust
self.unlock_tokens_if_needed(
    transfer_message.get_origin_chain(),
    &token,
    transfer_message.amount.0,   // full amount removed from origin-chain lock
);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token,
    transfer_message.fee.fee.into(),  // fee added to destination-chain lock
);
```

**Step 2 — Token transfer dispatched with `.detach()` (no callback, no revert path):**
```rust
self.send_tokens(token, relayer, U128(amount_without_fee), "").detach();
self.mark_fast_transfer_as_finalised(&fast_transfer.id());
``` [1](#0-0) 

In NEAR, `.detach()` schedules the cross-contract call as a separate receipt with no attached callback. If the inner `ft_transfer` or `mint` call fails, the failure is silently dropped. The state mutations from Step 1 — `unlock_tokens_if_needed` and `lock_tokens_if_needed` — are already committed in the current transaction and are **not reverted**.

Contrast this with `process_fin_transfer_to_near`, which correctly records the lock action and passes it to a callback that calls `revert_lock_actions` on failure:

```rust
let lock_actions = vec![self.unlock_tokens_if_needed(...)];
// ...
self.send_tokens(...).then(
    Self::ext(...).fin_transfer_send_tokens_callback(
        transfer_message, &fee_recipient, ..., lock_actions,
    ),
)
``` [2](#0-1) 

The `fin_transfer_send_tokens_callback` explicitly calls `self.revert_lock_actions(&lock_actions)` on failure: [3](#0-2) 

The `revert_lock_actions` mechanism exists precisely to handle this case: [4](#0-3) 

The fast-transfer path in `process_fin_transfer_to_other_chain` omits this protection entirely.

### Impact Explanation

If `send_tokens` fails in the fast-transfer finalization path:

1. **Permanent accounting corruption**: `locked_tokens[origin_chain][token]` is permanently decremented by `amount` even though the tokens were never released. The bridge's collateralization invariant — that `locked_tokens` reflects actual cross-chain exposure — is broken. This can allow subsequent `unlock_tokens` calls to succeed when they should fail, enabling over-release of funds from the origin chain.

2. **Irrecoverable relayer loss**: `mark_fast_transfer_as_finalised` is called in the same transaction as the detached send. The fast transfer is permanently marked finalized, so the relayer cannot retry or recover their pre-deposited tokens.

This matches the allowed impact: *"Balance, decimal, fee, token-mapping, or accounting corruption that breaks bridge collateralization or misdirects value"* and *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds."*

### Likelihood Explanation

The `send_tokens` call in this path uses `ft_transfer` for non-deployed (native NEAR) tokens. This can fail if:
- The bridge's token balance is insufficient at the moment of execution (e.e., due to concurrent transfers draining the balance between the current tx and the detached receipt execution).
- The relayer's token account has been closed or lacks storage deposit at execution time.

For deployed (bridge-minted) tokens, `mint` is used and is less likely to fail. Likelihood is **low-to-medium** for native tokens, **low** for deployed tokens. However, the consequence when it does occur is permanent and unrecoverable, with no admin recovery path since the fast transfer is already marked finalized.

### Recommendation

Apply the same callback pattern used in `process_fin_transfer_to_near`. Record the lock actions before dispatching `send_tokens`, attach a callback, and

### Citations

**File:** near/omni-bridge/src/lib.rs (L1702-1714)
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
```

**File:** near/omni-bridge/src/lib.rs (L1881-1977)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L1997-2040)
```rust
        self.unlock_tokens_if_needed(
            transfer_message.get_origin_chain(),
            &token,
            transfer_message.amount.0,
        );
        self.lock_tokens_if_needed(
            transfer_message.get_destination_chain(),
            &token,
            transfer_message.fee.fee.into(),
        );

        let fast_transfer = FastTransfer::from_transfer(transfer_message.clone(), token.clone());
        let recipient = if let Some(status) = self.get_fast_transfer_status(&fast_transfer.id()) {
            require!(
                !status.finalised,
                BridgeError::FastTransferAlreadyFinalised.as_ref()
            );
            Some(status.relayer)
        } else {
            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token,
                transfer_message
                    .amount_without_fee()
                    .near_expect(BridgeError::InvalidFee),
            );

            None
        };

        // If fast transfer happened, send tokens to the relayer that executed fast transfer
        if let Some(relayer) = recipient {
            self.send_tokens(
                token,
                relayer,
                U128(
                    transfer_message
                        .amount_without_fee()
                        .near_expect(BridgeError::InvalidFee),
                ),
                "",
            )
            .detach();
            self.mark_fast_transfer_as_finalised(&fast_transfer.id());
```

**File:** near/omni-bridge/src/token_lock.rs (L122-142)
```rust
    pub fn revert_lock_actions(&mut self, lock_actions: &[LockAction]) {
        for lock_action in lock_actions {
            match lock_action {
                LockAction::Locked {
                    chain_kind,
                    token_id,
                    amount,
                } => {
                    self.unlock_tokens(*chain_kind, token_id, *amount);
                }
                LockAction::Unlocked {
                    chain_kind,
                    token_id,
                    amount,
                } => {
                    self.lock_tokens(*chain_kind, token_id, *amount);
                }
                LockAction::Unchanged => {}
            }
        }
    }
```
