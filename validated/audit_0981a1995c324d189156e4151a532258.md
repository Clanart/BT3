### Title
Dust-Amount Transfer Permanently Locks User Funds via `normalize_amount` Zero-Truncation in `sign_transfer` - (File: near/omni-bridge/src/lib.rs)

### Summary

When a user initiates a NEAR-origin transfer with an `amount_without_fee` smaller than the decimal-normalization divisor (`10^(origin_decimals - decimals)`), `normalize_amount` returns 0 via floor division. The subsequent `require!(amount_to_transfer > 0, ...)` guard in `sign_transfer` then panics on every call, permanently blocking the transfer. Because the user's tokens are already locked or burned inside `init_transfer_internal` before `sign_transfer` is ever invoked, and no cancel/refund path exists, the funds are irrecoverably frozen.

### Finding Description

`normalize_amount` performs integer floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

For any token where `origin_decimals > decimals` (e.g., a token with 24 origin decimals normalized to 18 on NEAR, giving a divisor of `10^6 = 1,000,000`), any `amount_without_fee < 1,000,000` normalizes to exactly 0.

`sign_transfer` then enforces:

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

This `require!` panics unconditionally for the affected transfer. Because `sign_transfer` is the **only** path to advance the transfer to the destination chain, the transfer is permanently stuck.

The tokens are already gone from the user's account before `sign_transfer` is ever called. `init_transfer_internal` burns bridge tokens or locks native tokens immediately upon `ft_transfer_call`:

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(transfer_message.get_destination_chain(), &token_id, transfer_message.amount.0);
``` [3](#0-2) 

`init_transfer` only validates `fee.fee < amount`, with no minimum-amount guard against the normalization divisor:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [4](#0-3) 

`update_transfer_fee` cannot rescue the transfer because it only allows the fee to be **increased** (`fee.fee >= current_fee.fee`), which shrinks `amount_without_fee` further: [5](#0-4) 

There is no `cancel_transfer` or admin-recovery function for this state.

### Impact Explanation

User tokens are permanently frozen (native tokens remain locked in the bridge's `locked_tokens` accounting) or permanently burned (bridge-deployed tokens). The `pending_transfers` entry for the transfer ID is never removed. This matches the allowed impact: **permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation

Any token registered with `origin_decimals > decimals` is affected. This is the normal configuration for tokens bridged from high-precision chains (e.g., NEAR native tokens have 24 decimals; EVM tokens are typically normalized to 18, giving a 6-decimal gap and a divisor of `10^6`). A user sending any amount below `10^(origin_decimals - decimals)` in the token's smallest unit triggers the freeze. No special privilege is required — any unprivileged token holder calling `ft_transfer_call` with a small amount is sufficient.

### Recommendation

Add a minimum-amount check in `init_transfer` (or `init_transfer_internal`) that validates `normalize_amount(amount_without_fee) > 0` **before** locking or burning tokens:

```rust
let decimals = self.token_decimals.get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);
require!(
    Self::normalize_amount(
        transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
        decimals,
    ) > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

This mirrors the existing guard in `sign_transfer` but places it at the entry point, before any irreversible state change.

### Proof of Concept

1. Register a token with `origin_decimals = 24`, `decimals = 18` (divisor = `10^6`).
2. User calls `ft_transfer_call` with `amount = 500_000`, `fee = 0`, targeting an EVM recipient.
3. `init_transfer` passes (`fee(0) < amount(500_000)`). Tokens are burned/locked. Transfer message stored.
4. Relayer calls `sign_transfer` for the transfer ID.
5. `normalize_amount(500_000, {decimals:18, origin_decimals:24}) = 500_000 / 1_000_000 = 0`.
6. `require!(0 > 0, ...)` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. Every subsequent `sign_transfer` call for this transfer ID panics identically.
8. User's 500,000 token units are permanently lost with no recovery path. [6](#0-5) [7](#0-6)

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

**File:** near/omni-bridge/src/lib.rs (L523-557)
```rust
    fn init_transfer(
        &mut self,
        sender_id: AccountId,
        signer_id: AccountId,
        token_id: AccountId,
        amount: U128,
        init_transfer_msg: InitTransferMsg,
    ) -> PromiseOrPromiseIndexOrValue<U128> {
        require!(
            init_transfer_msg.recipient.get_chain() != ChainKind::Near,
            BridgeError::InvalidRecipientChain.as_ref()
        );

        self.current_origin_nonce += 1;
        let destination_nonce =
            self.get_next_destination_nonce(init_transfer_msg.get_destination_chain());

        let transfer_message = TransferMessage {
            origin_nonce: self.current_origin_nonce,
            token: OmniAddress::Near(token_id),
            amount,
            recipient: init_transfer_msg.recipient,
            fee: Fee {
                fee: init_transfer_msg.fee,
                native_fee: init_transfer_msg.native_token_fee,
            },
            sender: OmniAddress::Near(sender_id),
            msg: init_transfer_msg.msg.map(String::from).unwrap_or_default(),
            destination_nonce,
            origin_transfer_id: None,
        };
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

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
