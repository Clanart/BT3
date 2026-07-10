### Title
Fee Validity Check Before Decimal Normalization Allows Permanent Freezing of User Funds - (File: `near/omni-bridge/src/lib.rs`)

---

### Summary

In the NEAR bridge, the fee validity check (`fee < amount`) is performed at `init_transfer` time using raw NEAR-native token amounts. However, when `sign_transfer` is later called, the net amount `(amount - fee)` is normalized to the destination chain's decimal precision via floor division. If `(amount - fee)` is positive but smaller than the normalization factor, `normalize_amount` returns zero, causing `sign_transfer` to panic with `InvalidAmountToTransfer`. The user's tokens are already locked or burned at this point and there is no cancel/refund path, resulting in permanent loss.

---

### Finding Description

**Step 1 — Fee check at `init_transfer` (raw amounts):**

In `init_transfer`, the only fee validity check is:

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
```

This check passes as long as `fee < amount` in raw NEAR-native units (yoctoNEAR or the token's native smallest unit). No normalization is applied here. [1](#0-0) 

**Step 2 — Tokens are locked/burned immediately:**

`init_transfer_internal` is called, which burns bridged tokens or locks native tokens before returning. The transfer is stored in `pending_transfers`. [2](#0-1) 

**Step 3 — Normalization at `sign_transfer` (floor division):**

When a relayer later calls `sign_transfer`, the net amount is normalized to the destination chain's decimal precision using floor division:

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
``` [3](#0-2) 

**Step 4 — `normalize_amount` uses floor division:**

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
```

For NEAR (24 decimals) → EVM (18 decimals), the normalization factor is `10^6`. Any net amount `< 10^6` normalizes to zero. [4](#0-3) 

**Step 5 — `amount_without_fee` is a simple subtraction:**

```rust
pub fn amount_without_fee(&self) -> Option<u128> {
    self.amount.0.checked_sub(self.fee.fee.0)
}
``` [5](#0-4) 

**Step 6 — No cancel/refund path exists:**

`update_transfer_fee` only allows increasing the fee (not decreasing it), so the user cannot rescue the transfer by lowering the fee. There is no user-callable cancel function. The only way to remove a transfer from `pending_transfers` is via `claim_fee_callback`, which requires a successful finalization proof from the destination chain — impossible if `sign_transfer` always panics. [6](#0-5) 

---

### Impact Explanation

**Permanent freezing of user funds.** A user who submits a transfer where `0 < (amount - fee) < normalization_factor` will have their tokens locked in the bridge (or burned if it is a bridged token) with no recovery path. `sign_transfer` will always revert with `InvalidAmountToTransfer`, and no cancel mechanism exists. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

For NEAR → EVM transfers, the normalization factor is `10^6` (NEAR has 24 decimals, EVM tokens typically have 18). A user who sends `amount = 1_000_001` yoctoNEAR-equivalent tokens with `fee = 999_999` passes the `fee < amount` check at init time, but `amount - fee = 2`, which normalizes to `0`. This is a realistic edge case reachable by any unprivileged bridge user calling `ft_transfer_call` with a crafted `InitTransferMsg`. The user does not need any special role or permission.

---

### Recommendation

Validate that `normalize_amount(amount_without_fee) > 0` at `init_transfer` time, before locking or burning tokens. Specifically, add the following check inside `init_transfer` after the fee check:

```rust
// Ensure the net amount survives decimal normalization
let token_address = self.get_token_address(
    init_transfer_msg.get_destination_chain(),
    token_id.clone(),
);
if let Some(addr) = token_address {
    if let Some(decimals) = self.token_decimals.get(&addr) {
        let normalized = Self::normalize_amount(
            transfer_message.amount_without_fee().unwrap_or(0),
            decimals,
        );
        require!(normalized > 0, BridgeError::InvalidAmountToTransfer.as_ref());
    }
}
```

This mirrors the fix recommended in M-03: enforce the minimum constraint *after* the reduction is applied, not before.

---

### Proof of Concept

1. Token: NEAR-native token with `origin_decimals = 24`, `decimals = 18` (normalization factor = `10^6`).
2. User calls `ft_transfer_call` with `amount = 1_000_001` and `InitTransferMsg { fee: 999_999, recipient: <EVM address>, ... }`.
3. `init_transfer` checks `999_999 < 1_000_001` → passes. Tokens are locked. Transfer stored in `pending_transfers`.
4. Relayer calls `sign_transfer`. `amount_without_fee() = 2`. `normalize_amount(2, {24, 18}) = 2 / 10^6 = 0`.
5. `require!(amount_to_transfer > 0, ...)` → panics. `sign_transfer` reverts.
6. Transfer remains in `pending_transfers` forever. User's `1_000_001` units are permanently locked with no recovery path. [1](#0-0) [3](#0-2) [4](#0-3) [5](#0-4)

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

**File:** near/omni-bridge/src/lib.rs (L554-557)
```rust
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

**File:** near/omni-types/src/lib.rs (L593-595)
```rust
    pub fn amount_without_fee(&self) -> Option<u128> {
        self.amount.0.checked_sub(self.fee.fee.0)
    }
```
