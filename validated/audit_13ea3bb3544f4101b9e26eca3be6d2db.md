### Title
Dust Transfer Permanently Freezes User Funds via Zero Normalized Amount in `sign_transfer` - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a NEAR-side bridge transfer with an amount smaller than the decimal normalization unit (`10^(origin_decimals - decimals)`), the tokens are immediately burned or locked in `init_transfer_internal`, but every subsequent call to `sign_transfer` permanently reverts with `InvalidAmountToTransfer` because `normalize_amount` floor-divides to zero. There is no cancel or refund path, so the funds are irrecoverably frozen.

---

### Finding Description

The bridge's NEAR contract normalizes token amounts when crossing decimal boundaries. `normalize_amount` uses integer floor division:

```rust
// near/omni-bridge/src/lib.rs:2784-2787
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

In `sign_transfer`, this normalized value is checked to be non-zero before the MPC signing request is made:

```rust
// near/omni-bridge/src/lib.rs:475-485
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [2](#0-1) 

However, `init_transfer` only validates that `fee < amount`, not that `amount - fee >= 10^(origin_decimals - decimals)`:

```rust
// near/omni-bridge/src/lib.rs:554-557
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [3](#0-2) 

After this check passes, `init_transfer_internal` immediately burns or locks the tokens before any normalization check occurs:

```rust
// near/omni-bridge/src/lib.rs:1850-1857
if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
    self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
    self.lock_tokens_if_needed(
        transfer_message.get_destination_chain(),
        &token_id,
        transfer_message.amount.0,
    );
}
``` [4](#0-3) 

There is no public cancel or refund function that would unlock/re-mint tokens for a stuck transfer. The `remove_transfer_message_without_refund` helper is only called internally in the storage-balance-failure path, which executes *before* the burn/lock step. [5](#0-4) 

---

### Impact Explanation

**Permanent freezing of user funds** — matching the allowed critical impact class.

Once `init_transfer_internal` burns or locks the tokens, the transfer message is stored on-chain. Every relayer call to `sign_transfer` for that transfer ID will permanently revert with `InvalidAmountToTransfer`. Because there is no cancel/refund path, the tokens are irrecoverably lost.

---

### Likelihood Explanation

Any unprivileged bridge user can trigger this by sending a "dust" amount. For NEAR tokens bridged to an EVM chain (origin_decimals=24, EVM decimals=18), any amount below `10^6` yoctoNEAR (i.e., less than 0.000001 NEAR) with zero fee will produce a normalized amount of zero. The `init_transfer` entry point is fully public and the only guard is `fee < amount`, which passes for `amount=1, fee=0`. This is a realistic user mistake (sending a tiny test amount) or a deliberate griefing attack.

---

### Recommendation

Add a minimum-amount check in `init_transfer` (or `init_transfer_internal`) *before* burning/locking tokens, ensuring that `normalize_amount(amount - fee, decimals) > 0`. Alternatively, perform the normalization check at the point of token receipt and return the full amount to the sender if it would normalize to zero, consistent with how storage-balance failures already return `transfer_message.amount` to the caller.

---

### Proof of Concept

**Setup:** NEAR token with `origin_decimals = 24`, registered on an EVM chain with `decimals = 18` (diff = 6, so normalization divides by `10^6`).

1. User calls `ft_on_transfer` with `amount = 500_000` (500,000 yoctoNEAR) and `fee = 0`.
2. `init_transfer` check: `0 < 500_000` ✓ — passes.
3. `init_transfer_internal` burns 500,000 yoctoNEAR from the user. Transfer message stored on-chain.
4. Relayer calls `sign_transfer(transfer_id, ...)`.
5. `normalize_amount(500_000, {origin_decimals:24, decimals:18}) = 500_000 / 1_000_000 = 0`.
6. `require!(0 > 0, "ERR_INVALID_AMOUNT_TO_TRANSFER")` → **PANIC**.
7. No recovery path exists. The 500,000 yoctoNEAR are permanently burned/locked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

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

**File:** near/omni-bridge/src/lib.rs (L1838-1848)
```rust
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
