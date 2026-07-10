### Title
Permanent Freezing of User Funds via `normalize_amount` Returning Zero in `sign_transfer` - (File: `near/omni-bridge/src/lib.rs`)

### Summary

In `near/omni-bridge/src/lib.rs`, the `sign_transfer` function computes `amount_to_transfer` by calling `normalize_amount` (which applies floor division to convert between decimal representations) and then hard-panics if the result is zero. Because user tokens are already irrevocably locked or burned during `init_transfer`, and no cancel/refund path exists for pending transfers, any transfer whose post-fee amount normalizes to zero is permanently frozen.

### Finding Description

The `sign_transfer` function retrieves the stored transfer, normalizes the amount from the NEAR-side decimal representation to the destination chain's decimal representation, and then enforces a strict non-zero check:

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
``` [1](#0-0) 

The `Decimals` struct stores both `decimals` (destination chain) and `origin_decimals` (NEAR side): [2](#0-1) 

The `claim_fee_callback` function contains an explicit comment confirming that `normalize_amount` uses floor division and that `denormalize(normalize(x)) <= x`:

```rust
// Fee includes both the user-specified fee and any dust lost during decimal
// normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
// due to floor division, the difference naturally captures the normalization remainder.
let fee = transfer_message.amount.0 - denormalized_amount;
``` [3](#0-2) 

When the destination chain has significantly fewer decimals than the NEAR-side token (e.g., a NEAR token with 24 decimals bridging to a 6-decimal EVM token), any amount smaller than `10^(origin_decimals - decimals)` normalizes to zero via floor division. The `require!` then panics, permanently blocking the transfer.

The user's tokens are already consumed at `init_transfer` time — either burned (for deployed/bridged tokens) or locked: [4](#0-3) 

There is no `cancel_transfer` or user-accessible refund path in the contract. The only removal of a pending transfer happens inside `sign_transfer_callback` (only reachable after MPC signing succeeds) or `claim_fee_callback` (only reachable after a successful `fin_transfer` proof). Neither is reachable if `sign_transfer` always panics before reaching the MPC call.

### Impact Explanation

**Critical — Permanent freezing of user funds.**

A user who deposits a small amount of a high-decimal NEAR token destined for a low-decimal chain (e.g., a 24-decimal NEAR token → 6-decimal EVM USDC equivalent) will have their tokens burned/locked in `init_transfer`. The resulting pending transfer can never be finalized because `sign_transfer` will always panic with `InvalidAmountToTransfer`. With no cancel or refund mechanism, the funds are irrecoverably frozen in the bridge contract.

### Likelihood Explanation

**Medium.** The condition is triggered whenever:
1. A token pair has a large decimal difference (e.g., 24 NEAR-side vs. 6 EVM-side — a common real-world configuration for stablecoins).
2. The user's deposit amount (minus fee) is less than `10^(origin_decimals - destination_decimals)`.

The `init_transfer` validation only checks `fee.fee < amount`; it does not check whether `amount - fee` will survive normalization: [5](#0-4) 

Any user depositing a "dust" amount relative to the decimal gap triggers this permanently, without any on-chain warning.

### Recommendation

1. **Add a pre-check in `init_transfer`** that rejects transfers whose `amount - fee` would normalize to zero, so users receive an immediate revert with their tokens returned rather than having them locked.
2. **Replace the `require!` in `sign_transfer` with a graceful error** that either emits a refund event or provides an admin-callable escape hatch to return locked/burned tokens to the sender.
3. **Add a `cancel_transfer` function** (callable by the original sender or DAO) that removes the pending transfer and refunds/re-mints the locked tokens.

### Proof of Concept

**Setup:** Token `foo.near` has `origin_decimals = 24`, destination EVM token has `decimals = 6`. Decimal gap = 18, so the minimum transferable unit is `10^18` (1 NEAR-side token unit = `10^18` raw units).

1. User calls `ft_transfer_call` on `foo.near` with `amount = 5 * 10^17` (half a unit in NEAR decimals, a valid non-zero amount). Fee is set to `0`.
2. `init_transfer` passes the `fee.fee < amount` check (`0 < 5*10^17`). Tokens are burned. Transfer stored in `pending_transfers`.
3. Relayer calls `sign_transfer(transfer_id, ...)`.
4. `normalize_amount(5 * 10^17, {decimals: 6, origin_decimals: 24})` = `5 * 10^17 / 10^18` = `0` (floor division).
5. `require!(0 > 0, ...)` → **panic**. Transaction reverts.
6. Transfer remains in `pending_transfers`. User's `5 * 10^17` units of `foo.near` are permanently burned with no recovery path. [6](#0-5)

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

**File:** near/omni-bridge/src/lib.rs (L1128-1131)
```rust
        // Fee includes both the user-specified fee and any dust lost during decimal
        // normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
        // due to floor division, the difference naturally captures the normalization remainder.
        let fee = transfer_message.amount.0 - denormalized_amount;
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

**File:** near/omni-bridge/src/storage.rs (L131-136)
```rust
#[near(serializers=[borsh, json])]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Decimals {
    pub decimals: u8,
    pub origin_decimals: u8,
}
```
