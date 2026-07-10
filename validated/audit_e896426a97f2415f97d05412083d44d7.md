### Title
Tokens Permanently Locked/Burned When Transfer Amount Normalizes to Zero in `sign_transfer` - (File: `near/omni-bridge/src/lib.rs`)

### Summary

When a user initiates a NEAR-side bridge transfer with an amount that is smaller than the decimal normalization divisor (`10^(origin_decimals - decimals)`), the tokens are irreversibly burned or locked during `init_transfer_internal`, but the subsequent `sign_transfer` call always reverts with `ERR_INVALID_AMOUNT_TO_TRANSFER` because `normalize_amount` returns zero. There is no recovery path, so the user's funds are permanently frozen in the bridge.

### Finding Description

The NEAR bridge contract accepts a transfer via `ft_transfer_call` → `ft_on_transfer` → `init_transfer` → `init_transfer_internal`. Inside `init_transfer_internal`, the tokens are consumed unconditionally on the success path:

- For deployed (bridged) tokens: `burn_tokens_if_needed` is called, burning the tokens.
- For native tokens: `lock_tokens_if_needed` is called, locking them in the bridge. [1](#0-0) 

The function then returns `U128(0)`, signaling to the NEP-141 `ft_transfer_call` framework that all tokens were consumed (no refund). [2](#0-1) 

Later, the relayer calls `sign_transfer` to produce the MPC signature needed to finalize the transfer on the destination chain. Inside `sign_transfer`, the net transfer amount is computed using floor division:

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

`normalize_amount` divides by `10^(origin_decimals - decimals)`: [4](#0-3) 

If `amount_without_fee < 10^(origin_decimals - decimals)`, the result is `0` (floor division), and `sign_transfer` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`. This panic is permanent — the transfer record stays in `pending_transfers` forever, and there is no `cancel_transfer` or admin rescue function.

The only guard in `init_transfer` is `fee.fee < amount`, which does not prevent amounts that normalize to zero: [5](#0-4) 

### Impact Explanation

**Critical — Permanent irrecoverable lock of user funds.**

Any user who initiates a transfer with `amount_without_fee < 10^(origin_decimals - decimals)` will have their tokens permanently burned or locked. For a token registered with `origin_decimals = 24` and `decimals = 18` (a common NEAR-to-EVM pairing), any transfer where `amount - fee < 1,000,000` base units will trigger this. The tokens are consumed at `init_transfer_internal` and can never be recovered because `sign_transfer` will always revert for that transfer ID.

### Likelihood Explanation

Any unprivileged user can trigger this by calling `ft_transfer_call` with a sufficiently small amount. No special role or access is required. The `init_transfer` validation only checks `fee < amount`, not that the normalized net amount is nonzero. A user unfamiliar with the decimal normalization mechanics, or a user sending a small "test" transfer, can easily hit this condition.

### Recommendation

Add a normalization check at the start of `init_transfer` (before tokens are consumed) to reject transfers whose net amount would normalize to zero:

```rust
// In init_transfer, after constructing transfer_message:
let token_address = self.get_token_address(
    transfer_message.get_destination_chain(),
    self.get_token_id(&transfer_message.token),
);
if let Some(addr) = token_address {
    if let Some(decimals) = self.token_decimals.get(&addr) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee().near_expect(BridgeError::InvalidFee),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

This mirrors the check already present in `sign_transfer` but places it before any state mutation, so tokens are never consumed for an unfinalizeable transfer.

### Proof of Concept

1. Register a token with `origin_decimals = 24`, `decimals = 18` (divisor = `10^6 = 1,000,000`).
2. User calls `ft_transfer_call` with `amount = 500,000`, `fee = 0`, targeting an EVM recipient.
3. `init_transfer` passes the `fee < amount` check (0 < 500,000). ✓
4. `init_transfer_internal` burns/locks the 500,000 tokens and stores the transfer. Tokens consumed. ✓
5. Relayer calls `sign_transfer` for this transfer ID.
6. `normalize_amount(500_000, {origin_decimals:24, decimals:18}) = 500_000 / 1_000_000 = 0`.
7. `require!(0 > 0, ...)` → panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`. ✗
8. The transfer remains in `pending_transfers` indefinitely. The 500,000 tokens are permanently lost. [3](#0-2) [6](#0-5) [4](#0-3)

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

**File:** near/omni-bridge/src/lib.rs (L1850-1864)
```rust
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
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
