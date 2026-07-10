### Title
Permanent Fund Lock via Zero-Normalized Amount in `init_transfer_internal` — (`near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a NEAR-side bridge transfer with an amount that is smaller than the decimal-normalization divisor, `init_transfer_internal` irreversibly burns or locks the user's tokens, but the transfer can never be signed or completed because `sign_transfer` later rejects the zero-normalized amount. The tokens are permanently frozen with no recovery path.

---

### Finding Description

The NEAR bridge uses `normalize_amount` to convert a NEAR-native token amount into the destination chain's decimal precision before MPC signing: [1](#0-0) 

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

This is floor division. For a token with `origin_decimals = 24` and `decimals = 18`, the divisor is `10^6`. Any amount `< 1_000_000` normalizes to `0`.

The zero-amount guard exists only in `sign_transfer`, **after** the tokens have already been consumed: [2](#0-1) 

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

But `init_transfer_internal`, which runs **before** `sign_transfer`, already burns or locks the tokens unconditionally: [3](#0-2) 

```rust
if let OmniAddress::Near(token_id) = transfer_message.token.clone() {
    self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
    self.lock_tokens_if_needed(
        transfer_message.get_destination_chain(),
        &token_id,
        transfer_message.amount.0,
    );
```

The `init_transfer` entry-point only validates `fee < amount`: [4](#0-3) 

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

There is no check that `normalize_amount(amount - fee, decimals) > 0` before the irreversible burn/lock step. Once `init_transfer_internal` returns `U128(0)` (the NEP-141 "tokens consumed" signal), the tokens are gone. Every subsequent call to `sign_transfer` for that `transfer_id` will panic with `InvalidAmountToTransfer`, and no cancel/refund function exists for pending transfers in this state.

---

### Impact Explanation

User tokens are permanently burned (for deployed/bridge tokens) or permanently locked in the bridge contract (for native tokens), with no mechanism to recover them. The transfer record sits in `pending_transfers` indefinitely but can never be finalized or cancelled. This matches the **Critical** impact class: *Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds*.

---

### Likelihood Explanation

Any token registered with a decimal difference between its origin chain and NEAR (e.g., `origin_decimals = 24`, `decimals = 18`, divisor = `10^6`) is affected. A user who sends an amount below the divisor — whether by mistake or due to UI rounding — triggers the lock. This is a realistic user error for tokens with large decimal gaps, and no input validation prevents it at the `ft_on_transfer` / `init_transfer` boundary.

---

### Recommendation

Add a zero-normalized-amount check inside `init_transfer` (or at the top of `init_transfer_internal`) **before** tokens are burned or locked. The check must use the actual destination-chain decimals for the token:

```rust
// In init_transfer, after building transfer_message and before calling init_transfer_internal:
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    token_id.clone(),
);
if let Some(addr) = token_address {
    if let Some(decimals) = self.token_decimals.get(&addr) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee()
                .expect("fee < amount already checked"),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

Alternatively, mirror the pattern already present in `sign_transfer` (lines 475–485) at the `init_transfer` stage so the revert happens before any state mutation.

---

### Proof of Concept

1. A token `T` is registered with `origin_decimals = 24`, `decimals = 18` (divisor = `10^6`).
2. Alice calls `ft_transfer_call` on token `T` with `amount = 500_000` and a valid `InitTransferMsg` (fee = 0, recipient on EVM).
3. `init_transfer` passes the `fee < amount` check (0 < 500_000). [4](#0-3) 
4. `init_transfer_internal` is called. It stores the transfer message, then calls `burn_tokens_if_needed` (burns 500_000 of T) and `lock_tokens_if_needed`. Returns `U128(0)` — NEP-141 consumes Alice's tokens. [5](#0-4) 
5. A relayer calls `sign_transfer` for Alice's `transfer_id`. `normalize_amount(500_000, {24,18})` = `500_000 / 1_000_000` = `0`. The `require!(amount_to_transfer > 0, ...)` panics. [2](#0-1) 
6. Every future `sign_transfer` call for this transfer ID also panics. Alice's 500_000 T tokens are permanently burned. The transfer record is stuck in `pending_transfers` forever.

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
