### Title
Decimal Normalization Floor in `sign_transfer` Permanently Locks User Funds When Transfer Amount Is Below Destination Chain's Minimum Representable Unit — (`near/omni-bridge/src/lib.rs`)

### Summary
When a user initiates a NEAR→EVM outbound transfer with an `amount_without_fee` smaller than `10^(origin_decimals − decimals)`, the `normalize_amount` floor-division in `sign_transfer` produces zero. The subsequent `require!(amount_to_transfer > 0)` guard panics, permanently blocking the transfer. Because no cancel or refund path exists for pending outbound transfers, the user's tokens are irrecoverably locked in the bridge contract.

### Finding Description

**Root cause — missing minimum-amount guard at `init_transfer` entry point**

`init_transfer` accepts any amount that satisfies `fee < amount`: [1](#0-0) 

It stores the `TransferMessage` in `pending_transfers` and the tokens are immediately held by the bridge. No check is made that the amount will survive decimal normalization.

Later, when a relayer calls `sign_transfer`, the bridge normalises the amount for the destination chain: [2](#0-1) 

`normalize_amount` uses integer floor division: [3](#0-2) 

For a token registered with `origin_decimals = 24` (NEAR) and `decimals = 6` (EVM), the divisor is `10^18`. Any `amount_without_fee < 10^18` normalises to `0`, causing `sign_transfer` to panic with `InvalidAmountToTransfer`. The transfer message remains in `pending_transfers` indefinitely.

**No recovery path exists.** `remove_transfer_message` is only called from `claim_fee_callback` (requires a proof from the destination chain that a `FinTransfer` event was emitted — impossible if the transfer was never signed) and from `process_fin_transfer_to_near` (inbound path, irrelevant here). There is no `cancel_transfer` or user-initiated refund function. [4](#0-3) 

The code comment on `normalize_amount` acknowledges dust locking only for the *remainder* after a successful normalisation: [5](#0-4) 

This is distinct from the scenario described here, where the *entire* transfer amount normalises to zero and no tokens are ever delivered.

### Impact Explanation

Any user who bridges a token with a large decimal gap (e.g., 24 NEAR → 6 EVM) and sends fewer than `10^(origin_decimals − decimals)` base units will have their entire transfer amount permanently locked in the bridge contract. For a 24→6 decimal token, the minimum bridgeable amount is `10^18` base units (= 0.000001 of the token in 6-decimal representation). Amounts below this threshold are accepted by `init_transfer`, locked, and then irrecoverably stuck because `sign_transfer` will always revert.

This matches the allowed impact: **Permanent freezing / irrecoverable lock of user funds in bridge flows.**

### Likelihood Explanation

The scenario is reachable by any unprivileged user. Tokens with large decimal gaps (24 on NEAR, 6 or 8 on EVM) are common (e.g., USDT, USDC, wBTC bridged representations). A user who sends a small amount — or whose `amount_without_fee` falls below the threshold after a fee is set — will trigger this path. The `init_transfer` entry point provides no feedback that the amount is too small; it accepts the transaction and locks the tokens.

### Recommendation

Add a minimum-amount check at the `init_transfer` entry point, before tokens are locked, that verifies `normalize_amount(amount_without_fee, decimals) > 0` for the destination chain's registered decimals. Reject the transfer early with a clear error rather than accepting tokens that can never be delivered.

### Proof of Concept

1. Token `T` is registered with `origin_decimals = 24`, `decimals = 6` (destination EVM chain).
2. User calls `ft_transfer_call` transferring `amount = 500_000_000_000_000_000` (5 × 10^17, i.e., 0.0000005 T) with `fee = 0`.
3. `init_transfer` passes the only guard (`fee < amount` → `0 < 5×10^17` ✓) and stores the `TransferMessage`. Tokens are now held by the bridge.
4. Relayer calls `sign_transfer`:
   - `amount_without_fee = 5×10^17`
   - `normalize_amount(5×10^17, Decimals{decimals:6, origin_decimals:24})` = `5×10^17 / 10^18` = **0** (floor division)
   - `require!(0 > 0, ...)` → **panics with `InvalidAmountToTransfer`**
5. Every subsequent `sign_transfer` call for this transfer ID panics identically.
6. No cancel or refund function exists. The `5×10^17` base units of token `T` are permanently locked in the bridge contract. [1](#0-0) [2](#0-1) [3](#0-2)

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

**File:** near/omni-bridge/src/lib.rs (L1094-1094)
```rust
        let transfer_message = self.remove_transfer_message(fin_transfer.transfer_id);
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
