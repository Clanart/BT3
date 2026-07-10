### Title
`fast_fin_transfer_to_near_callback` Lacks Re-Validation of Finalization State, Enabling Double-Spend for NEAR Recipients — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`fast_fin_transfer` checks `is_unified_transfer_finalised` before scheduling an async callback chain. Because NEAR cross-contract calls are asynchronous, `fin_transfer_callback` can execute between the initial check and the callback that actually records the fast transfer state. The callback `fast_fin_transfer_to_near_callback` never re-checks finalization, so both paths independently send tokens to the recipient for the same transfer.

---

### Finding Description

**Root cause — inconsistent state check placement:**

In `fast_fin_transfer` (line 778), the guard against double-finalization reads from `finalised_transfers`:

```rust
if self.is_unified_transfer_finalised(&fast_fin_transfer_msg.transfer_id) {
    env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
}
``` [1](#0-0) 

The function then schedules an async callback chain (`check_or_pay_ft_storage` → `fast_fin_transfer_to_near_callback`) without recording any intermediate state. The actual state update — inserting into `fast_transfers` via `add_fast_transfer` — only happens inside the callback:

```rust
let required_balance = self
    .add_fast_transfer(fast_transfer, relayer_id, storage_payer.clone())
    .saturating_add(ONE_YOCTO);
``` [2](#0-1) 

`fast_fin_transfer_to_near_callback` contains **no call to `is_unified_transfer_finalised`** before it calls `send_tokens` to the recipient: [3](#0-2) 

**The race window:**

`process_fin_transfer_to_near` (called from `fin_transfer_callback`) checks `get_fast_transfer_status` to decide whether to redirect tokens to the relayer or to the original recipient:

```rust
let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());
let (recipient, msg, fee_recipient) = match fast_transfer_status {
    Some(status) => { ... self.remove_fast_transfer(...); (status.relayer, ...) }
    None => (recipient, transfer_message.msg.clone(), predecessor_account_id.clone()),
};
``` [4](#0-3) 

If `fin_transfer_callback` executes **after** `fast_fin_transfer` passes its guard but **before** `fast_fin_transfer_to_near_callback` runs, `get_fast_transfer_status` returns `None` (the fast transfer is not yet in `fast_transfers`). `process_fin_transfer_to_near` then sends tokens to the original recipient and inserts the transfer into `finalised_transfers` via `add_fin_transfer`: [5](#0-4) 

When `fast_fin_transfer_to_near_callback` subsequently runs, `add_fast_transfer` only checks whether the fast-transfer ID already exists in `fast_transfers` — it does **not** check `finalised_transfers`:

```rust
require!(
    self.fast_transfers.insert(...).is_none(),
    BridgeError::FastTransferAlreadyPerformed.as_ref()
);
``` [6](#0-5) 

Because `fin_transfer_callback` writes to `finalised_transfers` and `fast_fin_transfer_to_near_callback` writes to `fast_transfers`, the two sets are disjoint. Neither write blocks the other, so both `send_tokens` calls succeed.

---

### Impact Explanation

**Critical — double-spend / unbacked token minting.**

For bridged (deployed) tokens: `process_fin_transfer_to_near` mints tokens to the recipient. `fast_fin_transfer_to_near_callback` then also mints tokens to the same recipient. `resolve_fast_transfer` attempts to burn from the bridge's balance, but the bridge already forwarded the relayer's tokens to the recipient, so the burn either fails silently (`.detach()`) or burns from an unrelated balance. The result is unbacked supply: tokens minted without corresponding locked collateral on the origin chain. [7](#0-6) 

For native tokens: the bridge pays out twice from its own balance, draining reserves and breaking collateralization.

---

### Likelihood Explanation

NEAR's asynchronous cross-contract call model makes this race window structurally reachable. `fast_fin_transfer` is called via `ft_on_transfer` (requiring a trusted relayer), but `fin_transfer` is a public entry point callable by any account. A recipient who knows a fast transfer is in flight can immediately submit a `fin_transfer` proof for the same transfer. The callback ordering is determined by NEAR's receipt scheduling, which is observable on-chain. No key compromise or colluding MPC signers are required — only a valid proof and a pending fast transfer.

---

### Recommendation

Add a re-check of `is_unified_transfer_finalised` at the top of `fast_fin_transfer_to_near_callback`, before `add_fast_transfer` and before `send_tokens`:

```rust
pub fn fast_fin_transfer_to_near_callback(
    &mut self,
    fast_transfer: &FastTransfer,
    storage_payer: AccountId,
    relayer_id: AccountId,
) -> Promise {
    require!(
        Self::check_storage_balance_result(0),
        BridgeError::StorageRecipientOmitted.as_ref()
    );

+   // Re-check: fin_transfer_callback may have run since fast_fin_transfer was called
+   if self.is_unified_transfer_finalised(&fast_transfer.transfer_id) {
+       // Refund relayer and abort
+       env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
+   }

    let required_balance = self
        .add_fast_transfer(fast_transfer, relayer_id, storage_payer.clone())
        ...
```

Alternatively, record a "fast transfer pending" sentinel into a dedicated set at the start of `fast_fin_transfer` (before the async call), and have `process_fin_transfer_to_near` treat a pending-but-not-yet-recorded fast transfer as equivalent to a completed one.

---

### Proof of Concept

1. Transfer T originates on EVM (origin_chain=Eth, origin_nonce=N). Locked tokens exist on NEAR.
2. Trusted relayer calls `ft_transfer_call` on the token contract with `FastFinTransferMsg` for transfer T. This invokes `fast_fin_transfer` on the bridge.
3. `fast_fin_transfer` passes the `is_unified_transfer_finalised` check (T not yet in `finalised_transfers`) and schedules `check_or_pay_ft_storage` → `fast_fin_transfer_to_near_callback`. No state is written yet.
4. Before the callback executes, any account submits `fin_transfer` with a valid proof for transfer T.
5. `fin_transfer_callback` → `process_fin_transfer_to_near`: `get_fast_transfer_status(T)` returns `None` (fast transfer not yet in `fast_transfers`). Tokens are sent to the original recipient. `add_fin_transfer(T)` inserts T into `finalised_transfers`.
6. `fast_fin_transfer_to_near_callback` executes. `add_fast_transfer` checks only `fast_transfers` (not `finalised_transfers`) — succeeds. `send_tokens` sends tokens to the recipient **a second time**.
7. Recipient holds 2× the bridged amount. For deployed tokens, `resolve_fast_transfer` burns from the bridge's zero balance (silently fails via `.detach()`), leaving unbacked supply in circulation. [8](#0-7) [3](#0-2) [9](#0-8)

### Citations

**File:** near/omni-bridge/src/lib.rs (L748-836)
```rust
    #[allow(clippy::needless_pass_by_value)]
    fn fast_fin_transfer(
        &mut self,
        token_id: AccountId,
        amount: U128,
        signer_id: AccountId,
        fast_fin_transfer_msg: FastFinTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(self.is_trusted_relayer(&signer_id), "Relayer is not active");

        let origin_token = self
            .get_token_address(
                fast_fin_transfer_msg.transfer_id.origin_chain,
                token_id.clone(),
            )
            .near_expect(BridgeError::TokenNotFound);

        let decimals = self
            .token_decimals
            .get(&origin_token)
            .near_expect(BridgeError::TokenDecimalsNotFound);

        let denormalized_amount =
            Self::denormalize_amount(fast_fin_transfer_msg.amount.0, decimals);
        let denormalized_fee = Self::denormalize_fee(&fast_fin_transfer_msg.fee, decimals);
        require!(
            denormalized_amount == amount.0 + denormalized_fee.fee.0,
            BridgeError::InvalidFastTransferAmount.as_ref()
        );

        if self.is_unified_transfer_finalised(&fast_fin_transfer_msg.transfer_id) {
            env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
        }

        let fast_transfer = FastTransfer {
            token_id: token_id.clone(),
            recipient: fast_fin_transfer_msg.recipient.clone(),
            amount: U128(denormalized_amount),
            fee: denormalized_fee,
            transfer_id: fast_fin_transfer_msg.transfer_id,
            msg: fast_fin_transfer_msg.msg,
        };

        if let OmniAddress::Near(recipient) = fast_fin_transfer_msg.recipient {
            let storage_deposit_amount = fast_fin_transfer_msg
                .storage_deposit_amount
                .map(|amount| amount.0)
                .unwrap_or_default();
            if storage_deposit_amount > 0 {
                self.update_storage_balance(
                    signer_id.clone(),
                    NearToken::from_yoctonear(storage_deposit_amount),
                    NearToken::from_yoctonear(0),
                );
            }

            let deposit_action = StorageDepositAction {
                account_id: recipient,
                token_id,
                storage_deposit_amount: fast_fin_transfer_msg
                    .storage_deposit_amount
                    .map(|amount| amount.0),
            };

            Self::check_or_pay_ft_storage(
                &deposit_action,
                &mut NearToken::from_yoctonear(storage_deposit_amount),
            )
            .then(
                Self::ext(env::current_account_id())
                    .with_static_gas(
                        FAST_TRANSFER_CALLBACK_GAS.saturating_add(FT_TRANSFER_CALL_GAS),
                    )
                    .fast_fin_transfer_to_near_callback(
                        &fast_transfer,
                        signer_id,
                        fast_fin_transfer_msg.relayer,
                    ),
            )
            .into()
        } else {
            self.fast_fin_transfer_to_other_chain(
                &fast_transfer,
                signer_id,
                fast_fin_transfer_msg.relayer,
            );
            PromiseOrPromiseIndexOrValue::Value(U128(0))
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L838-893)
```rust
    #[private]
    pub fn fast_fin_transfer_to_near_callback(
        &mut self,
        #[serializer(borsh)] fast_transfer: &FastTransfer,
        #[serializer(borsh)] storage_payer: AccountId,
        #[serializer(borsh)] relayer_id: AccountId,
    ) -> Promise {
        require!(
            Self::check_storage_balance_result(0),
            BridgeError::StorageRecipientOmitted.as_ref()
        );

        let OmniAddress::Near(recipient) = fast_transfer.recipient.clone() else {
            env::panic_str(BridgeError::InvalidState.to_string().as_str())
        };

        let required_balance = self
            .add_fast_transfer(fast_transfer, relayer_id, storage_payer.clone())
            .saturating_add(ONE_YOCTO);

        self.update_storage_balance(
            storage_payer,
            required_balance,
            NearToken::from_yoctonear(0),
        );

        env::log_str(
            &OmniBridgeEvent::FastTransferEvent {
                fast_transfer: fast_transfer.clone(),
                new_transfer_id: None,
            }
            .to_log_string(),
        );

        let amount_without_fee = U128(
            fast_transfer
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
        );
        self.send_tokens(
            fast_transfer.token_id.clone(),
            recipient,
            amount_without_fee,
            &fast_transfer.msg,
        )
        .then(
            Self::ext(env::current_account_id())
                .with_static_gas(RESOLVE_FAST_TRANSFER_GAS)
                .resolve_fast_transfer(
                    &fast_transfer.token_id,
                    &fast_transfer.id(),
                    amount_without_fee,
                    !fast_transfer.msg.is_empty(),
                ),
        )
    }
```

**File:** near/omni-bridge/src/lib.rs (L903-904)
```rust
        // Burn the tokens to ensure the locked tokens are not double-minted
        self.burn_tokens_if_needed(token_id.clone(), amount);
```

**File:** near/omni-bridge/src/lib.rs (L1867-1902)
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
```

**File:** near/omni-bridge/src/lib.rs (L2253-2264)
```rust
        require!(
            self.fast_transfers
                .insert(
                    &fast_transfer.id(),
                    &FastTransferStatusStorage::V0(FastTransferStatus {
                        relayer,
                        storage_owner,
                        finalised: false,
                    }),
                )
                .is_none(),
            BridgeError::FastTransferAlreadyPerformed.as_ref()
```
