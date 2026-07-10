### Title
`sign_transfer` Permanently Locks Funds When `normalize_amount` Rounds Net Transfer Amount to Zero - (File: near/omni-bridge/src/lib.rs)

### Summary

`sign_transfer` enforces `amount_to_transfer > 0` after applying floor-division decimal normalization to `amount_without_fee()`. When a user initiates a NEAR-side transfer whose net amount (amount minus fee) is smaller than the normalization divisor `10^(origin_decimals - decimals)`, `normalize_amount` returns 0 and `sign_transfer` always reverts. Because there is no cancel or refund path for pending transfers, the locked tokens are irrecoverable.

### Finding Description

In `near/omni-bridge/src/lib.rs`, `sign_transfer` computes the destination-chain amount as:

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

`normalize_amount` performs integer floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

For a token registered with `origin_decimals = 24` and `decimals = 18` (a common pairing for NEAR-native tokens bridged to EVM), the divisor is `10^6`. Any `amount_without_fee()` value below `1_000_000` normalizes to `0`, causing `sign_transfer` to unconditionally revert.

`init_transfer` only validates `fee.fee < amount`; it does **not** check that `normalize_amount(amount - fee) > 0`:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

So a user can successfully lock tokens whose net amount is below the normalization threshold. Once locked, the transfer message is stored in `pending_transfers` and the tokens are held by the bridge. The only exit paths are:

- `sign_transfer` → always reverts (the bug).
- `claim_fee` → requires a `FinTransfer` proof from the destination chain, which can never exist because the transfer was never signed.
- `update_transfer_fee` → fees can only be increased (`fee.fee >= current_fee.fee`), which makes `amount_without_fee` smaller, worsening the situation.
- No `cancel_transfer` or refund function exists.

The result is a permanent deadlock: the transfer can neither be completed nor cancelled.

### Impact Explanation

Tokens locked by `init_transfer` for a sub-threshold amount are permanently frozen in the bridge contract. There is no recovery path. This matches the allowed impact: **Critical — Permanent freezing, irrecoverable lock of user funds in bridge flows.**

### Likelihood Explanation

Any user who calls `ft_transfer_call` with an amount below `10^(origin_decimals - decimals)` triggers the deadlock. For NEAR-native tokens (24 decimals) bridged to EVM (18 decimals), the threshold is `10^6` base units (0.000001 NEAR). A user testing with a small amount, or a user who updates their fee close to the transfer amount, can easily fall into this condition. No privileged access is required; the entry point is the standard public `ft_transfer_call` interface.

### Recommendation

Add a normalization guard in `init_transfer` (or in `init_transfer_internal`) before storing the transfer message:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee()
        .near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

Apply the same guard in `update_transfer_fee` to prevent a valid transfer from being updated into an un-signable state.

### Proof of Concept

1. A token is registered with `origin_decimals = 24`, `decimals = 18` (divisor = `10^6`).
2. User calls `ft_transfer_call` with `amount = 500_000` and `fee = 0`.
3. `init_transfer` passes: `fee.fee (0) < amount (500_000)`. Transfer message is stored; 500,000 units are locked.
4. Relayer calls `sign_transfer` for this transfer.
5. `normalize_amount(500_000, decimals)` = `500_000 / 1_000_000` = `0` (floor division).
6. `require!(amount_to_transfer > 0, ...)` reverts with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. No other exit path exists. The 500,000 units are permanently locked.

**Relevant code locations:** [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
