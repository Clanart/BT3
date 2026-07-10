### Title
Dust-Amount Transfer Permanently Locks User Tokens Due to Missing Normalization Pre-Check in `init_transfer` — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

When a user initiates a NEAR-origin bridge transfer with an amount below the decimal normalization threshold for the destination chain, tokens are irrecoverably locked in the bridge. `init_transfer_internal` locks the tokens and stores the transfer message, but the subsequent `sign_transfer` call panics with `ERR_INVALID_AMOUNT_TO_TRANSFER` because `normalize_amount` returns 0. No cancel or refund path exists for stuck pending transfers.

---

### Finding Description

`init_transfer` validates only that `fee < amount`: [1](#0-0) 

It does **not** validate that `amount_without_fee()` is above the normalization threshold `10^(origin_decimals − decimals)` for the destination chain.

`init_transfer_internal` then locks the tokens and stores the transfer message unconditionally: [2](#0-1) 

Later, when a trusted relayer calls `sign_transfer`, `normalize_amount` is applied: [3](#0-2) 

This is floor division. For a NEAR token (`origin_decimals = 24`) bridged to EVM (`decimals = 18`), the divisor is `10^6`. Any `amount_without_fee()` in the range `[1, 999_999]` produces `normalize_amount = 0`.

The guard that follows panics and reverts the `sign_transfer` transaction: [4](#0-3) 

Because `sign_transfer` is a separate transaction from `init_transfer`, the revert does **not** undo the token lock or the stored transfer message. The transfer message remains in `pending_transfers` indefinitely. There is no public cancel or refund function; `remove_transfer_message` is only reachable through the normal signing/fee-claim flow: [5](#0-4) 

The `sign_transfer_callback` only removes the message on a **successful** MPC signature when `fee.is_zero()` — a branch that is never reached because the panic occurs before the MPC call is dispatched.

---

### Impact Explanation

User tokens are permanently frozen in the bridge with no recovery path. This matches the allowed impact: **"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."**

The locked amount is the full transfer amount (not just dust), and neither the user, the relayer, nor any admin function can release it.

---

### Likelihood Explanation

Every token pair with a decimal difference between origin and destination is affected. NEAR tokens have 24 decimals; EVM bridge tokens are normalized to 18 decimals (6-decimal gap, threshold = 10^6 yoctoNEAR ≈ 0.000000000000000001 NEAR). Any user who sends fewer than 10^6 base units with `fee = 0` triggers the lock. The `init_transfer` fee check (`fee < amount`) passes for any positive amount, so there is no on-chain guard preventing this. The scenario is directly analogous to the 1-wei withdrawal in the external report.

---

### Recommendation

Add a pre-validation in `init_transfer` (before locking tokens) that checks `normalize_amount(amount_without_fee, decimals) > 0` for the destination chain. Alternatively, add a public `cancel_transfer` function that allows the original sender to reclaim locked tokens for a pending transfer that has not yet been signed.

---

### Proof of Concept

1. Token registered: NEAR token with `origin_decimals = 24`, EVM representation with `decimals = 18` (threshold = 10^6).
2. User calls `ft_transfer_call` with `amount = 999_999`, `fee = 0`, recipient = EVM address.
3. `init_transfer` passes: `0 < 999_999` ✓. Tokens locked, transfer message stored.
4. Relayer calls `sign_transfer` for the stored `transfer_id`.
5. `normalize_amount(999_999, {decimals:18, origin_decimals:24}) = 999_999 / 1_000_000 = 0`.
6. `require!(0 > 0, ERR_INVALID_AMOUNT_TO_TRANSFER)` — transaction panics and reverts.
7. Transfer message remains in `pending_transfers`; 999_999 tokens remain locked.
8. Every subsequent `sign_transfer` call for this `transfer_id` panics identically.
9. No cancel path exists; funds are permanently frozen.

### Citations

**File:** near/omni-bridge/src/lib.rs (L482-485)
```rust
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
