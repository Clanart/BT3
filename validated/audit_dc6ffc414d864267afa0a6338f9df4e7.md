### Title
Floor-Division in `normalize_amount` Silently Locks User Tokens With No Recovery Path - (File: near/omni-bridge/src/lib.rs)

### Summary
`normalize_amount` uses integer floor division to scale a token amount from its origin-chain decimal precision to NEAR's stored precision. When a user initiates a transfer whose `amount_without_fee()` is smaller than the divisor `10^(origin_decimals − decimals)`, the entire normalized amount truncates to zero. `sign_transfer` then panics with `InvalidAmountToTransfer`, but the user's tokens are already locked or burned in the bridge with no visible cancel or refund path, causing permanent irrecoverable loss.

---

### Finding Description

`normalize_amount` is defined as:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

When `origin_decimals > decimals` (e.g., a token with 18 decimals on Ethereum but 6 on NEAR, giving `diff_decimals = 12`), the divisor is `10^12`. Any transfer whose `amount_without_fee()` is less than `10^12` (i.e., less than 1 μtoken in 6-decimal terms) produces a normalized result of **zero** via integer truncation.

`sign_transfer` then calls `normalize_amount` on the already-locked amount and enforces a `> 0` guard:

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
``` [2](#0-1) 

The panic in `sign_transfer` leaves the transfer message in storage and the user's tokens locked or burned. No cancel or refund function is visible in the contract to recover from this state.

The inline SECURITY.md comment at the `normalize_amount` definition explicitly covers only the **remainder** (dust) case — where `normalize_amount(amount) > 0` but `denormalize(normalize(amount)) < amount`:

> "When fee = 0, dust stays locked/burned." [3](#0-2) 

It does **not** cover the case where the entire `amount_without_fee()` normalizes to zero, which is a qualitatively different and more severe outcome.

---

### Impact Explanation

A user who sends a transfer amount smaller than `10^(origin_decimals − decimals)` will have their tokens permanently locked in the NEAR bridge contract (for native tokens) or permanently burned (for bridged tokens). `sign_transfer` will always panic for that transfer ID, and no recovery path exists. This matches the allowed impact: **Critical — Permanent freezing / irrecoverable lock of user funds in bridge flows.**

---

### Likelihood Explanation

- Tokens with a large decimal gap between origin chain and NEAR (e.g., 18 vs. 6) are common (USDC, USDT, WBTC-style tokens).
- A user sending a "dust" amount (e.g., a few hundred wei of an 18-decimal token) triggers the condition.
- The condition can also be triggered by a user who sets a fee close to the total amount, leaving `amount_without_fee()` below the threshold.
- No on-chain validation prevents the initial lock from occurring; the check only fires later in `sign_transfer`.

Likelihood is **low-to-medium** for accidental triggering and **medium** for deliberate griefing of a specific user's transfer.

---

### Recommendation

Add a normalization check at the point of transfer initiation (in `ft_on_transfer` or the equivalent entry point) before locking or burning tokens, and reject the transfer early if `normalize_amount(amount_without_fee()) == 0`. Alternatively, implement a `cancel_transfer` function that allows users to reclaim locked tokens when a transfer cannot be finalized.

```diff
+ let normalized = Self::normalize_amount(amount_without_fee, decimals);
+ require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
  // only then lock/burn tokens
```

---

### Proof of Concept

1. A token is registered with `origin_decimals = 18`, `decimals = 6` (diff = 12, divisor = `10^12`).
2. User calls `ft_transfer_call` to the bridge with `amount = 500` (500 wei, well below `10^12`), `fee = 0`.
3. Bridge accepts the transfer, locks 500 tokens, stores the `TransferMessage`.
4. Relayer calls `sign_transfer` for this transfer ID.
5. `normalize_amount(500, decimals)` = `500 / 10^12` = **0** (integer truncation).
6. `require!(0 > 0, ...)` panics with `InvalidAmountToTransfer`.
7. Transfer message remains in storage; 500 tokens remain locked forever.
8. No `cancel_transfer` exists; user cannot recover funds. [1](#0-0) [2](#0-1)

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

**File:** near/omni-bridge/src/lib.rs (L2781-2783)
```rust
    /// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
    /// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
    /// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
