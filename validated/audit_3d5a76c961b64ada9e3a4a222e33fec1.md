### Title
Missing Decimal-Normalization Guard in `init_transfer` Allows Permanent Fund Lock — (`near/omni-bridge/src/lib.rs`)

### Summary

`init_transfer` enforces only `fee < amount` before locking user tokens. It does not verify that the net amount after decimal normalization (`normalize_amount(amount - fee)`) is greater than zero. When a user creates a transfer where `0 < amount - fee < 10^(origin_decimals - decimals)`, the tokens are locked but `sign_transfer` will always revert with `InvalidAmountToTransfer`, and there is no cancel/refund path. The funds are permanently frozen.

### Finding Description

**Root cause — `init_transfer` validation gap:**

In `near/omni-bridge/src/lib.rs`, the only fee/amount guard at transfer creation time is:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

This allows `amount - fee` to be any positive integer, including values smaller than `10^(origin_decimals - decimals)`.

**Root cause — `sign_transfer` normalization:**

Later, when a relayer calls `sign_transfer`, the net amount is normalized for the destination chain:

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

`normalize_amount` uses floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

If `origin_decimals > decimals` (e.g., origin chain stores 24 decimals, NEAR stores 18 → `diff_decimals = 6`), then any `amount - fee < 10^6` normalizes to `0`, causing `sign_transfer` to always revert.

**No recovery path:**

`remove_transfer_message` is only called inside `claim_fee_callback`, which is only reachable after a successful `sign_transfer` → `fin_transfer` cycle. There is no `cancel_transfer` or user-accessible refund function. The locked tokens are irrecoverable.

**The same gap exists in `update_transfer_fee`:**

```rust
require!(
    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

The sender can raise the token fee to `amount - 1`, making `amount - fee = 1`. If `diff_decimals > 0`, `normalize_amount(1) = 0`, and `sign_transfer` permanently fails on a previously-valid transfer.

### Impact Explanation

User tokens are locked in the NEAR bridge contract and can never be released. `sign_transfer` will revert on every call for that transfer ID, and no cancel/refund mechanism exists. This is an irrecoverable lock of user funds, matching the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

### Likelihood Explanation

Any user bridging a token whose origin chain has more decimals than its NEAR representation (a supported and documented configuration) can trigger this by setting `amount - fee` below the decimal-scaling threshold. The `update_transfer_fee` path makes it reachable even for transfers that were initially valid. No privileged role is required.

### Recommendation

Add a normalization check at `init_transfer` time (and in `update_transfer_fee`) before locking tokens:

```rust
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
).near_expect(BridgeError::FailedToGetTokenAddress);

let decimals = self.token_decimals
    .get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);

let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

Alternatively, add a `cancel_transfer` function that allows the original sender to reclaim locked tokens for transfers that have not yet been signed.

### Proof of Concept

Assume a token with `origin_decimals = 24`, `decimals = 18` (`diff_decimals = 6`).

1. User calls `ft_transfer_call` with `amount = 500_000` and `fee = 0`.
   - `init_transfer` check: `0 < 500_000` ✓ — transfer stored, tokens locked.
2. Relayer calls `sign_transfer`.
   - `amount_without_fee() = 500_000`
   - `normalize_amount(500_000, {decimals:18, origin_decimals:24}) = 500_000 / 10^6 = 0`
   - `require!(0 > 0)` → **panics with `InvalidAmountToTransfer`**
3. Every subsequent `sign_transfer` call reverts identically.
4. No cancel path exists → 500,000 tokens are permanently locked.

**Via `update_transfer_fee`** (larger amount, same outcome):

1. User creates transfer with `amount = 1_000_000_000`, `fee = 0`. Tokens locked.
2. User calls `update_transfer_fee` with `fee = 999_999_999` (passes `fee < amount`).
3. `amount_without_fee() = 1` → `normalize_amount(1) = 0` → `sign_transfer` always fails.
4. 1,000,000,000 tokens permanently locked. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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
