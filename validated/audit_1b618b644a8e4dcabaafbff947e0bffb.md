### Title
`init_transfer` Validates Raw `amount - fee` While `sign_transfer` Checks the Normalized (Reduced) Value, Permanently Locking Funds - (File: `near/omni-bridge/src/lib.rs`)

### Summary

`init_transfer` accepts a transfer as long as `fee < amount` (raw token units). However, `sign_transfer` later checks the **normalized** (decimal-scaled, floor-divided) value of `amount - fee` against `> 0`. When `origin_decimals > decimals` (e.g., NEAR 24-decimal token bridging to an 18-decimal EVM chain), any `amount - fee` smaller than `10^(origin_decimals - decimals)` normalizes to zero via floor division. The transfer is accepted and tokens are locked at `init_transfer` time, but `sign_transfer` always panics with `InvalidAmountToTransfer`, and there is no cancel path — permanently freezing the user's funds.

### Finding Description

**`init_transfer` fee check (line 554–557):**

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

This only checks the raw token-unit inequality. It does not account for the decimal normalization that will be applied later.

**`sign_transfer` normalized check (lines 475–485):**

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

`normalize_amount` applies floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

For a NEAR-native token with `origin_decimals = 24` bridging to an EVM chain with `decimals = 18`, `diff_decimals = 6`. Any `amount_without_fee < 10^6` normalizes to `0`, causing `sign_transfer` to panic.

**No recovery path exists:**
- `update_transfer_fee` enforces `fee.fee >= current_fee.fee` (line 400), so the fee can only be increased, making the normalized remainder even smaller.
- There is no public `cancel_transfer` or user-accessible refund function for pending transfers.
- The transfer message stays in `pending_transfers` indefinitely with tokens locked.

### Impact Explanation

A user whose transfer satisfies `fee < amount` (passes `init_transfer`) but has `normalize_amount(amount - fee) == 0` will have their tokens permanently locked in the bridge. `sign_transfer` will always revert with `InvalidAmountToTransfer`, and no on-chain mechanism allows the user to recover the locked tokens. This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation

The condition is reachable by any unprivileged bridge user calling `ft_transfer_call` → `ft_on_transfer` → `init_transfer`. For NEAR tokens (24 decimals) bridging to EVM (18 decimals), the threshold is `amount - fee < 10^6` yoctoNEAR. A user who sets a high fee (e.g., `fee = amount - 1`) or who sends a small amount with any non-zero fee can trigger this. The `update_transfer_fee` function even allows a third party to increase the fee on an existing transfer (if the token-fee component is unchanged), potentially pushing a borderline transfer into the broken state.

### Recommendation

Add the normalized-amount check at `init_transfer` time, mirroring the check already present in `sign_transfer`:

```rust
// After the existing fee < amount check:
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
);
if let Some(token_address) = token_address {
    if let Some(decimals) = self.token_decimals.get(&token_address) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee().unwrap_or(0),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

Alternatively, enforce the same check inside `update_transfer_fee` so that a fee update cannot push an existing transfer into the unresolvable state.

### Proof of Concept

1. Token is registered with `origin_decimals = 24`, `decimals = 18` (NEAR → EVM, `diff_decimals = 6`).
2. User calls `ft_transfer_call` with `amount = 1_000_001` and `InitTransferMsg { fee: 1_000_000, ... }`.
3. `init_transfer` check: `1_000_000 < 1_000_001` → passes. Tokens are locked.
4. Relayer calls `sign_transfer`.
5. `amount_without_fee() = 1_000_001 - 1_000_000 = 1`.
6. `normalize_amount(1, {24, 18}) = 1 / 10^6 = 0`.
7. `require!(0 > 0, ...)` → panics with `InvalidAmountToTransfer`.
8. Transaction reverts; transfer stays in `pending_transfers`; tokens remain locked forever. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3) [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L399-401)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
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

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
        require!(
            transfer_message.fee.fee < transfer_message.amount,
            BridgeError::InvalidFee.as_ref()
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
