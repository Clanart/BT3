### Title
Tokens Permanently Locked When Bridged Amount Normalizes to Zero Due to Decimal Scaling — (File: near/omni-bridge/src/lib.rs)

---

### Summary

When a user initiates a NEAR-side transfer to a destination chain where the token has fewer decimals (`origin_decimals > decimals`), the bridge locks/burns the user's tokens in `init_transfer_internal` **before** any check that the normalized destination amount is non-zero. The zero-amount guard only fires later in `sign_transfer`, which is called by a trusted relayer. If the user's amount is smaller than the minimum denomination of the destination chain, `sign_transfer` always panics, the transfer message is never removed, and the locked tokens have no recovery path.

---

### Finding Description

The Omni Bridge NEAR contract stores per-token decimal metadata in `token_decimals` as a `Decimals { decimals, origin_decimals }` struct. When bridging from NEAR to a destination chain that represents the token with fewer decimals (e.g., a NEAR token with 24 decimals bridged to EVM where it is represented with 18 decimals), the bridge must scale the amount down via `normalize_amount`:

```rust
// near/omni-bridge/src/lib.rs:2784-2787
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

For a token with `origin_decimals=24, decimals=18`, the divisor is `10^6`. Any amount below `10^6` normalizes to `0` via floor division.

**Step 1 — Tokens are locked/burned unconditionally in `init_transfer_internal`:**

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

No check is performed here against the minimum denomination of the destination chain. The full `transfer_message.amount` is burned/locked immediately.

**Step 2 — The zero-amount guard fires too late in `sign_transfer`:**

```rust
// near/omni-bridge/src/lib.rs:475-485
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

This check is correct in intent but executes **after** the tokens are already locked. When it panics, the `TransferMessage` remains in `pending_transfers` indefinitely.

**Step 3 — No recovery path exists:**

`remove_transfer_message` is only called in:
- `sign_transfer_callback` — only reached on a successful MPC signature (never reached if `sign_transfer` panics)
- `claim_fee_callback` — requires a `FinTransfer` proof from the destination chain (impossible since no signature was ever produced)

There is no `cancel_transfer` or user-initiated refund function. The locked tokens are irrecoverable.

The code comment on `normalize_amount` acknowledges dust locking but only for the remainder case:

```rust
// near/omni-bridge/src/lib.rs:2781-2783
/// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
/// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
/// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
```

This comment addresses rounding dust (e.g., 1 unit out of 1,000,001). It does not address the case where the **entire** `amount_without_fee` normalizes to zero, which is a qualitatively different and more severe outcome.

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

Any user who initiates a transfer with an amount smaller than `10^(origin_decimals - decimals)` will have their tokens permanently locked in the NEAR bridge contract with no recovery mechanism. The `sign_transfer` guard prevents the transfer from completing but does not refund the already-locked tokens. This matches: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** The condition requires a token registered with `origin_decimals > decimals` (e.g., a NEAR-native token with 24 decimals bridged to EVM where it is deployed with 18 decimals — a 10^6 multiplier). A user sending fewer than 10^6 base units (a small but non-negligible amount for low-value tokens or tokens with high decimal precision) triggers the bug. The entry point is the public `ft_transfer_call` → `ft_on_transfer` flow, callable by any token holder without privilege.

---

### Recommendation

Move the minimum-denomination check into `init_transfer` (or `init_transfer_internal`) **before** burning or locking tokens. The `token_decimals` mapping is already available at that point, so the check is straightforward:

```rust
// In init_transfer_internal, before burn_tokens_if_needed:
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    &token_id,
);
if let Some(decimals) = token_address.and_then(|a| self.token_decimals.get(&a)) {
    let normalized = Self::normalize_amount(
        transfer_message.amount_without_fee().unwrap_or(0),
        decimals,
    );
    require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
}
```

This mirrors the fix described in the original report: the source chain (NEAR) already knows the destination chain's decimal configuration and should enforce the minimum denomination before accepting the transfer.

---

### Proof of Concept

**Setup**: Token `token.near` is registered with `origin_decimals=24, decimals=18` for the EVM destination chain (multiplier = 10^6).

1. User calls `ft_transfer_call` on `token.near` with `amount = 500_000` (less than 10^6), specifying an EVM recipient.
2. `ft_on_transfer` → `init_transfer` → `init_transfer_internal` executes.
3. `burn_tokens_if_needed(token_id, U128(500_000))` burns the user's 500,000 tokens. [1](#0-0) 
4. `lock_tokens_if_needed(EVM, token_id, 500_000)` records 500,000 as locked. [2](#0-1) 
5. Transfer message is stored in `pending_transfers`. Function returns `U128(0)`.
6. Trusted relayer calls `sign_transfer(transfer_id, ...)`.
7. `normalize_amount(500_000, Decimals{decimals:18, origin_decimals:24})` = `500_000 / 1_000_000` = `0`. [3](#0-2) 
8. `require!(0 > 0, ...)` panics with `InvalidAmountToTransfer`. [4](#0-3) 
9. Transfer message remains in `pending_transfers`. No signature is produced. No `fin_transfer` can ever occur. The 500,000 tokens are permanently locked.
10. `claim_fee_callback` cannot be invoked (requires a `FinTransfer` proof that will never exist). `sign_transfer_callback` is never reached. No other removal path exists. [5](#0-4)

### Citations

**File:** near/omni-bridge/src/lib.rs (L482-485)
```rust
        require!(
            amount_to_transfer > 0,
            BridgeError::InvalidAmountToTransfer.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L648-668)
```rust
    #[private]
    pub fn sign_transfer_callback(
        &mut self,
        #[callback_result] call_result: Result<SignatureResponse, PromiseError>,
        #[serializer(borsh)] message_payload: TransferMessagePayload,
        #[serializer(borsh)] fee: &Fee,
    ) {
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }

            env::log_str(
                &OmniBridgeEvent::SignTransferEvent {
                    signature,
                    message_payload,
                }
                .to_log_string(),
            );
        }
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
