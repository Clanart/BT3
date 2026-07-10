Based on my analysis of the production code, I need to trace the exact call sequence and verify each step.

**Step 1: `sign_transfer` — the panic site** [1](#0-0) 

`normalize_amount` is called on `amount_without_fee()`, and the result is immediately guarded by `require!(amount_to_transfer > 0, ...)`. If `normalize_amount` returns 0, the function panics here.

**Step 2: `normalize_amount` arithmetic**

The function is not shown in the truncated output, but the decimal struct is: [2](#0-1) 

For `decimals={decimals:6, origin_decimals:24}`, `diff = 24 - 6 = 18`, so `normalize_amount(1) = 1 / 10^18 = 0` by integer floor division. This is confirmed by the comment in `claim_fee_callback`: [3](#0-2) 

**Step 3: `init_transfer` — no pre-storage guard** [4](#0-3) 

The only amount check before storing is `fee < amount`. There is **no** check that `normalize_amount(amount - fee) > 0`. The transfer is stored in `pending_transfers` and tokens are locked/burned before `sign_transfer` is ever called.

**Step 4: No cancellation path**

`remove_transfer_message` is only called in two places:
- `sign_transfer_callback` — only when signing *succeeds* and fee is zero
- `claim_fee_callback` — only after proof verification of a completed transfer [5](#0-4) 

There is no user-callable cancel/refund function. `storage_unregister` explicitly blocks unregistration while pending transfers exist: [6](#0-5) 

---

### Title
Permanent token lock when sub-unit transfer amount normalizes to zero — (`near/omni-bridge/src/lib.rs`)

### Summary
`init_transfer` stores a transfer and locks/burns user tokens without verifying that `normalize_amount(amount - fee) > 0`. When `sign_transfer` is later called, it panics on `require!(amount_to_transfer > 0)`, leaving the transfer permanently stuck in `pending_transfers` with no cancellation path.

### Finding Description
When a NEAR-origin token has `origin_decimals` significantly larger than `decimals` (e.g., `{decimals: 6, origin_decimals: 24}`, diff = 18), any transferred amount below `10^18` normalizes to zero via integer floor division. The `init_transfer` path stores the transfer and retains the tokens (returning `U128(0)` to the NEP-141 callback, meaning no refund) without performing this check. The `sign_transfer` function, called later by a relayer, then panics at:

```rust
require!(amount_to_transfer > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

Since there is no user-callable cancel or refund function, and `remove_transfer_message` is only reachable via successful signing or proof-verified fee claim, the transfer and the user's tokens are permanently frozen.

### Impact Explanation
**Critical — Permanent freezing of user funds.** The user's tokens are burned or locked in the bridge contract with no recovery path. The `storage_unregister` function actively blocks account cleanup while pending transfers exist, compounding the lock.

### Likelihood Explanation
**Medium.** The decimal configuration `{decimals: 6, origin_decimals: 24}` is realistic for tokens like USDC bridged from a chain using 24-decimal representation. A user sending a dust or test amount (e.g., `1` yocto-unit) would trigger this. No privileged access is required — only a standard `ft_transfer_call` to the bridge.

### Recommendation
Add a normalization check inside `init_transfer` before storing the transfer:

```rust
let token_address = self.get_token_address(
    init_transfer_msg.get_destination_chain(),
    token_id.clone(),
);
if let Some(addr) = token_address {
    if let Some(decimals) = self.token_decimals.get(&addr) {
        let normalized = Self::normalize_amount(
            transfer_message.amount.0 - transfer_message.fee.fee.0,
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

This mirrors the guard already present in `sign_transfer` and prevents the transfer from ever being stored with an un-signable amount.

### Proof of Concept
1. Deploy a token with `origin_decimals=24`, `decimals=6` (diff=18).
2. Call `ft_transfer_call(amount=1, msg=InitTransfer{...})` on the bridge.
3. `init_transfer` stores the transfer; `ft_on_transfer` returns `U128(0)` — tokens are kept.
4. Call `sign_transfer(transfer_id, ...)`.
5. `normalize_amount(1, {6, 24})` = `1 / 10^18` = `0`.
6. `require!(0 > 0)` panics — `sign_transfer` reverts.
7. Query `pending_transfers` — the transfer still exists.
8. No cancel function exists; tokens are permanently locked.

### Citations

**File:** near/omni-bridge/src/lib.rs (L471-485)
```rust
        let decimals = self
            .token_decimals
            .get(&token_address)
            .near_expect(BridgeError::TokenDecimalsNotFound);
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

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L655-658)
```rust
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }
```

**File:** near/omni-bridge/src/lib.rs (L1128-1131)
```rust
        // Fee includes both the user-specified fee and any dust lost during decimal
        // normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
        // due to floor division, the difference naturally captures the normalization remainder.
        let fee = transfer_message.amount.0 - denormalized_amount;
```

**File:** near/omni-bridge/src/storage.rs (L132-136)
```rust
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Decimals {
    pub decimals: u8,
    pub origin_decimals: u8,
}
```

**File:** near/omni-bridge/src/storage.rs (L222-227)
```rust
        if !force.unwrap_or_default() {
            require!(
                storage.total.saturating_sub(storage.available)
                    == self.required_balance_for_account(),
                BridgeError::StoragePendingTransfers.as_ref()
            );
```
