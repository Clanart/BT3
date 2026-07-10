### Title
Sender Can Permanently Lock Own Funds by Setting Fee to `amount - 1` via `update_transfer_fee`, Making `sign_transfer` Permanently Revert - (`near/omni-bridge/src/lib.rs`)

### Summary

The `update_transfer_fee` function allows a transfer sender to raise the token fee up to `amount - 1`. For tokens where `origin_decimals > decimals` (e.g., a NEAR-native token with 24 decimals bridging to an EVM chain with 18 decimals), setting `fee = amount - 1` causes `amount_without_fee() = 1`, and `normalize_amount(1, decimals)` floors to `0`. The subsequent `sign_transfer` call then permanently reverts with `ERR_INVALID_AMOUNT_TO_TRANSFER`. Because there is no cancel or admin-recovery path for `pending_transfers`, the user's tokens (already burned or locked at `init_transfer` time) are irrecoverably frozen.

### Finding Description

**Step 1 — `update_transfer_fee` permits fee = amount − 1.** [1](#0-0) 

The only upper bound on the new fee is `fee.fee < transfer.message.amount`, so `fee.fee = amount - 1` is accepted. No check is made that the post-fee amount survives decimal normalization.

**Step 2 — `sign_transfer` normalizes the residual amount and requires it to be non-zero.** [2](#0-1) 

`normalize_amount` performs floor division: [3](#0-2) 

For a token with `origin_decimals = 24` and `decimals = 18` (divisor = 10^6), any `amount_without_fee() < 1_000_000` normalizes to `0`, causing the `require!(amount_to_transfer > 0)` guard to panic.

**Step 3 — Tokens are already consumed at `init_transfer` time.**

When `init_transfer_internal` succeeds, deployed tokens are burned and native tokens are locked: [4](#0-3) 

There is no `cancel_transfer` or DAO-accessible removal path for `pending_transfers`. The only removal paths are `remove_transfer_message` (called from `claim_fee_callback`) and `remove_transfer_message_without_refund` (called on storage failure), neither of which is reachable once the transfer is stuck. [5](#0-4) 

**Step 4 — Passing `fee: None` to `sign_transfer` does not help.**

Even if the relayer omits the fee check by passing `fee: None`, `amount_to_transfer` is still computed from the stored `amount_without_fee()`, which is `1` after the fee update. The normalization still returns `0` and the call still reverts. [6](#0-5) 

### Impact Explanation

The user's tokens are burned or locked at `init_transfer` time and can never be recovered. The transfer entry remains in `pending_transfers` indefinitely with no finalization path. This is an irrecoverable lock of user funds in the bridge, matching the **High** impact category: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

### Likelihood Explanation

The condition requires:
1. A token registered with `origin_decimals > decimals` — common for NEAR-native tokens (24 decimals) bridging to EVM (18 decimals), giving a divisor of 10^6.
2. The sender calls `update_transfer_fee` with `fee.fee = amount - 1` where `amount ≤ divisor`.

A user who initiates a small transfer (e.g., `amount = 500_000` yocto-units of a 24-decimal token) and then raises the fee to `499_999` triggers the condition. The action is callable by the sender at any time before `sign_transfer` is executed, with no cost beyond the native-fee deposit difference.

### Recommendation

Add a normalization check inside `update_transfer_fee` before accepting the new fee:

```rust
// In update_transfer_fee, after computing the new fee:
let token_address = self.get_token_address(
    transfer.message.get_destination_chain(),
    self.get_token_id(&transfer.message.token),
);
if let Some(token_address) = token_address {
    if let Some(decimals) = self.token_decimals.get(&token_address) {
        let residual = transfer.message.amount.0
            .checked_sub(fee.fee.0)
            .near_expect(BridgeError::InvalidFee);
        require!(
            Self::normalize_amount(residual, decimals) > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
    }
}
```

This mirrors the same guard already present in `sign_transfer` and prevents the fee from being raised to a value that makes the transfer permanently unsignable.

### Proof of Concept

```
1. Token registered: origin_decimals = 24, decimals = 18 (divisor = 1_000_000).
2. Alice calls ft_transfer_call → init_transfer with amount = 500_000, fee = 0.
   → Tokens burned/locked. Transfer stored in pending_transfers.
3. Alice calls update_transfer_fee(transfer_id, Fee { fee: 499_999, native_fee: 0 }).
   → Accepted: 499_999 < 500_000. amount_without_fee() = 1.
4. Relayer calls sign_transfer(transfer_id, fee_recipient, fee: None).
   → normalize_amount(1, {origin=24, dest=18}) = 1 / 1_000_000 = 0.
   → require!(0 > 0) panics: ERR_INVALID_AMOUNT_TO_TRANSFER.
5. No recovery path exists. Alice's 500_000 units are permanently locked.
```

### Citations

**File:** near/omni-bridge/src/lib.rs (L399-402)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );
```

**File:** near/omni-bridge/src/lib.rs (L455-460)
```rust
        if let Some(fee) = &fee {
            require!(
                &transfer_message.fee == fee,
                BridgeError::InvalidFee.as_ref()
            );
        }
```

**File:** near/omni-bridge/src/lib.rs (L475-485)
```rust
        let amount_to_transfer = Self::normalize_amount(
            transfer_message
                .amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );

        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L1850-1857)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
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

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
