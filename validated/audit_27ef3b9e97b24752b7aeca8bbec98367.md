### Title
Missing Re-Validation of Finalization State in `fast_fin_transfer_to_near_callback` Enables Double-Mint of Bridged Assets - (File: near/omni-bridge/src/lib.rs)

### Summary
The `fast_fin_transfer` flow for NEAR-bound recipients contains an async gap between the initial finalization guard and the actual token dispatch. If `fin_transfer` is processed and completes during that gap, the `fast_fin_transfer_to_near_callback` executes without re-checking finalization state, causing the recipient to receive tokens twice and inflating the bridged token supply beyond what is locked on the source chain.

### Finding Description

The `fast_fin_transfer` function (called via `ft_on_transfer`) performs a single upfront guard:

```rust
if self.is_unified_transfer_finalised(&fast_fin_transfer_msg.transfer_id) {
    env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
}
``` [1](#0-0) 

For NEAR-bound recipients the function then schedules an **asynchronous** storage-check promise before the actual token dispatch:

```rust
Self::check_or_pay_ft_storage(&deposit_action, ...)
    .then(
        Self::ext(env::current_account_id())
            ...
            .fast_fin_transfer_to_near_callback(&fast_transfer, signer_id, ...)
    )
    .into()
``` [2](#0-1) 

The callback `fast_fin_transfer_to_near_callback` that eventually sends tokens to the recipient contains **no re-check** of `is_unified_transfer_finalised` or `finalised_transfers`:

```rust
pub fn fast_fin_transfer_to_near_callback(...) -> Promise {
    require!(Self::check_storage_balance_result(0), ...);
    // ← NO check of finalised_transfers here
    let required_balance = self
        .add_fast_transfer(fast_transfer, relayer_id, storage_payer.clone())
        .saturating_add(ONE_YOCTO);
    ...
    self.send_tokens(fast_transfer.token_id.clone(), recipient, amount_without_fee, ...)
``` [3](#0-2) 

Meanwhile, `fin_transfer_callback` → `process_fin_transfer_to_near` checks `get_fast_transfer_status` to decide whether to redirect tokens to the relayer. If the fast-transfer record has **not yet been written** (because `add_fast_transfer` is only called inside the callback, after the async gap), `fast_transfer_status` is `None` and the function sends tokens directly to the original recipient and marks the transfer finalized:

```rust
let fast_transfer_status = self.get_fast_transfer_status(&fast_transfer.id());
...
let (recipient, msg, fee_recipient) = match fast_transfer_status {
    Some(status) => { ... }
    None => (recipient, transfer_message.msg.clone(), predecessor_account_id.clone()),
};
``` [4](#0-3) 

`add_fin_transfer` is called at the top of `process_fin_transfer_to_near`, marking the transfer finalized in `finalised_transfers` before any token dispatch: [5](#0-4) 

For deployed (bridged) tokens, `send_tokens` calls `mint` on the token contract: [6](#0-5) 

**Race sequence:**

| Step | Action | State |
|------|--------|-------|
| 1 | Relayer calls `fast_fin_transfer` for transfer T | Guard passes; `finalised_transfers` does not contain T; `fast_transfers` does not contain T |
| 2 | Async receipt: `check_or_pay_ft_storage` executes | `fast_fin_transfer_to_near_callback` receipt queued |
| 3 | Second relayer (or same) calls `fin_transfer` with proof for T | `verify_proof` receipt queued |
| 4 | `fin_transfer_callback` executes: `fast_transfer_status == None` → mints tokens to recipient; `add_fin_transfer` marks T finalized | Recipient receives X tokens; T in `finalised_transfers` |
| 5 | `fast_fin_transfer_to_near_callback` executes: **no re-check**; `add_fast_transfer` succeeds (key absent); mints tokens to recipient again | Recipient receives X tokens **again** |

The relayer's deposited tokens are burned in `resolve_fast_transfer`, but the bridge has already minted an extra X tokens to the recipient that are not backed by any locked collateral on the source chain. [7](#0-6) 

### Impact Explanation
For every bridged (deployed) token, the bridge mints tokens via `mint()`. When both `fin_transfer_callback` and `fast_fin_transfer_to_near_callback` complete for the same transfer, the recipient receives 2× the intended amount. The relayer's tokens are burned, but the net result is an unbacked token supply increase on NEAR — a direct unauthorized mint of bridged assets breaking bridge collateralization.

### Likelihood Explanation
Both `fin_transfer` and `fast_fin_transfer` require trusted-relayer status. The race does not require malicious intent; it can occur naturally when two relayers (or the same relayer) submit both transactions in close succession and NEAR's receipt scheduling causes `fin_transfer_callback` to be processed before `fast_fin_transfer_to_near_callback`. The async gap spans at least one NEAR block (the storage-check cross-contract call), giving a realistic window for `fin_transfer` receipts to interleave.

### Recommendation
Re-check finalization state at the start of `fast_fin_transfer_to_near_callback` before dispatching tokens:

```rust
pub fn fast_fin_transfer_to_near_callback(
    &mut self,
    fast_transfer: &FastTransfer,
    storage_payer: AccountId,
    relayer_id: AccountId,
) -> Promise {
    // Re-validate: if fin_transfer completed during the async gap, abort and refund
    if self.is_unified_transfer_finalised(&fast_transfer.transfer_id) {
        // Return full amount to relayer (ft_on_transfer will refund)
        env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
    }
    ...
}
```

Alternatively, write the fast-transfer record **synchronously** (before any async call) in `fast_fin_transfer` so that `process_fin_transfer_to_near` can detect it and redirect tokens to the relayer rather than the recipient.

### Proof of Concept

1. User initiates transfer T of 100 USDC from Ethereum to NEAR (recipient = `alice.near`).
2. Relayer R1 calls `ft_transfer_call(bridge, 100, FastFinTransferMsg{transfer_id: T, recipient: alice.near, ...})`.
   - `fast_fin_transfer` passes `is_unified_transfer_finalised` check (T not finalized).
   - Async receipt created for `check_or_pay_ft_storage`.
3. Relayer R2 (or R1) calls `fin_transfer` with the EVM proof for T.
   - `verify_proof` receipt created.
4. NEAR processes receipts such that `fin_transfer_callback` executes first:
   - `fast_transfer_status` is `None` (fast transfer not yet recorded).
   - Bridge mints 100 USDC to `alice.near`.
   - T added to `finalised_transfers`.
5. `fast_fin_transfer_to_near_callback` executes:
   - No check of `finalised_transfers`.
   - `add_fast_transfer` succeeds (key absent in `fast_transfers`).
   - Bridge mints another 100 USDC to `alice.near`.
   - R1's 100 USDC burned in `resolve_fast_transfer`.
6. Result: `alice.near` holds 200 USDC; only 100 USDC was locked on Ethereum. Bridge supply is unbacked by 100 USDC.

### Citations

**File:** near/omni-bridge/src/lib.rs (L778-780)
```rust
        if self.is_unified_transfer_finalised(&fast_fin_transfer_msg.transfer_id) {
            env::panic_str(BridgeError::TransferAlreadyFinalised.to_string().as_str());
        }
```

**File:** near/omni-bridge/src/lib.rs (L812-827)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L895-912)
```rust
    #[private]
    pub fn resolve_fast_transfer(
        &mut self,
        token_id: &AccountId,
        fast_transfer_id: &FastTransferId,
        amount: U128,
        is_ft_transfer_call: bool,
    ) -> U128 {
        // Burn the tokens to ensure the locked tokens are not double-minted
        self.burn_tokens_if_needed(token_id.clone(), amount);

        if Self::is_refund_required(is_ft_transfer_call) {
            self.remove_fast_transfer(fast_transfer_id);
            amount
        } else {
            U128(0)
        }
    }
```

**File:** near/omni-bridge/src/lib.rs (L1875-1876)
```rust
        let mut required_balance = self.add_fin_transfer(&transfer_message.get_transfer_id());

```

**File:** near/omni-bridge/src/lib.rs (L1879-1902)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L2082-2101)
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
```
