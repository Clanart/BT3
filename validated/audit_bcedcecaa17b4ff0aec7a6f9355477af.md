### Title
Missing Pre-Validation of Normalized `amount_without_fee` in `init_transfer` Causes Permanent Fund Lock — (`near/omni-bridge/src/lib.rs`)

### Summary
`init_transfer` burns/locks user tokens before validating that the normalized `amount_without_fee` is non-zero. `sign_transfer` performs this check later, but by then there is no recovery path. Any user who sets a fee such that `amount - fee < 10^(origin_decimals - decimals)` will have their tokens permanently locked on NEAR with no mechanism to recover them.

### Finding Description
`init_transfer` validates only that `fee.fee < amount`: [1](#0-0) 

It then immediately burns or locks the full `amount` in `init_transfer_internal`: [2](#0-1) 

Later, when a relayer calls `sign_transfer`, the bridge normalizes `amount_without_fee()` to the destination chain's decimal precision using floor division: [3](#0-2) 

`sign_transfer` then enforces that the normalized result is non-zero: [4](#0-3) 

If `amount - fee < 10^(origin_decimals - decimals)`, `normalize_amount` returns `0` and `sign_transfer` panics every time it is called. The transfer message remains in `pending_transfers` indefinitely. There is no public `cancel_transfer` function, and `claim_fee_callback` requires a proof from the destination chain that the transfer was finalized — which can never happen because the transfer was never signed.

The code comment for `normalize_amount` acknowledges dust locking only for the case where `fee = 0` and a small remainder exists: [5](#0-4) 

It does not address the case where the entire `amount_without_fee` rounds to zero, which is a distinct and unrecovered scenario.

### Impact Explanation
User tokens are permanently and irrecoverably locked/burned on NEAR. The transfer can never be signed, finalized, or cancelled. This matches the allowed impact: **Permanent freezing, irrecoverable lock of user funds in bridge flows.**

### Likelihood Explanation
Any user bridging a token with decimal normalization (e.g., a 24-decimal NEAR token to an 18-decimal EVM destination, where `diff_decimals = 6`) who sets `fee` such that `amount - fee < 1_000_000` triggers this. This can occur via:
- A buggy or malicious frontend that pre-fills a high fee
- A user manually maximizing relayer incentive
- A user who misunderstands the fee field semantics

The `update_transfer_fee` function only allows fees to be **increased**, not decreased, so there is no self-correction path after the fact: [6](#0-5) 

### Recommendation
Add a validation in `init_transfer` (before burning/locking tokens) that `normalize_amount(amount_without_fee, decimals) > 0`. The token address and decimals are available at `init_transfer` time via `token_decimals`. Alternatively, add a `cancel_transfer` function that allows the original sender to reclaim locked tokens when the transfer has not yet been signed.

### Proof of Concept
Consider a NEAR token with `origin_decimals = 24`, bridged to an EVM chain where `decimals = 18` (`diff_decimals = 6`, divisor = `1_000_000`).

1. User calls `ft_transfer_call` with `amount = 2_000_000` and `fee = 1_500_001`.
2. `init_transfer` checks `fee (1_500_001) < amount (2_000_000)` → passes.
3. `init_transfer_internal` burns/locks `2_000_000` tokens on NEAR.
4. Relayer calls `sign_transfer`.
5. `amount_without_fee() = 2_000_000 - 1_500_001 = 499_999`.
6. `normalize_amount(499_999, {origin: 24, decimals: 18}) = 499_999 / 1_000_000 = 0`.
7. `require!(0 > 0)` → panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
8. Step 4–7 repeats forever. The `2_000_000` tokens are permanently locked. `claim_fee` cannot be called because no destination-chain proof exists. No cancel function exists. [7](#0-6) [8](#0-7)

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
