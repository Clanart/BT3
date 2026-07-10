### Title
Dust-Amount Transfers Permanently Lock User Funds Due to Missing Pre-Normalization Minimum Check — (`near/omni-bridge/src/lib.rs`)

---

### Summary

The NEAR-side bridge accepts and locks/burns user tokens in `init_transfer` before any decimal-normalization check is applied. When `amount_without_fee` is smaller than the normalization divisor (`10^(origin_decimals − decimals)`), the subsequent `sign_transfer` call always panics with `InvalidAmountToTransfer`, and no recovery path exists. The tokens are permanently irrecoverable.

---

### Finding Description

**Step 1 — Tokens are locked/burned without a minimum-amount guard.**

`init_transfer` (called from `ft_on_transfer`) validates only that `fee < amount`: [1](#0-0) 

It then calls `init_transfer_internal`, which burns or locks the full `amount` before returning: [2](#0-1) 

There is no check that `amount - fee` is large enough to survive decimal normalization.

**Step 2 — Normalization in `sign_transfer` uses floor division.**

`normalize_amount` divides by `10^(origin_decimals − decimals)`: [3](#0-2) 

The code comment explicitly acknowledges that when `fee = 0`, dust stays locked/burned: [4](#0-3) 

**Step 3 — `sign_transfer` panics if the normalized amount is zero.**

After normalization, `sign_transfer` requires `amount_to_transfer > 0`: [5](#0-4) 

If `amount_without_fee < 10^(origin_decimals − decimals)`, this `require!` always panics. The transfer message remains in `pending_transfers` indefinitely.

**Step 4 — No cancellation or recovery path exists.**

`sign_transfer_callback` only removes the transfer message on successful signing when `fee.is_zero()`: [6](#0-5) 

When `sign_transfer` itself panics (before the MPC call), the callback is never reached. No admin, DAO, or user-facing cancel/refund function removes a stuck pending transfer and returns the locked tokens.

**Concrete example:**

- Token: NEAR-native token with `origin_decimals = 24`, `decimals = 18` (divisor = `10^6`)
- User sends `amount = 500_000` yoctoNEAR, `fee = 0`
- `init_transfer_internal` locks 500,000 yoctoNEAR in the bridge
- `sign_transfer` computes `normalize_amount(500_000, ...) = 0`
- `require!(0 > 0, ...)` → panic
- 500,000 yoctoNEAR is permanently locked; `sign_transfer` will always fail for this transfer ID

The same applies to any token where `origin_decimals > decimals` (e.g., a 24-decimal NEAR token bridging to an 18-decimal EVM representation).

---

### Impact Explanation

User funds are permanently frozen in the NEAR bridge contract (or permanently burned for deployed/bridged tokens) with no recovery mechanism. This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

Any unprivileged user who calls `ft_on_transfer` with a small amount triggers this path. For tokens with a 6-decimal normalization gap (common for NEAR↔EVM), any transfer of fewer than `10^6` base units permanently locks funds. This can happen accidentally (user sends a small test amount) or deliberately (griefing). The entry point is fully permissionless.

---

### Recommendation

Add a pre-normalization minimum check inside `init_transfer` (before tokens are locked/burned) to ensure `amount_without_fee >= 10^(origin_decimals − decimals)` for the destination chain's token decimals. Alternatively, perform the normalization check first and revert (returning tokens via NEP-141 refund) before any lock/burn action.

```rust
// In init_transfer, before init_transfer_internal:
let decimals = self.token_decimals.get(&token_address)...;
let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee()...,
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

---

### Proof of Concept

1. Register a NEAR-native token with `origin_decimals = 24`, `decimals = 18` on the bridge.
2. Call `ft_transfer_call` on the token contract with `amount = 1` and `msg` encoding an `InitTransferMsg` with `fee = U128(0)` and an ETH recipient.
3. Observe that `init_transfer_internal` locks 1 yoctoNEAR (emits `InitTransferEvent`).
4. Call `sign_transfer` for the resulting `TransferId`.
5. Observe the call panics with `ERR_INVALID_AMOUNT_TO_TRANSFER` because `normalize_amount(1, {decimals:18, origin_decimals:24}) = 0`.
6. Confirm the transfer message still exists in `pending_transfers` and the 1 yoctoNEAR remains locked with no way to recover it.

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
