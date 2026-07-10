### Title
Floor-Division in `normalize_amount` Permanently Locks User Funds When Transfer Amount Is Below Decimal Granularity — (`File: near/omni-bridge/src/lib.rs`)

### Summary

`normalize_amount` uses floor (integer) division to scale a NEAR-native token amount down to the destination chain's decimal precision. If a user initiates a transfer whose `amount - fee` is smaller than `10^(origin_decimals - decimals)`, the normalized result is `0`. `sign_transfer` then panics with `InvalidAmountToTransfer`, but the user's tokens were already locked or burned during `init_transfer_internal`. No cancellation path exists, so the funds are permanently irrecoverable.

### Finding Description

`normalize_amount` performs integer (floor) division: [1](#0-0) 

`sign_transfer` applies this to `amount_without_fee()` and then enforces a strict `> 0` guard: [2](#0-1) 

`amount_without_fee` is simply `amount - fee`: [3](#0-2) 

The transfer message is stored and tokens are locked/burned **before** `sign_transfer` is ever called, inside `init_transfer_internal`: [4](#0-3) 

The only guard at `init_transfer` time is `fee < amount`: [5](#0-4) 

There is no check that `amount - fee >= 10^diff_decimals`. A transfer with `amount = 5, fee = 0` targeting a chain with `diff_decimals = 6` (e.g., NEAR 24-decimal token → EVM 18-decimal token) passes the `init_transfer` guard, locks the 5 tokens, and then permanently fails every `sign_transfer` call.

The `sign_transfer_callback` only removes the pending transfer when `fee.is_zero()` **and** signing succeeds: [6](#0-5) 

Since `sign_transfer` panics before reaching MPC, the callback never fires. `claim_fee_callback` also cannot help because no destination-chain proof of finalization exists. There is no `cancel_transfer` function anywhere in the contract.

The code comment acknowledges floor division but only describes the "dust" (sub-unit remainder) case, not the case where the **entire** `amount - fee` normalizes to zero: [7](#0-6) 

### Impact Explanation

Any user who initiates a NEAR-origin transfer with `amount - fee < 10^(origin_decimals - decimals)` will have their tokens permanently locked in the bridge (for non-deployed tokens) or permanently burned (for deployed bridge tokens), with no recovery path. This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation

The condition is reachable by any unprivileged user calling `ft_transfer_call` with a small amount. For common decimal pairs:

- NEAR (24) → EVM (18): amounts below `1,000,000` base units are affected
- NEAR (24) → Solana (9): amounts below `10^15` base units are affected

No special role or privileged access is required. The `init_transfer` validation does not prevent it.

### Recommendation

Add a minimum-amount check inside `init_transfer` (before storing the transfer message and locking tokens) that verifies `normalize_amount(amount - fee, decimals) > 0`. This requires looking up the destination token's decimals at initiation time, or alternatively enforcing a protocol-level minimum transfer amount per destination chain.

### Proof of Concept

1. Token registered with `origin_decimals = 24`, `decimals = 18` (diff = 6).
2. User calls `ft_transfer_call` with `amount = 999_999` (< 10^6) and `fee = 0`.
3. `init_transfer` passes the `fee < amount` check; `init_transfer_internal` locks 999,999 tokens and stores the `TransferMessage` in `pending_transfers`.
4. Relayer calls `sign_transfer`:
   - `amount_without_fee() = 999_999`
   - `normalize_amount(999_999, Decimals { origin_decimals: 24, decimals: 18 }) = 999_999 / 1_000_000 = 0`
   - `require!(0 > 0, ...)` → panics with `InvalidAmountToTransfer`
5. No MPC signing occurs; `sign_transfer_callback` never fires; the `TransferMessage` remains in `pending_transfers` indefinitely.
6. `claim_fee` cannot be called (no destination-chain finalization proof exists).
7. The 999,999 tokens are permanently locked/burned with no recovery mechanism.

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

**File:** near/omni-bridge/src/lib.rs (L655-658)
```rust
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }
```

**File:** near/omni-bridge/src/lib.rs (L1829-1865)
```rust
    fn init_transfer_internal(
        &mut self,
        transfer_message: TransferMessage,
        storage_owner: AccountId,
    ) -> U128 {
        let required_storage_balance = self
            .add_transfer_message(transfer_message.clone(), storage_owner.clone())
            .saturating_add(NearToken::from_yoctonear(transfer_message.fee.native_fee.0));

        if self
            .try_update_storage_balance(
                storage_owner,
                required_storage_balance,
                NearToken::from_yoctonear(0),
            )
            .is_err()
        {
            self.remove_transfer_message_without_refund(transfer_message.get_transfer_id());
            return transfer_message.amount;
        }

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
    }
```

**File:** near/omni-bridge/src/lib.rs (L2781-2787)
```rust
    /// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
    /// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
    /// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-types/src/lib.rs (L593-595)
```rust
    pub fn amount_without_fee(&self) -> Option<u128> {
        self.amount.0.checked_sub(self.fee.fee.0)
    }
```
