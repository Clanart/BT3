### Title
Inconsistent Fee/Amount Validation Between `init_transfer` and `sign_transfer` Causes Permanent Fund Lock - (File: `near/omni-bridge/src/lib.rs`)

### Summary

`init_transfer` validates the fee using raw NEAR-decimal arithmetic (`fee < amount`), while `sign_transfer` enforces a stricter condition using decimal-normalized arithmetic (`normalize_amount(amount - fee) > 0`). A transfer that passes the first check can permanently fail the second, locking user tokens with no recovery path.

### Finding Description

**`init_transfer` fee validation** (NEAR-decimal space): [1](#0-0) 

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

This check only requires `fee < amount` in raw NEAR-native token units. Tokens are immediately locked/burned at this point.

**`sign_transfer` normalization check** (destination-chain-decimal space): [2](#0-1) 

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

**`normalize_amount` uses floor division:** [3](#0-2) 

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

The condition `normalize_amount(amount - fee) > 0` requires `(amount - fee) >= 10^(origin_decimals - decimals)`. This is a strictly stronger requirement than `fee < amount`, and it is never checked at transfer initiation time.

**Concrete scenario** (NEAR token with `origin_decimals = 24`, EVM destination with `decimals = 18`, `diff_decimals = 6`):

- User calls `ft_transfer_call` → `init_transfer` with `amount = 500_000` (in yoctoNEAR-equivalent units) and `fee = 0`.
- Validation passes: `0 < 500_000`. Tokens are locked.
- Relayer calls `sign_transfer`. `normalize_amount(500_000) = 500_000 / 10^6 = 0`.
- `sign_transfer` panics: `ERR_INVALID_AMOUNT_TO_TRANSFER`.
- The transfer is permanently stuck: tokens are locked, `sign_transfer` always reverts, and there is no `cancel_transfer` or refund path.

The same scenario applies when a user calls `update_transfer_fee` to raise the fee close to the amount: [4](#0-3) 

```rust
require!(
    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

`update_transfer_fee` only checks `fee < amount` (same weak condition), so a sender can raise the fee until `amount - fee < 10^diff_decimals`, after which `sign_transfer` will always fail. Fees can only be increased, not decreased, so there is no self-recovery. [5](#0-4) 

### Impact Explanation

User tokens are permanently locked in the NEAR bridge contract with no recovery mechanism. The `claim_fee_callback` path (the only function that calls `remove_transfer_message`) requires a proof of finalization on the destination chain, which can never exist if `sign_transfer` always reverts. This is an irrecoverable fund lock matching the Critical/High allowed impact: **"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."**

### Likelihood Explanation

- Any unprivileged user initiating a NEAR → EVM (or NEAR → Solana) transfer with a net amount (after fee) below `10^diff_decimals` triggers this.
- For tokens with `origin_decimals = 24` and `decimals = 6` (`diff_decimals = 18`), any transfer where `amount - fee < 10^18` (i.e., less than 1 full destination-chain token unit) is permanently stuck. This is a realistic amount for small transfers.
- No special role or privileged access is required; any bridge user can trigger this accidentally.

### Recommendation

Add a normalization check at transfer initiation time in `init_transfer` (and in `update_transfer_fee`) to ensure the net amount after fee normalizes to a positive value on the destination chain:

```rust
// In init_transfer, after fee validation:
let token_address = self.get_token_address(destination_chain, token_id);
if let Some(decimals) = self.token_decimals.get(&token_address) {
    let normalized = Self::normalize_amount(
        transfer_message.amount_without_fee().unwrap_or(0),
        decimals,
    );
    require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
}
```

Apply the same guard in `update_transfer_fee` before accepting the new fee. This aligns the two validation paths so that any transfer accepted at initiation time is guaranteed to be signable.

### Proof of Concept

1. Register a NEAR token with `origin_decimals = 24`, mapped to an EVM token with `decimals = 18` (`diff_decimals = 6`).
2. Call `ft_transfer_call` with `amount = 999_999` (< `10^6`) and `fee = 0`. `init_transfer` succeeds; tokens are locked.
3. Call `sign_transfer` for the resulting `transfer_id`. `normalize_amount(999_999) = 0`. The call panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
4. Repeat step 3 indefinitely — it always fails. There is no cancel or refund entry point. Tokens are permanently locked. [6](#0-5) [2](#0-1) [1](#0-0)

### Citations

**File:** near/omni-bridge/src/lib.rs (L399-402)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );
```

**File:** near/omni-bridge/src/lib.rs (L404-409)
```rust
                require!(
                    fee.fee == current_fee.fee
                        || OmniAddress::Near(env::predecessor_account_id())
                            == transfer.message.sender,
                    BridgeError::SenderCanUpdateTokenFeeOnly.as_ref()
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

**File:** near/omni-bridge/src/lib.rs (L2776-2787)
```rust
    fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount * (10_u128.pow(diff_decimals))
    }

    /// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
    /// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
    /// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
