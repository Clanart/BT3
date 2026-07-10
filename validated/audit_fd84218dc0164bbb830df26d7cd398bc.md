### Title
`normalize_amount` Returns Zero for Small Transfers, Permanently Locking User Funds - (`File: near/omni-bridge/src/lib.rs`)

### Summary

When a user initiates a NEAR-originated transfer of a token whose `origin_decimals` exceeds its normalized `decimals`, any transfer where `amount_without_fee < 10^(origin_decimals - decimals)` causes `normalize_amount` to return `0`. The subsequent `require!(amount_to_transfer > 0, ...)` check in `sign_transfer` then permanently panics, leaving the user's tokens irrecoverably locked in the bridge with no cancel path.

### Finding Description

`normalize_amount` and `denormalize_amount` use integer floor division and multiplication respectively, keyed on the difference between `origin_decimals` and `decimals`:

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

For a token with `origin_decimals = 24` and `decimals = 18` (a realistic case for tokens bridged from Solana or NEAR to EVM), `diff_decimals = 6`. Any transfer where `amount_without_fee < 1_000_000` (i.e., less than `10^6`) produces `normalize_amount(...) == 0`.

`sign_transfer` then enforces:

```rust
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [2](#0-1) 

This `require!` panics unconditionally for every subsequent call to `sign_transfer` for that transfer ID, because the stored `TransferMessage` amount never changes.

The critical gap is that `init_transfer` only validates `fee < amount`:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [3](#0-2) 

It does **not** validate that `normalize_amount(amount - fee, decimals) > 0`. The user's tokens are already transferred to the bridge contract before `sign_transfer` is ever called, and there is no user-callable cancel or refund path visible in the contract.

The `Decimals` struct stores both fields, and the difference is set at token registration time via `add_token`:

```rust
self.token_decimals.insert(
    token_address,
    &Decimals { decimals, origin_decimals },
)
``` [4](#0-3) 

For tokens deployed via `deploy_token_internal`, both fields are set to the same value (diff = 0, no issue). But for tokens registered via `bind_token_callback`, `decimals` and `origin_decimals` can differ:

```rust
self.add_token(
    &deploy_token.token,
    &deploy_token.token_address,
    deploy_token.decimals,
    deploy_token.origin_decimals,
);
``` [5](#0-4) 

On the EVM side, `_normalizeDecimals` caps at 18, so a token with 24 origin decimals gets `decimals = 18`, `origin_decimals = 24`, diff = 6:

```solidity
function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
    uint8 maxAllowedDecimals = 18;
    if (decimals > maxAllowedDecimals) { return maxAllowedDecimals; }
    return decimals;
}
``` [6](#0-5) 

Similarly on Solana, `MAX_ALLOWED_DECIMALS` is enforced:

```rust
let origin_decimals = metadata.decimals;
metadata.decimals = std::cmp::min(MAX_ALLOWED_DECIMALS, metadata.decimals);
``` [7](#0-6) 

### Impact Explanation

A user who transfers a sub-threshold amount of any token with `origin_decimals > decimals` has their tokens permanently locked. `sign_transfer` will panic on every invocation for that `transfer_id`. There is no user-accessible cancel or refund function in the contract. This matches **Critical: Permanent freezing / irrecoverable lock of user funds in bridge flows**.

### Likelihood Explanation

Any unprivileged user can trigger this by calling `ft_transfer_call` with a small amount of a token that has a decimal difference. Tokens bridged from Solana (which caps at 9 decimals) or EVM (capped at 18) against NEAR tokens with higher native precision are affected. The user need not be malicious — a legitimate small transfer suffices.

### Recommendation

Add a pre-flight check in `init_transfer` (or in `ft_on_transfer`) that validates the normalized amount will be non-zero before accepting the tokens:

```rust
let token_address = self.get_token_address(destination_chain, token_id.clone());
if let Some(addr) = token_address {
    if let Some(decimals) = self.token_decimals.get(&addr) {
        let normalized = Self::normalize_amount(
            amount_without_fee,
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

Alternatively, add a protocol-level minimum transfer amount per token that accounts for the decimal scaling factor.

### Proof of Concept

1. A token is registered via `bind_token` with `origin_decimals = 24`, `decimals = 18` (diff = 6).
2. User calls `ft_transfer_call` with `amount = 500_000` (less than `10^6`), fee = 0.
3. `init_transfer` accepts the transfer: `fee (0) < amount (500_000)` passes.
4. Tokens are transferred to the bridge contract and `TransferMessage` is stored.
5. Relayer calls `sign_transfer` for this `transfer_id`.
6. `normalize_amount(500_000, Decimals { decimals: 18, origin_decimals: 24 })` = `500_000 / 10^6` = `0`.
7. `require!(0 > 0, ...)` panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`.
8. Every subsequent call to `sign_transfer` for this transfer ID panics identically.
9. The user's 500,000 units are permanently locked in the bridge with no recovery path.

### Citations

**File:** near/omni-bridge/src/lib.rs (L482-485)
```rust
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

**File:** near/omni-bridge/src/lib.rs (L1262-1267)
```rust
        self.add_token(
            &deploy_token.token,
            &deploy_token.token_address,
            deploy_token.decimals,
            deploy_token.origin_decimals,
        );
```

**File:** near/omni-bridge/src/lib.rs (L2724-2735)
```rust
        require!(
            self.token_decimals
                .insert(
                    token_address,
                    &Decimals {
                        decimals,
                        origin_decimals,
                    }
                )
                .is_none(),
            BridgeError::TokenExists.as_ref()
        );
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```

**File:** evm/src/omni-bridge/contracts/OmniBridge.sol (L586-592)
```text
    function _normalizeDecimals(uint8 decimals) internal pure returns (uint8) {
        uint8 maxAllowedDecimals = 18;
        if (decimals > maxAllowedDecimals) {
            return maxAllowedDecimals;
        }
        return decimals;
    }
```

**File:** solana/programs/bridge_token_factory/src/instructions/user/deploy_token.rs (L77-78)
```rust
        let origin_decimals = metadata.decimals;
        metadata.decimals = std::cmp::min(MAX_ALLOWED_DECIMALS, metadata.decimals);
```
