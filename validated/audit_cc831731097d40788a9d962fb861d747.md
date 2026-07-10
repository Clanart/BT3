### Title
Missing Minimum Normalized Amount Check in `init_transfer` Causes Permanent Fund Lock — (`near/omni-bridge/src/lib.rs`)

### Summary

The NEAR bridge's `init_transfer` path accepts and permanently consumes user tokens even when the transfer amount (after fee deduction) normalizes to zero on the destination chain. The guard against a zero normalized amount exists only in `sign_transfer`, which is called later by a relayer — after the user's tokens are already irrecoverably locked or burned. With no cancel mechanism available to the user, the funds are permanently frozen.

### Finding Description

When a user bridges a NEAR-native token to a lower-decimal destination chain (e.g., 18-decimal NEAR token → 6-decimal EVM USDC), the bridge must normalize the amount via floor division. The normalization check is placed in `sign_transfer`: [1](#0-0) 

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

However, by the time `sign_transfer` is called, the user's tokens have already been consumed. The `init_transfer` path only validates `fee < amount`: [2](#0-1) 

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

This does **not** guarantee that `normalize_amount(amount - fee) > 0`. A user can set `fee = amount - 1`, leaving `amount_without_fee = 1 yoctoNEAR`. For a 6-decimal destination token, the normalization factor is `10^12`, so `normalize_amount(1) = 0`. The transfer message is stored in `pending_transfers` and the tokens are consumed (locked for native tokens, burned for bridge tokens): [3](#0-2) 

The `update_transfer_fee` function cannot rescue the user — it only allows increasing the fee, not decreasing it: [4](#0-3) 

```rust
require!(
    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

The only privileged recovery path is `transfer_token_as_dao`, which requires the `DAO` role and is not accessible to the user. [5](#0-4) 

The normalization remainder behavior is explicitly acknowledged in the codebase comment in `claim_fee_callback`: [6](#0-5) 

```rust
// Since `denormalize(normalize(x)) <= x` due to floor division,
// the difference naturally captures the normalization remainder.
```

This confirms that `normalize_amount` uses floor division and can produce zero for small inputs.

### Impact Explanation

**Critical — Permanent freezing of user funds.**

A user who initiates a transfer where `normalize_amount(amount - fee) == 0` will have their tokens permanently locked in the bridge (or burned if it is a bridge token), with no user-accessible recovery path. The transfer sits in `pending_transfers` indefinitely; no relayer can ever successfully call `sign_transfer` for it, and the user cannot cancel or reclaim the tokens.

### Likelihood Explanation

**Medium.** The condition is reachable by any unprivileged user via `ft_transfer_call` → `ft_on_transfer` → `init_transfer`. It is most likely to occur when:

1. A user sets a fee close to the transfer amount (e.g., `fee = amount - 1`) to maximize relayer incentive, leaving a sub-threshold remainder.
2. A user sends a very small amount (below the normalization threshold, e.g., `< 10^12 yoctoNEAR` for a 6-decimal EVM token).
3. A UI or integration does not pre-validate the normalized output amount before submitting the transaction.

For any 18-decimal NEAR token bridged to a 6-decimal EVM token, the threshold is `10^12 yoctoNEAR` (= 0.000001 tokens). Any `amount_without_fee` below this value produces a zero normalized amount.

### Recommendation

Add a check in `init_transfer` (or `init_transfer_internal`) that validates the normalized amount is greater than zero **before** consuming the user's tokens:

```rust
let token_address = self.get_token_address(
    init_transfer_msg.get_destination_chain(),
    token_id.clone(),
);
if let Some(token_address) = token_address {
    if let Some(decimals) = self.token_decimals.get(&token_address) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

This mirrors the existing guard in `sign_transfer` but places it at the point of user action — before tokens are consumed — consistent with the mitigation pattern recommended in the referenced report (add a `minCollateralAmount` check at the entry point of the user action).

### Proof of Concept

1. A 18-decimal NEAR token is registered and mapped to a 6-decimal EVM token (normalization factor = `10^12`).
2. User calls `ft_transfer_call` with `amount = 2_000_000_000_000` (2 × 10^12 yoctoNEAR) and `msg` containing `InitTransferMsg { fee: 1_000_000_000_001, ... }`.
3. `init_transfer` validates `fee (1_000_000_000_001) < amount (2_000_000_000_000)` — passes.
4. `amount_without_fee = 999_999_999_999` (< 10^12).
5. Transfer message is stored; `ft_on_transfer` returns `U128(0)`, consuming all tokens.
6. Relayer calls `sign_transfer`; `normalize_amount(999_999_999_999) = 0`; panics with `InvalidAmountToTransfer`.
7. Transfer remains in `pending_transfers` forever. User's tokens are permanently locked. [1](#0-0) [2](#0-1)

### Citations

**File:** near/omni-bridge/src/lib.rs (L399-401)
```rust
                require!(
                    fee.fee >= current_fee.fee && fee.fee < transfer.message.amount,
                    BridgeError::InvalidFee.as_ref()
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

**File:** near/omni-bridge/src/lib.rs (L1128-1131)
```rust
        // Fee includes both the user-specified fee and any dust lost during decimal
        // normalization (see `normalize_amount`). Since `denormalize(normalize(x)) <= x`
        // due to floor division, the difference naturally captures the normalization remainder.
        let fee = transfer_message.amount.0 - denormalized_amount;
```

**File:** near/omni-bridge/src/lib.rs (L1511-1530)
```rust
    #[access_control_any(roles(Role::DAO))]
    pub fn transfer_token_as_dao(
        &mut self,
        token: AccountId,
        amount: U128,
        recipient: AccountId,
        msg: Option<String>,
    ) -> Promise {
        if let Some(msg) = msg {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_CALL_GAS)
                .ft_transfer_call(recipient, amount, None, msg)
        } else {
            ext_token::ext(token)
                .with_attached_deposit(ONE_YOCTO)
                .with_static_gas(FT_TRANSFER_GAS)
                .ft_transfer(recipient, amount, None)
        }
    }
```
