### Title
Tokens Permanently Locked/Burned When `normalize_amount(amount_without_fee)` Rounds to Zero — (`File: near/omni-bridge/src/lib.rs`)

---

### Summary

`init_transfer` accepts and locks/burns user tokens without verifying that `amount_without_fee` meets the minimum transferable unit for the destination chain. When `normalize_amount(amount_without_fee)` floors to zero due to decimal-difference division, `sign_transfer` permanently panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`, and there is no cancel path. The tokens are irrecoverably locked or burned.

---

### Finding Description

**Step 1 — Tokens are locked/burned unconditionally in `init_transfer_internal`.**

When a user calls `ft_transfer_call` with an `InitTransfer` message, `init_transfer` validates only that `fee.fee < amount`: [1](#0-0) 

It then calls `init_transfer_internal`, which immediately burns (for deployed tokens) or locks the full `amount` before any decimal-normalization check: [2](#0-1) 

**Step 2 — `sign_transfer` normalizes `amount_without_fee` and panics if the result is zero.**

`normalize_amount` uses floor division by `10^(origin_decimals − decimals)`: [3](#0-2) 

`sign_transfer` then requires the normalized result to be strictly positive: [4](#0-3) 

If `amount_without_fee < 10^(origin_decimals − decimals)`, `normalize_amount` returns 0 and `sign_transfer` panics on every call — permanently.

**Step 3 — No recovery path exists.**

`update_transfer_fee` only allows the fee to be *increased* (making `amount_without_fee` even smaller): [5](#0-4) 

`sign_transfer_callback` removes the pending transfer only when the MPC signing succeeds and fee is zero: [6](#0-5) 

Because `sign_transfer` panics before the MPC call is ever made, the callback is never reached. There is no `cancel_transfer` function. The transfer message stays in `pending_transfers` forever, and the burned/locked tokens are unrecoverable.

---

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

Any user who initiates a transfer where `amount − fee < 10^(origin_decimals − decimals)` loses their tokens permanently. For a token registered with `origin_decimals = 24` and `decimals = 18` (a common NEAR-to-EVM pairing), any transfer where `amount_without_fee < 1_000_000` (in the 24-decimal unit) triggers this. The tokens are burned or locked on NEAR, the pending transfer can never be signed, and there is no escape hatch.

---

### Likelihood Explanation

**Medium.** The condition is reachable by any unprivileged user via `ft_transfer_call`. A user bridging a small token amount (e.g., a dust cleanup or a test transfer) with a non-trivial fee, or simply bridging an amount below the minimum representable unit on the destination chain, will silently trigger the lock. The protocol provides no pre-flight view function to check whether a given `(amount, fee)` pair is signable, and the only on-chain guard (`fee < amount`) does not catch this case.

---

### Recommendation

**Short term:** Add a validation in `init_transfer` (before burning/locking) that `normalize_amount(amount_without_fee, decimals) > 0`. Reject the transfer early so `ft_transfer_call` returns the tokens to the sender.

**Long term:** Expose a view function (e.g., `get_min_transferable_amount(token, destination_chain)`) so callers can compute the minimum valid `amount_without_fee` off-chain before submitting. Consider adding a `cancel_transfer` function that allows the original sender to reclaim locked/burned tokens for transfers that have been pending beyond a timeout.

---

### Proof of Concept

Assume a token is registered with `origin_decimals = 24`, `decimals = 18` (decimal diff = 6, so minimum unit = `1_000_000`).

1. Alice calls `ft_transfer_call` on the token contract with `amount = 500_000` and `fee = 0`, targeting an EVM recipient.
2. `init_transfer` passes the `fee < amount` check (`0 < 500_000` ✓).
3. `init_transfer_internal` burns `500_000` units of Alice's deployed token.
4. The relayer calls `sign_transfer` for Alice's transfer.
5. `normalize_amount(500_000 − 0, {decimals:18, origin_decimals:24})` = `500_000 / 1_000_000` = `0`.
6. `require!(amount_to_transfer > 0, ...)` panics → `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. The relayer retries — same result every time.
8. `update_transfer_fee` can only increase the fee, making `amount_without_fee` smaller.
9. Alice's `500_000` token units are permanently burned; the pending transfer is stuck forever. [4](#0-3) [3](#0-2) [2](#0-1)

### Citations

**File:** near/omni-bridge/src/lib.rs (L399-402)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );
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
