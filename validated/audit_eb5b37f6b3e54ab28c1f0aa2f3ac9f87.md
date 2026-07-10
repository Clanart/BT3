### Title
Permanent Fund Lock via Integer Division Truncation in `normalize_amount` When Transfer Amount Is Below Decimal Scaling Threshold — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a NEAR-origin transfer with an amount smaller than `10^(origin_decimals - decimals)`, the `normalize_amount` function truncates the entire transfer amount to zero via integer floor division. The user's tokens are already irrevocably locked in the bridge at this point. The subsequent `sign_transfer` call always reverts with `InvalidAmountToTransfer`, and no cancel or refund mechanism exists to recover the locked funds.

---

### Finding Description

`normalize_amount` performs integer floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

When `amount < 10^diff_decimals`, this evaluates to `0`. The protocol has a guard in `sign_transfer` that rejects zero-amount transfers:

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

However, this guard fires **after** the user's tokens have already been locked during `init_transfer_internal`:

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,
);
``` [3](#0-2) 

The `init_transfer` entry point only validates `fee < amount`, with no check that `normalize_amount(amount - fee, decimals) > 0`:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [4](#0-3) 

The transfer message is stored in `pending_transfers`. The only paths to remove it are:

1. `sign_transfer_callback` — only when `fee.is_zero()`, and only if MPC signing succeeds. Since `sign_transfer` panics before calling MPC, this callback never executes.
2. `claim_fee_callback` — requires a `ProverResult::FinTransfer` proof from the destination chain. Since `sign_transfer` never succeeds, no `FinTransfer` event is ever emitted on the destination chain, so no such proof can be generated. [5](#0-4) [6](#0-5) 

There is no DAO or admin function to forcibly remove a pending transfer and return tokens to the sender. The tokens are permanently locked.

---

### Impact Explanation

Any user who initiates a NEAR-origin transfer with `amount < 10^(origin_decimals - decimals)` will have their tokens permanently locked in the bridge with no recovery path. This is a **permanent, irrecoverable fund lock** matching the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

The SECURITY.md comment on `normalize_amount` acknowledges that "when fee = 0, dust stays locked/burned," but this refers to sub-unit remainders after normalization. The case described here is qualitatively different: the **entire transfer amount** normalizes to zero and is permanently locked, not merely a small remainder.

---

### Likelihood Explanation

This is reachable by any unprivileged bridge user via `ft_transfer_call`. The condition is triggered whenever:

- `origin_decimals > decimals` (normalization to lower precision — the standard case for cross-chain bridging)
- `amount - fee < 10^(origin_decimals - decimals)`

**Concrete example:** A token registered with `origin_decimals = 24` and `decimals = 18` (diff = 6, scaling factor = 1,000,000). A user transferring any amount from 1 to 999,999 units with `fee = 0` will have their entire balance permanently locked. This is realistic for tokens with large decimal differences (e.g., NEAR-native tokens bridging to EVM chains).

The user cannot self-rescue: `update_transfer_fee` only allows increasing the fee (`fee.fee >= current_fee.fee`), which makes `amount - fee` smaller, worsening the truncation. [7](#0-6) 

---

### Recommendation

Add a pre-lock validation in `init_transfer` (or in `ft_on_transfer` before accepting the tokens) that verifies the normalized net amount is non-zero:

```rust
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
);
if let Some(token_address) = token_address {
    if let Some(decimals) = self.token_decimals.get(&token_address) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

This mirrors the existing guard in `sign_transfer` but applies it before tokens are locked, preventing the irrecoverable state.

---

### Proof of Concept

**Setup:** Token registered with `origin_decimals = 24`, `decimals = 18` (diff = 6).

1. User calls `ft_transfer_call` with `amount = 500_000`, `fee = 0`, recipient on EVM.
2. `init_transfer` passes the `fee < amount` check (0 < 500,000). Tokens are locked. Transfer message stored in `pending_transfers`.
3. Relayer calls `sign_transfer`. `normalize_amount(500_000, {24, 18}) = 500_000 / 1_000_000 = 0`. The `require!(amount_to_transfer > 0)` check fires. Transaction panics. MPC is never called. Callback never runs.
4. Transfer message remains in `pending_transfers`. No `FinTransfer` proof can ever be generated. `claim_fee` cannot be called. No DAO recovery function exists.
5. The 500,000 units are permanently locked in the bridge. [1](#0-0) [2](#0-1) [3](#0-2) [4](#0-3)

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

**File:** near/omni-bridge/src/lib.rs (L1094-1094)
```rust
        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
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
