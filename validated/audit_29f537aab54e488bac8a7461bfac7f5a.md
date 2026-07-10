### Title
Dust Transfers Normalize to Zero, Permanently Locking User Funds in Bridge — (`near/omni-bridge/src/lib.rs`)

### Summary

When a user initiates a NEAR-side bridge transfer with an amount smaller than the decimal conversion factor (`10^(origin_decimals - decimals)`), the tokens are locked in the bridge but the transfer can never be signed or finalized. There is no cancellation path, so the funds are permanently frozen.

### Finding Description

The bridge uses `normalize_amount` (floor division) to convert a NEAR-native token amount to the destination chain's decimal representation before MPC signing: [1](#0-0) 

In `sign_transfer`, the normalized `amount_without_fee()` is checked to be greater than zero: [2](#0-1) 

If the user's `amount - fee` is less than `10^(origin_decimals - decimals)`, `normalize_amount` returns `0` and `sign_transfer` panics with `InvalidAmountToTransfer`. However, by this point the tokens were already locked (or burned for deployed tokens) during `init_transfer_internal`: [3](#0-2) 

The transfer message is stored in `pending_transfers` and the tokens are locked. There is no `cancel_transfer` or user-accessible refund path. The only code paths that remove a transfer message are:
- `claim_fee_callback` — requires a finalized destination-chain proof, which can never exist for this transfer
- `sign_transfer_callback` with `fee.is_zero()` — never reached because `sign_transfer` panics before calling the MPC signer [4](#0-3) 

The SECURITY.md comment at line 2781–2783 acknowledges dust being "locked/burned" only for the sub-unit *remainder* after normalization, not for the case where the *entire* net amount normalizes to zero.

### Impact Explanation

User funds are permanently frozen in the NEAR bridge contract. For tokens with a large decimal gap (e.g., `origin_decimals = 24`, `decimals = 18`, so `diff = 6`), any transfer of fewer than `1,000,000` base units (with zero fee) normalizes to zero and is irrecoverable. This matches the **Permanent freezing / irrecoverable lock of user funds** impact category.

### Likelihood Explanation

Any unprivileged user can trigger this by calling `ft_transfer_call` on a registered token with a small amount and a valid `InitTransferMsg`. No special role or key is required. The only prerequisite is that the token has `origin_decimals > decimals` (common for tokens bridged from chains with 24-decimal precision to 18-decimal EVM chains).

### Recommendation

Add a pre-check in `init_transfer` (before locking tokens) that verifies `normalize_amount(amount - fee, decimals) > 0`. Alternatively, implement a `cancel_transfer` function that allows the original sender to reclaim locked tokens for transfers that have not yet been signed.

### Proof of Concept

1. A token is registered with `origin_decimals = 24`, `decimals = 18` (diff = 6, factor = 1,000,000).
2. User calls `ft_transfer_call` with `amount = 500_000` and `fee = 0`, targeting an EVM recipient.
3. `init_transfer_internal` runs: 500,000 tokens are locked in the bridge, `TransferMessage` is stored. [5](#0-4) 
4. Trusted relayer calls `sign_transfer`. `normalize_amount(500_000, {24, 18}) = 500_000 / 1_000_000 = 0`. [2](#0-1) 
5. `sign_transfer` panics. The transfer message remains in `pending_transfers`. The 500,000 tokens remain locked with no recovery path. [6](#0-5)

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
