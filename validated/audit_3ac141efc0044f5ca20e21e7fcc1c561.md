### Title
Missing Minimum-Amount Validation at `init_transfer` Causes Permanent Lock of User Funds When Decimal Normalization Rounds to Zero - (File: near/omni-bridge/src/lib.rs)

### Summary
`init_transfer` in the NEAR bridge contract only validates `fee < amount` before locking/burning user tokens. It does not validate that the net amount (`amount - fee`) survives decimal normalization to a non-zero value. The normalization check (`amount_to_transfer > 0`) only occurs later in `sign_transfer`, by which time the tokens are already irrecoverably locked or burned. Any user who initiates a transfer with a net amount below the decimal-normalization threshold will have their tokens permanently frozen with no recovery path.

### Finding Description

`init_transfer` stores the transfer message and immediately locks or burns the user's tokens via `init_transfer_internal`: [1](#0-0) 

The only pre-lock validation on the amount is:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [1](#0-0) 

Tokens are then locked or burned inside `init_transfer_internal` before any normalization check: [2](#0-1) 

The normalization check only happens later, inside `sign_transfer`:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [3](#0-2) 

`normalize_amount` uses floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [4](#0-3) 

For a token registered with `origin_decimals = 24` and `decimals = 18` (a 6-decimal difference, multiplier = 1,000,000), any net transfer amount below 1,000,000 normalizes to 0. `sign_transfer` will always panic with `ERR_INVALID_AMOUNT_TO_TRANSFER` for such a transfer, and there is no cancel or refund mechanism visible in the contract.

### Impact Explanation

**Critical — Permanent freezing / irrecoverable lock of user funds.**

Once `init_transfer_internal` returns `U128(0)` (success), the NEP-141 `ft_transfer_call` callback keeps the tokens in the bridge. The transfer message is stored on-chain. Every subsequent call to `sign_transfer` for that `transfer_id` will panic. Because there is no `cancel_transfer` or user-accessible refund path, the tokens are permanently locked (native tokens) or permanently burned (bridged tokens) with no recovery.

### Likelihood Explanation

**Medium.** The condition is triggered whenever `amount - fee < 10^(origin_decimals - decimals)`. For NEAR-native tokens bridged to EVM (24 → 18 decimals, threshold = 1,000,000 yoctoNEAR ≈ 0.000000000000000001 NEAR), the threshold is negligible. However, for tokens with smaller decimal differences (e.g., 8 → 6, threshold = 100 base units), a user sending a small but non-trivial amount can trigger this. The user-facing `ft_transfer_call` entry point is fully unprivileged and requires no special role.

### Recommendation

Add a normalization check inside `init_transfer`, before tokens are locked or burned, to ensure the net amount survives decimal conversion:

```rust
// After resolving decimals for the destination token:
let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

Alternatively, enforce a protocol-level minimum transfer amount per token that accounts for the decimal difference, rejecting sub-threshold transfers at `init_transfer` time before any state mutation.

### Proof of Concept

1. Register a token with `origin_decimals = 24`, `decimals = 18` (6-decimal gap, multiplier = 1,000,000).
2. User calls `ft_transfer_call` on the token contract with `amount = 999_999` and `fee = 0`, targeting the bridge with an EVM recipient.
3. Bridge's `ft_on_transfer` → `init_transfer` passes the `fee < amount` check (0 < 999,999 ✓).
4. `init_transfer_internal` locks 999,999 units and stores the transfer message; returns `U128(0)` → tokens are kept by the bridge.
5. Relayer calls `sign_transfer` for the stored `transfer_id`.
6. `normalize_amount(999_999, {24, 18}) = 999_999 / 1_000_000 = 0` (floor division).
7. `require!(0 > 0, ...)` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
8. No cancel or refund path exists; the user's 999,999 token units are permanently locked in the bridge with no way to recover them. [3](#0-2) [4](#0-3) [1](#0-0) [5](#0-4)

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

**File:** near/omni-bridge/src/lib.rs (L1850-1864)
```rust
        if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
        } else {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

        env::log_str(&OmniBridgeEvent::InitTransferEvent { transfer_message }.to_log_string());
        U128(0)
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
