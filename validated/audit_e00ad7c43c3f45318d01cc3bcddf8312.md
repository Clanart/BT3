### Title
User Tokens Permanently Locked/Burned When Transfer Amount Normalizes to Zero Due to Missing Pre-Transfer Decimal Validation — (File: `near/omni-bridge/src/lib.rs`)

### Summary

The NEAR Omni Bridge's `init_transfer_internal` locks or burns user tokens before any decimal-normalization check is performed. When a user initiates a transfer with an amount smaller than the decimal-scaling factor (`10^(origin_decimals - decimals)`), `normalize_amount` returns zero. The subsequent `sign_transfer` call then always panics with `InvalidAmountToTransfer`, making the transfer permanently uncompletable. Because no public cancel/refund path exists for this state, the user's tokens are irrecoverably lost.

---

### Finding Description

**Vulnerability class:** Silent fund consumption / missing pre-transfer input validation leading to permanent fund lock.

**Transfer flow on NEAR:**

1. User calls `ft_transfer_call` → `ft_on_transfer` → `init_transfer` → `init_transfer_internal`
2. Inside `init_transfer_internal`, tokens are **immediately locked or burned** and the transfer message is stored in `pending_transfers`.
3. Later, a relayer calls `sign_transfer`, which applies `normalize_amount` to `amount_without_fee()`.
4. If the normalized result is zero, `sign_transfer` panics with `InvalidAmountToTransfer` — **every future signing attempt will also fail**.

**`normalize_amount` uses floor division:**

