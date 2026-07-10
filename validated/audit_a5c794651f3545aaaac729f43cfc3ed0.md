### Title
`sign_transfer` Rejects Transfers With Sub-Minimum Normalized Amount That Were Accepted at `init_transfer` — (`File: near/omni-bridge/src/lib.rs`)

---

### Summary

The NEAR bridge accepts a transfer at initiation time with only a `fee < amount` check, but applies a stricter `normalize_amount(amount - fee, decimals) > 0` check at signing time. For tokens where `origin_decimals > decimals` (e.g., a NEAR-native token with 24 decimals bridging to a 6-decimal destination), any transfer whose net amount is below `10^(origin_decimals - decimals)` will be permanently locked: it passes `init_transfer` but can never pass `sign_transfer`.

---

### Finding Description

**At `init_transfer` time**, the only validation on the amount is:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [1](#0-0) 

There is no check that the amount, after decimal normalization, is non-zero. The tokens are immediately locked in the bridge and the `TransferMessage` is stored in `pending_transfers`.

**At `sign_transfer` time**, the relayer calls `sign_transfer`, which computes:

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
``` [2](#0-1) 

`normalize_amount` performs **floor division**:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [3](#0-2) 

For any token where `origin_decimals > decimals`, if `amount - fee < 10^(origin_decimals - decimals)`, the result is `0`, and `sign_transfer` panics with `InvalidAmountToTransfer`. The transfer can never be signed.

There is no user-callable cancel or refund path once a transfer is stored. `remove_transfer_message` is only invoked inside `sign_transfer_callback` (on success with zero fee) and `claim_fee_callback` — neither of which is reachable when signing itself reverts. [4](#0-3) 

`update_transfer_fee` cannot rescue the transfer either: it only allows raising the fee up to `amount - 1`, leaving `amount_without_fee() = 1`, which still normalizes to `0` under floor division. [5](#0-4) 

---

### Impact Explanation

**Critical — Permanent freezing of user funds.**

A user whose transfer amount (net of fee) is below the normalization threshold has their tokens irrecoverably locked in the NEAR bridge contract. There is no escape hatch: the transfer cannot be signed, cancelled, or refunded. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** The condition is triggered whenever `amount - fee < 10^(origin_decimals - decimals)`. For tokens with a large decimal gap (e.g., a NEAR-side token with 24 decimals bridging to a 6-decimal destination, giving `diff = 18`), the threshold is `10^18` base units — a non-trivial amount. A user who initiates a transfer just below this threshold (e.g., `amount = 10^18 - 1`, `fee = 0`) will have their tokens permanently locked. The check is entirely absent at initiation time, so no warning is given to the user. Any unprivileged user calling `ft_transfer_call` into the bridge is the attacker-controlled entry path.

---

### Recommendation

Add the same `normalize_amount > 0` guard inside `init_transfer` (or `init_transfer_internal`) before storing the transfer message, so that sub-minimum transfers are rejected immediately and the tokens are returned to the sender via the NEP-141 `ft_transfer_call` refund mechanism (returning the full `amount` from `ft_on_transfer`).

```rust
// In init_transfer, after building transfer_message:
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
).near_expect(BridgeError::FailedToGetTokenAddress);

let decimals = self.token_decimals
    .get(&token_address)
    .near_expect(BridgeError::TokenDecimalsNotFound);

let normalized = Self::normalize_amount(
    transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
    decimals,
);
require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
```

---

### Proof of Concept

Assume a token registered with `origin_decimals = 24`, `decimals = 6` (diff = 18).

1. Alice calls `ft_transfer_call` on the token contract with `amount = 5 * 10^17`, `fee = 0`, `recipient = <EVM address>`.
2. `init_transfer` checks `fee (0) < amount (5e17)` → passes. Transfer stored. Alice's tokens locked.
3. Relayer calls `sign_transfer` for Alice's transfer.
4. `normalize_amount(5e17, {decimals:6, origin_decimals:24}) = 5e17 / 10^18 = 0` (floor division).
5. `require!(0 > 0, "InvalidAmountToTransfer")` → **panics**.
6. No other path exists to recover Alice's `5 * 10^17` tokens. They are permanently locked. [6](#0-5) [7](#0-6) [8](#0-7)

### Citations

**File:** near/omni-bridge/src/lib.rs (L399-402)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
                );
```

**File:** near/omni-bridge/src/lib.rs (L471-485)
```rust
        let decimals = self
            .token_decimals
            .get(&token_address)
            .near_expect(BridgeError::TokenDecimalsNotFound);
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

**File:** near/omni-bridge/src/lib.rs (L2776-2787)
```rust
    fn denormalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount * (10_u128.pow(diff_decimals))
    }

    /// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
    /// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
    /// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
