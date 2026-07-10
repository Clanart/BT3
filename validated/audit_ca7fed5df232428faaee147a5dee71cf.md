### Title
Transfer Amount Below Decimal Normalization Threshold Permanently Locks User Funds in `sign_transfer` - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When a NEAR user initiates a bridge transfer with an amount smaller than the decimal normalization divisor (`10^(origin_decimals - decimals)`), the transfer passes the only upfront validation (`fee < amount`) and tokens are immediately locked. However, every subsequent call to `sign_transfer` will permanently fail with `InvalidAmountToTransfer` because `normalize_amount` floor-divides the amount to zero. There is no cancel or refund path, so the user's tokens are irrecoverably locked.

---

### Finding Description

**Step 1 — Init transfer passes validation and locks tokens.**

`init_transfer` (called via `ft_on_transfer`) performs only one amount check:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

A transfer with `amount = 999_999` and `fee = 0` satisfies `0 < 999_999` and proceeds. `init_transfer_internal` then locks or burns the tokens:

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(..., transfer_message.amount.0);
```

The transfer message is stored in `pending_transfers`.

**Step 2 — `sign_transfer` normalizes the amount to zero.**

`normalize_amount` uses floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

For a token with `origin_decimals = 24` and `decimals = 18`, the divisor is `10^6 = 1_000_000`. Any `amount_without_fee < 1_000_000` normalizes to `0`.

**Step 3 — The `require!` guard in `sign_transfer` always fails.**

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);

require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

With `amount_to_transfer == 0`, this `require!` panics on every invocation. The transfer can never be signed, and therefore never finalized.

**Step 4 — No recovery path exists.**

`remove_transfer_message` is only called on successful signing (when `fee.is_zero()`) or during fee claim. Neither path is reachable for this transfer. There is no `cancel_transfer` or admin-rescue function.

---

### Impact Explanation

**Critical — Permanent irrecoverable lock of user funds.**

The user's tokens are burned or locked in `init_transfer_internal` and can never be recovered. The transfer message sits in `pending_transfers` indefinitely. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** The condition requires a token whose NEAR-side `origin_decimals` exceeds the destination-chain `decimals` (e.g., 24 vs 18, a common configuration for tokens with high precision). Any user who transfers fewer than `10^(origin_decimals - decimals)` token units — a plausible mistake for a token with 24 decimals where the minimum "safe" unit is 1,000,000 — will have their funds permanently locked. No privileged access is required; any bridge user can trigger this.

---

### Recommendation

Add a minimum-amount guard inside `init_transfer` (before locking tokens) that verifies the net amount after fee is at least `10^(origin_decimals - decimals)` for the destination token. Alternatively, look up the token's `Decimals` struct at init time and reject transfers whose `amount_without_fee` would normalize to zero:

```rust
let decimals = self.token_decimals.get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);
let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

This mirrors the check already present in `sign_transfer` but moves it to the point before tokens are locked.

---

### Proof of Concept

**Setup:** Token registered with `origin_decimals = 24`, `decimals = 18` (divisor = `10^6`).

1. User calls `ft_transfer_call` with `amount = 500_000`, `fee = 0`, destination = EVM chain.
2. `init_transfer` check: `0 < 500_000` → passes. [1](#0-0) 
3. `init_transfer_internal` burns/locks 500,000 token units. [2](#0-1) 
4. Relayer calls `sign_transfer` for this transfer.
5. `amount_without_fee() = 500_000 - 0 = 500_000`. [3](#0-2) 
6. `normalize_amount(500_000, {decimals:18, origin_decimals:24}) = 500_000 / 1_000_000 = 0`. [4](#0-3) 
7. `require!(0 > 0, ...)` → panics with `InvalidAmountToTransfer`. [5](#0-4) 
8. Transfer message remains in `pending_transfers`. `sign_transfer_callback` is never reached, so `remove_transfer_message` is never called. [6](#0-5) 
9. The 500,000 token units are permanently locked with no recovery path.

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

**File:** near/omni-bridge/src/lib.rs (L655-658)
```rust
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }
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

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-types/src/lib.rs (L593-595)
```rust
    pub fn amount_without_fee(&self) -> Option<u128> {
        self.amount.0.checked_sub(self.fee.fee.0)
    }
```