```rust
fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
    let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
    amount / (10_u128.pow(diff_decimals))
}
``` [1](#0-0) 

Any amount strictly less than `10^(origin_decimals - decimals)` normalizes to zero. For a token with `origin_decimals = 24` and `decimals = 6` (a 18-decimal gap, common for NEAR-native tokens bridged to EVM), any amount below `10^18` (i.e., below 1 whole EVM-side token) normalizes to zero.

**The zero-amount guard exists only in `sign_transfer`, after funds are already consumed:**

```rust
require!(
    amount_to_transfer > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
``` [2](#0-1) 

**But by this point, `init_transfer_internal` has already locked/burned the tokens:**

```rust
self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);
self.lock_tokens_if_needed(
    transfer_message.get_destination_chain(),
    &token_id,
    transfer_message.amount.0,
);
``` [3](#0-2) 

**`init_transfer` only validates `fee < amount`, not that `normalize_amount(amount - fee) > 0`:**

```rust
require!(
    transfer_message.fee.fee < transfer_message.amount,
    BridgeError::InvalidFee.as_ref()
);
``` [4](#0-3) 

There is no public cancel or refund entry point for a stuck pending transfer. `remove_transfer_message` is a private helper; no public `cancel_transfer` function is exposed to users. [5](#0-4) 

The protocol's own comment acknowledges that when `fee = 0`, dust "stays locked/burned" — but this is documented only for sub-unit dust remainders, not for the case where the **entire** transfer amount normalizes to zero:

```rust
/// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
/// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
/// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
``` [6](#0-5) 

---

### Impact Explanation

A user who sends a token amount below the normalization threshold has their tokens **permanently locked in the bridge contract (for native tokens) or permanently burned (for bridged tokens)** with no recovery path. The transfer message remains in `pending_transfers` indefinitely, but `sign_transfer` will always revert. This constitutes an irrecoverable loss of user funds, matching the allowed impact: **Critical — Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows.**

---

### Likelihood Explanation

This is reachable by any unprivileged user who calls `ft_transfer_call` with a small amount on a token that has a large decimal gap between its NEAR representation and its destination-chain representation. Tokens with `origin_decimals = 24` (NEAR-native) bridged to EVM chains with `decimals = 6` have an 18-decimal gap, meaning any amount below `10^18` (i.e., below 1 whole destination-side token) triggers the bug. A user sending, for example, `500_000_000_000_000_000` (0.5 of a NEAR-native token) would lose their entire balance. This is a realistic user mistake, especially for users unfamiliar with decimal normalization.

---

### Recommendation

Add a normalization check inside `init_transfer` (or `init_transfer_internal`) **before** locking or burning tokens. Retrieve the destination token's `Decimals` and verify that `normalize_amount(amount_without_fee, decimals) > 0`. If the check fails, revert the transaction immediately so the user's `ft_transfer_call` returns the full amount (NEAR's NEP-141 `ft_transfer_call` refunds tokens when `ft_on_transfer` returns the original amount).

```rust
// Before locking/burning, validate normalized amount
let token_address = self.get_token_address(destination_chain, token_id.clone())
    .expect("token address must exist");
let decimals = self.token_decimals.get(&token_address)
    .expect("decimals must exist");
require!(
    Self::normalize_amount(transfer_message.amount_without_fee().unwrap(), decimals) > 0,
    BridgeError::InvalidAmountToTransfer.as_ref()
);
```

---

### Proof of Concept

**Setup:** Token registered with `origin_decimals = 24`, `decimals = 6` (18-decimal gap). Destination chain is EVM.

**Step 1:** User calls `ft_transfer_call` with `amount = 999_999_999_999_999_999` (just below `10^18`), `fee = 0`.

**Step 2:** `init_transfer` passes the `fee < amount` check. `init_transfer_internal` is called:
- `burn_tokens_if_needed` burns the user's `999_999_999_999_999_999` tokens (if bridged token), or `lock_tokens_if_needed` locks them.
- Transfer message stored in `pending_transfers`.
- Returns `U128(0)` — NEP-141 interprets this as "all tokens used", no refund.

**Step 3:** Relayer calls `sign_transfer`:
```
normalize_amount(999_999_999_999_999_999, Decimals { origin: 24, dest: 6 })
= 999_999_999_999_999_999 / 10^18
= 0
```
`require!(amount_to_transfer > 0, ...)` → **panics with `ERR_INVALID_AMOUNT_TO_TRANSFER`**.

**Step 4:** Every subsequent `sign_transfer` call for this transfer ID also panics. The transfer is permanently stuck. The user's tokens are gone.

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

**File:** near/omni-bridge/src/lib.rs (L1851-1857)
```rust
            self.burn_tokens_if_needed(token_id.clone(), transfer_message.amount);

            self.lock_tokens_if_needed(
                transfer_message.get_destination_chain(),
                &token_id,
                transfer_message.amount.0,
            );
```

**File:** near/omni-bridge/src/lib.rs (L2194-2211)
```rust
    fn remove_transfer_message(&mut self, transfer_id: TransferId) -> TransferMessage {
        let storage_usage = env::storage_usage();
        let transfer = self
            .pending_transfers
            .remove(&transfer_id)
            .map(storage::TransferMessageStorage::into_main)
            .near_expect(BridgeError::TransferNotExist);

        let refund =
            env::storage_byte_cost().saturating_mul((storage_usage - env::storage_usage()).into());

        if let Some(mut storage) = self.accounts_balances.get(&transfer.owner) {
            storage.available = storage.available.saturating_add(refund);
            self.accounts_balances.insert(&transfer.owner, &storage);
        }

        transfer.message
    }
```

**File:** near/omni-bridge/src/lib.rs (L2781-2783)
```rust
    /// Uses floor division — any sub-unit remainder ("dust") is truncated and not transferred
    /// to the destination chain. When fee > 0, dust is absorbed into the fee via `claim_fee`.
    /// When fee = 0, dust stays locked/burned. See SECURITY.md for details.
```

**File:** near/omni-bridge/src/lib.rs (L2784-2787)
```rust
    fn normalize_amount(amount: u128, decimals: Decimals) -> u128 {
        let diff_decimals: u32 = (decimals.origin_decimals - decimals.decimals).into();
        amount / (10_u128.pow(diff_decimals))
    }
```
