### Title
Sub-Dust Transfer Permanently Locks User Tokens in NEAR Bridge — (`File: near/omni-bridge/src/lib.rs`)

---

### Summary

`init_transfer` accepts any token amount that exceeds the fee without verifying that the amount survives decimal normalization. `sign_transfer` later rejects the transfer with `ERR_INVALID_AMOUNT_TO_TRANSFER` when `normalize_amount(amount_without_fee()) == 0`. Because no cancel or refund path exists, the locked tokens are irrecoverable.

---

### Finding Description

**Root cause — missing pre-flight normalization check in `init_transfer`**

`init_transfer` stores the transfer message and returns `U128(0)` to the NEP-141 callback, keeping all tokens in the bridge. Its only amount validation is:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [1](#0-0) 

This check passes for any `amount >= 1` with `fee = 0`. It does **not** verify that the amount survives the decimal normalization that will be applied later.

**The normalization that blocks signing**

`normalize_amount` uses integer floor division:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [2](#0-1) 

For a NEAR-native token (`origin_decimals = 24`) bridging to EVM (`decimals = 18`), `diff_decimals = 6`. Any amount below `1_000_000` normalizes to `0`.

**The hard block in `sign_transfer`**

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

Every call to `sign_transfer` for this transfer will panic. The transfer message is never removed, and the tokens are never returned.

**No recovery path**

`sign_transfer_callback` only removes the transfer message when signing succeeds and `fee.is_zero()`. [4](#0-3) 

`claim_fee` requires a successful prior `sign_transfer` to have produced a valid proof. There is no `cancel_transfer` or user-callable refund function anywhere in the contract. The only administrative escape valve is `set_locked_tokens`, which adjusts accounting but does not move the physically held tokens. [5](#0-4) 

---

### Impact Explanation

Any NEAR-native token with `origin_decimals > decimals` (e.g., 24 vs 18) is affected. A user who sends an amount below the normalization threshold — e.g., fewer than `1_000_000` yocto-units of a 24-decimal token toward an 18-decimal EVM destination — will have those tokens permanently locked in the bridge contract with no on-chain recovery path. This matches the allowed impact: **Critical — permanent freezing / irrecoverable lock of user funds in bridge flows**.

---

### Likelihood Explanation

The entry point is the standard NEP-141 `ft_transfer_call` flow, callable by any token holder. No privileged role is required. The condition is easily triggered accidentally (small test transfers, dust amounts, or tokens with large decimal gaps) or deliberately by a griefing attacker who wants to lock their own or others' funds. Tokens with 24-decimal NEAR-side representation bridging to 18-decimal EVM chains are the primary production case.

---

### Recommendation

Add a normalization pre-check inside `init_transfer` (or `init_transfer_internal`) before accepting the tokens:

```rust
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
);
if let Some(addr) = token_address {
    if let Some(decimals) = self.token_decimals.get(&addr) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee()
                .near_expect(BridgeError::InvalidFee),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

Returning the full amount from `ft_on_transfer` (refunding the sender) when this check fails is the correct NEP-141 pattern and avoids any lock.

---

### Proof of Concept

1. Deploy a NEAR-native token with 24 decimals. Register it in the bridge with `origin_decimals = 24`, `decimals = 18` for an EVM destination.
2. Call `ft_transfer_call` with `amount = 500_000` (below the `1_000_000` normalization threshold), `fee = 0`, and a valid EVM recipient.
3. `init_transfer` accepts the tokens (fee check `0 < 500_000` passes). Tokens are held by the bridge.
4. Trusted relayer calls `sign_transfer` for the resulting `transfer_id`.
5. `normalize_amount(500_000, Decimals { origin_decimals: 24, decimals: 18 })` returns `0`.
6. `require!(amount_to_transfer > 0, ...)` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
7. Repeat step 4 indefinitely — result is always the same panic.
8. No `cancel_transfer` exists. The `500_000` units are permanently locked in the bridge contract. [6](#0-5) [3](#0-2) [2](#0-1)

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

**File:** near/omni-bridge/src/lib.rs (L655-658)
```rust
        if let Ok(signature) = call_result {
            if fee.is_zero() {
                self.remove_transfer_message(message_payload.transfer_id);
            }
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** near/omni-bridge/src/token_lock.rs (L38-44)
```rust
    #[access_control_any(roles(Role::DAO, Role::TokenLockController))]
    pub fn set_locked_tokens(&mut self, args: Vec<SetLockedTokenArgs>) {
        for arg in args {
            self.locked_tokens
                .insert(&(arg.chain_kind, arg.token_id), &arg.amount.0);
        }
    }
```
