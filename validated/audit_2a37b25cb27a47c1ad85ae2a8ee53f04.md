### Title
Fee Validation Check on Raw Amount Before Decimal Normalization Can Permanently Lock User Funds - (File: `near/omni-bridge/src/lib.rs`)

### Summary

The `init_transfer` and `update_transfer_fee` functions validate `fee < amount` on raw (denormalized) NEAR token amounts. However, `sign_transfer` normalizes `amount - fee` using floor division before sending to the destination chain. When `amount - fee < 10^(origin_decimals - decimals)`, the normalized result is 0, causing `sign_transfer` to always revert. Because the fee can only be increased (never decreased) via `update_transfer_fee`, and no cancel/refund path exists, the user's locked tokens become permanently irrecoverable.

### Finding Description

**Root cause — check on raw value, used value is normalized:**

In `init_transfer` (line 554–557), the fee guard operates on the raw NEAR-unit amount:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [1](#0-0) 

In `update_transfer_fee` (line 399–402), the same raw-unit guard is applied when the sender raises the fee:

```rust
require!(
    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [2](#0-1) 

Later, in `sign_transfer` (lines 475–485), the amount actually sent to the destination chain is the **floor-divided** normalized value:

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

`normalize_amount` uses integer floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [4](#0-3) 

**No recovery path:**

`update_transfer_fee` enforces `fee.fee >= current_fee.fee`, so the fee can only be raised, never lowered. [2](#0-1)  There is no cancel or refund function for pending transfers; `remove_transfer_message` is only reachable through `claim_fee_callback` (requires a successful `sign_transfer` first) or `sign_transfer_callback` when `fee.is_zero()`. [5](#0-4) 

### Impact Explanation

A user who sets (or raises) the fee such that `amount - fee < 10^(origin_decimals - decimals)` will have their tokens permanently locked in the bridge:

- `init_transfer` / `update_transfer_fee` accept the fee because `fee < amount` (raw units).
- Every subsequent `sign_transfer` call reverts with `InvalidAmountToTransfer` because `normalize_amount(amount - fee) == 0`.
- The fee cannot be decreased; no cancel path exists.
- Tokens remain in `pending_transfers` indefinitely with no on-chain recovery.

This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows (High).**

### Likelihood Explanation

Likelihood is **low-to-medium**:

- Tokens with `origin_decimals > decimals` (e.g., a NEAR token with 24 decimals bridging to an EVM chain where it is represented with 18 decimals, giving a factor of 10^6) are a normal configuration.
- A user who sets `fee = amount - 1` when `amount` is small (e.g., `amount = 10^6 + 1`, `fee = 10^6`) will trigger the condition without any obvious warning.
- The UI or SDK layer may not surface the normalization factor, making accidental triggering plausible.
- The attacker-controlled entry path is the public `ft_transfer_call` → `init_transfer` flow, requiring no special role.

### Recommendation

Before storing the transfer message, validate that the **normalized** net amount is positive:

```rust
let normalized_net = Self::normalize_amount(
    transfer_message.amount.0.checked_sub(transfer_message.fee.fee.0)
        .near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized_net > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

Apply the same guard in `update_transfer_fee` after updating the fee field. This mirrors the Bond Protocol fix of rounding the expiry **before** the threshold check, ensuring the stored/used value is what is actually validated.

### Proof of Concept

Assume a NEAR-native token registered with `origin_decimals = 24`, `decimals = 18` (factor = 10^6).

1. User calls `ft_transfer_call` with `amount = 1_000_001` and `fee = 1_000_000`.
2. `init_transfer` checks `1_000_000 < 1_000_001` → passes. Transfer stored. [1](#0-0) 
3. Trusted relayer calls `sign_transfer`.
4. `amount_without_fee() = 1` → `normalize_amount(1, {origin=24, dest=18}) = 1 / 10^6 = 0`.
5. `require!(0 > 0, ...)` → panics with `InvalidAmountToTransfer`. [6](#0-5) 
6. User attempts `update_transfer_fee` to lower the fee — rejected because `fee >= current_fee.fee` is required. [2](#0-1) 
7. `1_000_001` tokens remain locked in `pending_transfers` with no recovery path.

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

**File:** near/omni-bridge/src/lib.rs (L649-667)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
