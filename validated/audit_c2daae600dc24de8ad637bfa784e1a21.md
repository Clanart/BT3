### Title
Missing Pre-Transfer Normalized-Amount Validation Causes Permanent Fund Lock - (`near/omni-bridge/src/lib.rs`)

### Summary

`init_transfer` burns or locks user tokens before verifying that the net transfer amount (`amount - fee`) is non-zero after decimal normalization to the destination chain. The normalization check only occurs later in `sign_transfer`, which will always revert with `ERR_INVALID_AMOUNT_TO_TRANSFER`. Because there is no cancel/refund path once tokens are burned or locked, the user's funds are permanently irrecoverable.

### Finding Description

The Omni Bridge normalizes amounts between chains with different decimal precisions using floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

For a NEAR-native token bridged to an EVM chain where `origin_decimals = 24` and `decimals = 6`, `diff_decimals = 18`, so any `amount - fee < 10^18` yoctoNEAR normalizes to exactly `0`.

`init_transfer` only validates `fee < amount`:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [2](#0-1) 

It does **not** check that `normalize_amount(amount - fee, decimals) > 0`. Immediately after this check, `init_transfer_internal` burns or locks the tokens and returns `U128(0)` (no refund to the NEP-141 caller):

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(...);
...
U128(0)
``` [3](#0-2) 

Later, when a relayer calls `sign_transfer`, the normalized amount is computed and the zero-amount guard fires:

```rust
let amount_to_transfer = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [4](#0-3) 

`sign_transfer` will revert on every call for this transfer. The transfer message remains in `pending_transfers` indefinitely, and there is no cancel or refund function in the contract. The burned tokens are gone; locked tokens are irrecoverable.

### Impact Explanation

**Critical / High — Permanent freezing of user funds.**

Any user who initiates a NEAR→EVM transfer where `amount - fee < 10^(origin_decimals - decimals)` will have their tokens permanently burned or locked with no recovery path. For a 24→6 decimal token pair the threshold is `10^18` yoctoNEAR (≈ 0.000001 NEAR), a realistic dust amount. For tokens with larger decimal gaps the threshold is higher and the risk is greater.

### Likelihood Explanation

Moderate. The condition is triggered by ordinary user behavior: sending a small or dust-level amount, or setting a fee that leaves a sub-unit remainder. No adversarial action is required. The protocol accepts the transfer, burns/locks the tokens, and silently blocks all future signing attempts.

### Recommendation

Add a normalized-amount check inside `init_transfer` **before** burning or locking tokens. At that point the destination token address and its `Decimals` record are already available via `token_id_to_address` and `token_decimals`. Reject the transfer early with a clear error if `normalize_amount(amount - fee, decimals) == 0`, so the NEP-141 `ft_on_transfer` callback can return the full amount and refund the user.

```rust
// Inside init_transfer, after resolving token_address and decimals:
let normalized = Self::normalize_amount(
    amount.0.checked_sub(init_transfer_msg.fee.0)
        .near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

### Proof of Concept

1. Token is registered with `origin_decimals = 24`, `decimals = 6` (e.g., NEAR → EVM USDC-like representation).
2. User calls `ft_transfer_call` with `amount = 5 * 10^17` yoctoNEAR and `fee = 0`.
3. `init_transfer` passes the `fee < amount` check (0 < 5×10^17 ✓).
4. `init_transfer_internal` burns `5 * 10^17` yoctoNEAR tokens and returns `U128(0)` — tokens are gone.
5. Relayer calls `sign_transfer`: `normalize_amount(5 * 10^17, {24, 6}) = 5 * 10^17 / 10^18 = 0`. Panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
6. Every subsequent `sign_transfer` call reverts identically. The transfer message stays in `pending_transfers` forever. User funds are permanently lost. [5](#0-4) [6](#0-5) [4](#0-3) [1](#0-0)

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
