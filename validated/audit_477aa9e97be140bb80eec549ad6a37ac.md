### Title
Decimal Normalization to Zero in `sign_transfer` Permanently Freezes User Funds - (File: `near/omni-bridge/src/lib.rs`)

### Summary

When a user initiates a NEAR-side transfer of a token whose origin decimals exceed the destination chain's decimals, and the transferred amount (after fee) is smaller than the normalization divisor `10^(origin_decimals - decimals)`, the `normalize_amount` call in `sign_transfer` returns `0`. The subsequent `require!(amount_to_transfer > 0)` guard then permanently blocks every future `sign_transfer` attempt for that transfer. Because the tokens were already burned or locked during `init_transfer_internal` and no cancel/refund path exists, the user's funds are irrecoverably frozen.

### Finding Description

`sign_transfer` computes the on-wire amount by calling `normalize_amount` on `amount_without_fee()`:

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

`normalize_amount` performs integer floor division:

```rust
// near/omni-bridge/src/lib.rs:2784-2787
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

For a NEAR-native token with 24 origin decimals bridging to an EVM chain with 18 decimals, the divisor is `10^6`. Any `amount_without_fee` strictly less than `1_000_000` normalizes to `0`, causing the `require!` to panic on every call.

Meanwhile, `init_transfer_internal` burns or locks the full token amount **before** any normalization check:

```rust
// near/omni-bridge/src/lib.rs:1851-1857
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,
);
```

There is no `cancel_transfer` or user-callable refund function. `update_transfer_fee` can only **increase** the fee (enforced at line 400: `fee.fee >= current_fee.fee`), which shrinks `amount_without_fee` further rather than rescuing it. The transfer record sits in `pending_transfers` indefinitely while the underlying tokens are gone.

### Impact Explanation

The user's tokens are permanently frozen. For deployed (bridged) tokens they are burned with no corresponding mint on the destination chain. For native NEAR tokens they are locked in the bridge contract with no mechanism to unlock them. This matches the **Critical** impact class: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.*

### Likelihood Explanation

The condition is reachable by any unprivileged user who calls `ft_transfer_call` with a small amount. For the common NEAR→EVM path (24→18 decimals, divisor `10^6`), any transfer of fewer than `1_000_000` base units (i.e., less than `0.000001` of the token in human-readable units) triggers the freeze. Users bridging dust amounts, testing with small values, or using tokens with large decimal gaps are all realistic victims. No privileged access or external condition is required.

### Recommendation

Add a normalization check **before** burning or locking tokens in `init_transfer_internal`, and reject the transfer early (returning the full amount as a NEP-141 refund) if `normalize_amount(amount_without_fee, decimals) == 0`. Alternatively, enforce a minimum bridgeable amount at the `ft_on_transfer` entry point equal to `10^(origin_decimals - decimals)` for each registered token pair.

### Proof of Concept

1. Register a NEAR-native token with `origin_decimals = 24`, `decimals = 18` (divisor = `10^6`).
2. User calls `ft_transfer_call` with `amount = 500_000` (< `10^6`) and `fee = 0`.
3. `init_transfer_internal` burns `500_000` tokens and stores the pending transfer.
4. Relayer calls `sign_transfer` for that `transfer_id`.
5. `normalize_amount(500_000, {decimals:18, origin_decimals:24})` = `500_000 / 1_000_000` = `0`.
6. `require!(0 > 0, "ERR_INVALID_AMOUNT_TO_TRANSFER")` panics — every future `sign_transfer` call for this transfer also panics.
7. User's `500_000` tokens are permanently burned; no destination-chain mint ever occurs; no refund path exists.

---

**Root cause:** `init_transfer_internal` commits the token burn/lock unconditionally before any normalization guard, while `sign_transfer` enforces `amount_to_transfer > 0` only after the fact, with no recovery path. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

### Citations

**File:** near/omni-bridge/src/lib.rs (L398-402)
```rust
                let current_fee = transfer.message.fee;
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
