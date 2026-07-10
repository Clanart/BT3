### Title
Sub-Decimal Amount Permanently Locks User Tokens When `normalize_amount` Returns Zero in `sign_transfer` — (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

`init_transfer` accepts any amount satisfying `fee < amount` without verifying that the amount survives decimal normalization. When a user bridges a NEAR-native token whose origin decimals exceed the destination chain's decimals (e.g., 24 → 18), any amount smaller than `10^(origin_decimals − dest_decimals)` normalizes to zero via floor division. The transfer message is stored and tokens are burned/locked in `init_transfer_internal`, but every subsequent call to `sign_transfer` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`. No cancel or refund path exists for pending transfers, so the user's tokens are permanently irrecoverable.

---

### Finding Description

**Step 1 — `init_transfer` stores the message and burns/locks tokens without a normalized-amount check.**

`init_transfer` validates only that `fee < amount`: [1](#0-0) 

It then calls `init_transfer_internal`, which burns or locks the full raw amount and inserts the transfer message into `pending_transfers`: [2](#0-1) 

**Step 2 — `normalize_amount` uses floor division and can return 0.** [3](#0-2) 

For a token registered with `origin_decimals = 24` and `decimals = 18` (diff = 6), any raw amount below `1_000_000` divides to zero.

**Step 3 — `sign_transfer` panics on zero, but does NOT remove the stored transfer.** [4](#0-3) 

The panic unwinds the call without touching `pending_transfers`. The transfer message remains stored permanently.

**Step 4 — No public cancel or refund path exists.**

`remove_transfer_message` is only called inside `sign_transfer_callback` (on successful MPC signing) and `claim_fee_callback`. Neither is reachable because `sign_transfer` never reaches the MPC call. There is no admin or user-facing function to cancel a pending transfer and recover the locked/burned tokens.

---

### Impact Explanation

- **For deployed (bridged) tokens**: `burn_tokens_if_needed` destroys the tokens at `init_transfer_internal` time. They are gone.
- **For native NEAR tokens**: `lock_tokens_if_needed` increments the locked-token counter. The tokens sit in the bridge contract forever with no unlock path.

In both cases the user suffers a **permanent, irrecoverable loss** of their bridged assets. This matches the Critical impact class: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

The condition is reachable by any ordinary bridge user:

1. The token must have more decimals on NEAR than on the destination chain. The canonical example is a NEAR-native token with 24 decimals bridged to an EVM chain where the registered token has 18 decimals (`diff_decimals = 6`).
2. The user sends an amount below `10^6` raw units (e.g., `amount = 1` through `999_999`). This is a plausible small-value transfer.
3. Fee can be zero (satisfies `fee < amount`).

No privileged access, no key compromise, and no colluding parties are required. The user triggers the lock entirely through the normal `ft_transfer_call` → `ft_on_transfer` public entry point.

---

### Recommendation

Add a normalized-amount check inside `init_transfer` **before** tokens are burned or locked. Reject the transfer early if the normalized net amount would be zero:

```rust
// In init_transfer, after building transfer_message and before init_transfer_internal:
if let Some(token_address) = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
) {
    if let Some(decimals) = self.token_decimals.get(&token_address) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

This mirrors the guard already present in `sign_transfer` at line 482–485 but places it at the entry point, before any irreversible state change.

---

### Proof of Concept

1. Register a NEAR-native token with `origin_decimals = 24`, `decimals = 18` (diff = 6).
2. Call `ft_transfer_call` with `amount = 500_000`, `fee = 0`, recipient on EVM.
3. `ft_on_transfer` → `init_transfer` passes the `fee < amount` check.
4. `init_transfer_internal` burns/locks `500_000` tokens and inserts the transfer message.
5. Relayer calls `sign_transfer` for the new `TransferId`.
6. `normalize_amount(500_000 − 0, {24, 18})` = `500_000 / 1_000_000` = **0**.
7. `require!(0 > 0, ...)` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
8. `pending_transfers` still contains the entry; tokens are permanently burned/locked.
9. Every future `sign_transfer` call for this `TransferId` produces the same panic. No recovery is possible. [5](#0-4) [6](#0-5) [4](#0-3) [3](#0-2)

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
