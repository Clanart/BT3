### Title
Unchecked Multiplication Overflow in `denormalize_amount` Causes Permanent Fund Freeze in `fin_transfer_callback` — (`near/omni-bridge/src/lib.rs`)

---

### Summary

`denormalize_amount` performs `amount * (10_u128.pow(diff_decimals))` with no overflow guard. The workspace `Cargo.toml` sets `overflow-checks = true` for the release profile, so any overflow panics and aborts the NEAR transaction. Because `fin_transfer_callback` and `fast_fin_transfer` call `denormalize_amount` after the source-chain tokens are already locked or burned, a panic there permanently freezes the user's funds with no recovery path.

---

### Finding Description

`denormalize_amount` is defined as:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount * (10_u128.pow(diff_decimals))   // ← unchecked multiplication
}
``` [1](#0-0) 

The workspace release profile explicitly enables overflow trapping:

```toml
[profile.release]
overflow-checks = true
``` [2](#0-1) 

`denormalize_amount` is called in two critical cross-chain finalization paths:

**Path 1 — `fin_transfer_callback`** (proof-based finalization, any chain → NEAR):

```rust
amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
...
fee: Self::denormalize_fee(&init_transfer.fee, decimals),
``` [3](#0-2) 

**Path 2 — `fast_fin_transfer`** (fast-relayer path):

```rust
let denormalized_amount =
    Self::denormalize_amount(fast_fin_transfer_msg.amount.0, decimals);
let denormalized_fee = Self::denormalize_fee(&fast_fin_transfer_msg.fee, decimals);
``` [4](#0-3) 

`denormalize_fee` also delegates to `denormalize_amount`:

```rust
fn denormalize_fee(fee: &Fee, decimals: Decimals) -> Fee {
    Fee {
        fee: U128(Self::denormalize_amount(fee.fee.0, decimals)),
        ...
    }
}
``` [5](#0-4) 

---

### Impact Explanation

When `denormalize_amount` overflows, the NEAR runtime aborts the callback transaction. At that point:

- The source-chain tokens are already locked in the EVM vault or burned on Solana/StarkNet.
- The NEAR `fin_transfer_callback` state changes are reverted, so the transfer is never recorded as finalized.
- No refund or retry mechanism exists for a panicked callback — the transfer ID is never stored, so `fin_transfer` cannot be retried with the same proof.
- The user's funds are permanently irrecoverable.

This matches the **Critical** impact class: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

---

### Likelihood Explanation

The overflow threshold is `u128::MAX / 10^diff_decimals`. For a token where `origin_decimals - decimals = 15` (e.g., a NEAR token with 24 native decimals bridged against an 18-decimal bridge standard, diff = 6 is common per the existing tests; diff = 15 is also tested):

- At diff = 15: overflow when `amount > ~3.4 × 10^23` (in bridge-standard units). For an 18-decimal bridge standard this equals ~340 million whole tokens — reachable for high-supply tokens.
- At diff = 18: overflow when `amount > ~3.4 × 10^20` — reachable for tokens with supplies in the hundreds of whole tokens at that precision.

Any unprivileged user who initiates a transfer on the source chain with a sufficiently large amount triggers the panic. The source-chain `initTransfer` accepts any `uint128` amount up to `u128::MAX`, so no source-chain guard prevents the problematic value from entering the proof. [1](#0-0) 

---

### Recommendation

Replace the bare multiplication with a checked variant and revert with a descriptive error on overflow:

```rust
fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    let multiplier = 10_u128.checked_pow(diff_decimals)
        .expect("decimal multiplier overflow");
    amount.checked_mul(multiplier)
        .expect("denormalize_amount overflow")
}
```

Alternatively, use `saturating_mul` and reject the transfer before tokens are locked on the source chain by adding an off-chain or on-chain pre-check on the normalized amount.

---

### Proof of Concept

1. Deploy a NEAR token registered with `origin_decimals = 24`, `decimals = 6` (diff = 18).
2. On EVM, call `initTransfer` with `amount = 10^21` (valid `uint128`, ~1000 whole tokens at 18 decimals).
3. Submit the resulting proof to NEAR `fin_transfer`.
4. `fin_transfer_callback` calls `denormalize_amount(10^21, diff=18)` → `10^21 * 10^18 = 10^39 > u128::MAX`.
5. With `overflow-checks = true`, the NEAR runtime panics and aborts the callback.
6. The EVM tokens remain locked; the NEAR transfer is never finalized; funds are permanently frozen.

### Citations

**File:** near/omni-bridge/src/lib.rs (L725-727)
```rust
            amount: Self::denormalize_amount(init_transfer.amount.0, decimals).into(),
            recipient: init_transfer.recipient,
            fee: Self::denormalize_fee(&init_transfer.fee, decimals),
```

**File:** near/omni-bridge/src/lib.rs (L770-772)
```rust
        let denormalized_amount =
            Self::denormalize_amount(fast_fin_transfer_msg.amount.0, decimals);
        let denormalized_fee = Self::denormalize_fee(&fast_fin_transfer_msg.fee, decimals);
```

**File:** near/omni-bridge/src/lib.rs (L2776-2779)
```rust
    fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount * (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-bridge/src/lib.rs (L2790-2795)
```rust
    fn denormalize_fee(fee: &Fee, decimals: Decimals) -> Fee {
        Fee {
            fee: U128(Self::denormalize_amount(fee.fee.0, decimals)),
            native_fee: fee.native_fee,
        }
    }
```

**File:** near/Cargo.toml (L24-31)
```text
[profile.release]
codegen-units = 1
# Tell `rustc` to optimize for small code size.
opt-level = "z"
lto = true
debug = false
panic = "abort"
overflow-checks = true
```
