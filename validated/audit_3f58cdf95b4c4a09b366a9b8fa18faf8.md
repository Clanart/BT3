### Title
`normalize_amount` Floor Division Returns Zero for Sub-Unit Transfers, Permanently Locking User Funds — (`near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a NEAR-to-other-chain transfer with an amount smaller than the minimum representable unit on the destination chain (`10^(origin_decimals − decimals)`), `normalize_amount` returns `0`. The `sign_transfer` function then panics with `InvalidAmountToTransfer`, but the user's tokens are already locked in the bridge with no cancel or refund path. The result is a permanent, irrecoverable lock of user funds.

---

### Finding Description

`normalize_amount` performs integer floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

When `origin_decimals > decimals` (e.g., a token registered with `origin_decimals = 24`, `decimals = 18`, giving `diff_decimals = 6`), any transfer amount below `10^6 = 1_000_000` normalizes to `0`.

`sign_transfer` calls `normalize_amount` on `amount_without_fee()` and then guards against a zero result:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [2](#0-1) 

The guard prevents the MPC signing step from proceeding, but it does **not** refund the user. The user's tokens were already transferred to the bridge in the preceding `ft_transfer_call` / `init_transfer` step and are stored in a `TransferMessage`. No public cancel or refund entry point was found in the contract. The `refund` helper is an internal utility, not a user-callable function. [3](#0-2) 

The existing code comment acknowledges only the "dust" case (sub-unit remainder after normalization of a larger amount):

> Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred to the destination chain. When fee = 0, dust stays locked/burned. [4](#0-3) 

The unacknowledged case is when the **entire** `amount_without_fee()` is below the minimum unit, causing the whole transfer amount — not just dust — to be permanently locked.

---

### Impact Explanation

**Permanent freezing / irrecoverable lock of user funds in the bridge vault.**

A user who sends any amount strictly less than `10^(origin_decimals − decimals)` (e.g., fewer than 1,000,000 base units for a token with `diff_decimals = 6`) will have their tokens locked in the NEAR bridge contract forever:

- `sign_transfer` always panics → MPC signature is never produced → `fin_transfer` on the destination chain is never callable.
- The fee can only be **increased** via `update_transfer_fee`, which makes `amount_without_fee()` smaller, not larger — the user cannot self-rescue.
- No cancel/refund function is exposed to the user.

---

### Likelihood Explanation

**Medium.** Tokens with `origin_decimals > decimals` are a normal and expected configuration (e.g., a 24-decimal NEAR-native token bridged to an 18-decimal EVM representation). Any user who sends a "small" amount — whether by mistake, by UI rounding, or because the token has low market value per base unit — triggers the lock. No privileged role or special condition is required beyond a valid token registration with a decimal gap.

---

### Recommendation

Add a validation at `init_transfer` / `ft_on_transfer` time (before tokens are locked) that rejects amounts whose `normalize_amount` result would be zero:

```rust
require!(
    Self::normalize_amount(amount_without_fee, decimals) > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

Alternatively, implement a user-callable `cancel_transfer` that returns locked tokens when a transfer has not been signed within a timeout period.

---

### Proof of Concept

**Setup:**
- Token registered with `origin_decimals = 24`, `decimals = 18` → `diff_decimals = 6`, minimum transferable unit = `1_000_000`.
- User calls `ft_transfer_call` on the NEAR token contract, transferring `500_000` base units to the bridge with `fee = 0`.
- Bridge stores a `TransferMessage` with `amount = 500_000`.

**Execution:**
1. Relayer calls `sign_transfer(transfer_id, None, None)`.
2. `normalize_amount(500_000, Decimals { origin_decimals: 24, decimals: 18 })` → `500_000 / 1_000_000 = 0`.
3. `require!(0 > 0, ...)` → **panic: `ERR_INVALID_AMOUNT_TO_TRANSFER`**.
4. The `TransferMessage` remains in storage; the 500,000 base-unit tokens remain locked in the bridge.
5. The user cannot increase `amount_without_fee()` (fee can only increase, not decrease).
6. No cancel path exists → tokens are permanently locked.

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

**File:** near/omni-bridge/src/lib.rs (L2770-2773)
```rust
    fn refund(account_id: AccountId, amount: NearToken) {
        if !amount.is_zero() {
            Promise::new(account_id).transfer(amount).detach();
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
