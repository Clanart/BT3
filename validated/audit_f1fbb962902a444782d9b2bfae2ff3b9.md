### Title
Sub-unit Transfer Amount Permanently Locks User Funds via Unsignable Pending Transfer - (File: near/omni-bridge/src/lib.rs)

### Summary
`sign_transfer` in the NEAR bridge contract reverts with `BridgeError::InvalidAmountToTransfer` whenever `normalize_amount(amount_without_fee, decimals) == 0`. Because `init_transfer` stores the transfer and locks/burns the user's tokens **before** any normalization check, a user who sends an amount smaller than the decimal-scaling divisor creates a pending transfer that can never be signed and never cancelled, permanently freezing their funds.

### Finding Description

`normalize_amount` performs floor division to convert a NEAR-native token amount to the destination chain's decimal representation: [1](#0-0) 

For a token registered with `origin_decimals = 24` (NEAR) and `decimals = 18` (EVM), `diff_decimals = 6` and the divisor is `10^6`. Any `amount_without_fee < 1_000_000` normalizes to `0`.

`sign_transfer` then hard-reverts on that zero: [2](#0-1) 

However, `init_transfer` — the function that stores the transfer and locks/burns tokens — performs **no** equivalent minimum-amount check. Its only guard is `fee < amount`: [3](#0-2) 

`init_transfer_internal` then immediately locks or burns the tokens and inserts the transfer into `pending_transfers`: [4](#0-3) 

There is no public cancel or refund function. `remove_transfer_message` is only reachable through `claim_fee_callback` (requires a proof of destination-chain finalization) or `sign_transfer_callback` (only reached after a successful `sign_transfer`): [5](#0-4) 

Neither path is reachable when `sign_transfer` always reverts. The transfer is permanently stuck.

The same entry point exists via `finish_withdraw_v2`, which also stores a transfer message with no normalization pre-check: [6](#0-5) 

The code comment for `normalize_amount` acknowledges dust but only in the context of a *remainder* after normalization, not the case where the entire amount normalizes to zero: [7](#0-6) 

### Impact Explanation

Any user who initiates a NEAR → EVM (or NEAR → Solana/StarkNet) transfer with `amount_without_fee < 10^(origin_decimals − decimals)` will have their tokens permanently locked in the bridge (native tokens) or permanently burned (bridged tokens) with no recovery path. This is an irrecoverable loss of user funds, matching the allowed critical impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

Any token whose NEAR representation uses more decimals than its destination-chain representation is affected (e.g., 24 NEAR decimals vs. 18 EVM decimals, divisor = 10^6). A user sending fewer than 1,000,000 base units triggers the freeze. This is reachable by any unprivileged bridge user via the public `ft_transfer_call` → `ft_on_transfer` → `init_transfer` path with no special preconditions.

### Recommendation

Add a normalization pre-check inside `init_transfer` (and `finish_withdraw_v2`) before storing the transfer and locking/burning tokens. Specifically, look up the destination token's `Decimals` and verify that `normalize_amount(amount_without_fee, decimals) > 0`; revert with a clear error (e.g., `BridgeError::InvalidAmountToTransfer`) if the check fails. This mirrors the guard already present in `sign_transfer` but moves it to the point where the user's funds are first committed.

### Proof of Concept

1. A token is registered with `origin_decimals = 24` (NEAR) and `decimals = 18` (EVM), so `diff_decimals = 6`.
2. User calls `ft_transfer_call` on the NEAR token with `amount = 500_000` and `fee = 0`, targeting the bridge.
3. `init_transfer` passes the `fee < amount` check (0 < 500_000). Tokens are locked. Transfer stored in `pending_transfers` with `amount = U128(500_000)`.
4. Relayer calls `sign_transfer` for this transfer.
5. `normalize_amount(500_000, Decimals { decimals: 18, origin_decimals: 24 })` = `500_000 / 1_000_000` = `0`.
6. `require!(0 > 0, BridgeError::InvalidAmountToTransfer)` → **PANIC**.
7. Every subsequent `sign_transfer` call for this transfer ID also panics. No cancel path exists. The 500,000 NEAR token units are permanently locked.

### Citations

**File:** near/omni-bridge/src/lib.rs (L482-485)
```rust
        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1314-1353)
```rust
    #[allow(clippy::needless_pass_by_value)]
    pub fn finish_withdraw_v2(
        &mut self,
        #[serializer(borsh)] sender_id: &AccountId,
        #[serializer(borsh)] amount: u128,
        #[serializer(borsh)] recipient: String,
    ) {
        let token_id = env::predecessor_account_id();
        require!(self.is_deployed_token(&token_id),);

        self.current_origin_nonce += 1;
        let destination_nonce = self.get_next_destination_nonce(ChainKind::Eth);

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount: U128(amount),
            recipient: OmniAddress::Eth(
                H160::from_str(&recipient).near_expect(BridgeError::InvalidRecipientAddress),
            ),
            fee: Fee {
                fee: U128(0),
                native_fee: U128(0),
            },
            sender: OmniAddress::Near(sender_id.clone()),
            msg: String::new(),
            destination_nonce,
            origin_transfer_id: None,
        };

        let required_storage_balance =
            self.add_transfer_message(transfer_message.clone(), sender_id.clone());

        self.update_storage_balance(
            env::current_account_id(),
            required_storage_balance,
            NearToken::from_yoctonear(0),
        );

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
```

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
    }
```

**File:** near/omni-bridge/src/lib.rs (L2194-2211)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
    }
```

**File:** near/omni-bridge/src/lib.rs (L2781-2787)
```rust
    /// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
    /// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
    /// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
