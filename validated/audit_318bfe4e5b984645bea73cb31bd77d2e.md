### Title
Normalized Amount Rounds to Zero in `sign_transfer`, Permanently Locking User Funds - (File: `near/omni-bridge/src/lib.rs`)

### Summary

When a user initiates a NEAR-side bridge transfer (`init_transfer`) with a small token amount relative to the decimal difference between the NEAR representation and the destination chain representation, the subsequent `sign_transfer` call will always panic with `ERR_INVALID_AMOUNT_TO_TRANSFER`. Because the tokens are already burned/locked at init time and there is no cancellation path, the funds are permanently frozen.

### Finding Description

The NEAR bridge contract stores token decimal metadata as a `Decimals` struct with two fields: `origin_decimals` (the token's native precision on its origin chain) and `decimals` (the normalized precision used on NEAR). When a token has `origin_decimals > decimals`, the `normalize_amount` helper divides by `10^(origin_decimals - decimals)` using integer floor division:

```rust
// near/omni-bridge/src/lib.rs:2784-2787
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

This is called inside `sign_transfer` after subtracting the fee:

```rust
// near/omni-bridge/src/lib.rs:475-485
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

However, `init_transfer` only validates that `fee < amount`:

```rust
// near/omni-bridge/src/lib.rs:554-557
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

There is no check that `(amount - fee)` is large enough to survive normalization. If `amount - fee < 10^(origin_decimals - decimals)`, the normalized value is `0`, and `sign_transfer` panics permanently for that transfer.

By the time `sign_transfer` is called, `init_transfer_internal` has already burned or locked the user's tokens:

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
```

The transfer message remains in `pending_transfers` indefinitely. There is no `cancel_transfer` or refund mechanism for this state. `update_transfer_fee` can only increase the fee (making `amount_without_fee` smaller, worsening the situation). The transfer is irrecoverable.

### Impact Explanation

User funds are permanently frozen in the NEAR bridge contract. The tokens are burned (for deployed bridge tokens) or locked (for native tokens) at `init_transfer` time, and the only forward path — `sign_transfer` — always panics for this transfer. No recovery function exists. This matches the allowed impact: **Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

### Likelihood Explanation

This is realistic for any token pair where the NEAR-side representation has significantly more decimals than the destination chain representation. For example:

- A token with `origin_decimals = 24` (NEAR standard) and `decimals = 6` (e.g., USDC-like representation on EVM): `diff_decimals = 18`, divisor = `10^18`. Any transfer where `amount - fee < 10^18` (i.e., less than 1 full unit in destination precision) rounds to zero.
- A token with `origin_decimals = 18` and `decimals = 6`: divisor = `10^12`. Any `amount - fee < 10^12` rounds to zero.

An unprivileged user initiating a small transfer (or a transfer where the fee consumes most of the amount) can trigger this condition without any special access. The `init_transfer` entry point is fully public.

### Recommendation

Add a normalization check inside `init_transfer` (or `init_transfer_internal`) before locking/burning tokens. Specifically, after computing the fee-adjusted amount, verify that `normalize_amount(amount - fee, decimals) > 0`. If the check fails, revert immediately and return the tokens to the user rather than locking them.

```rust
// In init_transfer, after fee validation:
let decimals = self.token_decimals.get(&token_address)...;
let normalized = Self::normalize_amount(amount.0 - fee.0, decimals);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

This mirrors the fix pattern from the referenced Velocimeter report: validate the critical condition before committing any state change.

### Proof of Concept

1. Register a token with `Decimals { origin_decimals: 24, decimals: 6 }` in the bridge.
2. User calls `ft_transfer_call` with `amount = 5 * 10^17` (0.5 in NEAR-native units) and `fee = 0`.
3. `init_transfer` passes: `fee (0) < amount (5e17)` ✓. Tokens are burned/locked.
4. Relayer calls `sign_transfer` for this transfer.
5. `normalize_amount(5e17, {24, 6}) = 5e17 / 10^18 = 0`.
6. `require!(0 > 0, ...)` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. Transfer stays in `pending_transfers` forever; user's `5 * 10^17` tokens are permanently lost. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
