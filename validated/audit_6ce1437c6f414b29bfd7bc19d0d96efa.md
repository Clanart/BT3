### Title
Incomplete Balance Update in `try_to_transfer_balance_from_message_account` Permanently Locks Native Fee Funds — (File: near/omni-bridge/src/storage.rs)

---

### Summary

In `near/omni-bridge/src/storage.rs`, the function `try_to_transfer_balance_from_message_account` credits the message account's balance to the `storage_payer`'s `available` field but **never updates `storage_payer.total`**. This breaks the invariant `total >= available`, causing the credited native fee to be permanently unwithdrawable — irrecoverably locked in the bridge.

---

### Finding Description

The function is documented as: *"Used when native fee for the transfer is deposited to the dedicated message account. Deducts the total balance from `account_id` and credits it to `storage_payer`."*

The relevant code path is:

```rust
// near/omni-bridge/src/storage.rs lines 281–288
storage.available = storage.available.saturating_add(balance.total);

if storage.available < required_storage_payer_balance.saturating_add(native_fee) {
    return Err(StorageError::SignerNotEnoughBalance);
}

self.accounts_balances.insert(storage_payer, &storage);
self.accounts_balances.remove(account_id);
```

Only `storage.available` is increased; `storage.total` is left unchanged. Compare this to `storage_deposit`, which correctly updates **both** fields together:

```rust
// near/omni-bridge/src/storage.rs lines 159–161
storage.total = storage.total.saturating_add(amount);
storage.available = storage.available.saturating_add(amount);
```

After `try_to_transfer_balance_from_message_account` runs, the storage_payer's state is:
- `total = T` (original, unchanged)
- `available = A + balance.total` (increased, where `A + balance.total > T` is possible)

This violates the invariant `total >= available`.

When the storage_payer subsequently calls `storage_withdraw` (lines 187–211):

```rust
let to_withdraw = amount.unwrap_or(storage.available);
storage.total = storage.total.checked_sub(to_withdraw).near_expect(
    StorageError::NotEnoughStorageBalance { ... }
);
```

If `to_withdraw = storage.available > storage.total`, `checked_sub` returns `None` and the call panics/reverts. The credited native fee amount (`balance.total - (T - A)`) is permanently unwithdrawable — irrecoverably locked in the bridge contract.

The analog to the DYAD bug is exact: in DYAD, the wrong collateral category's value was used in the withdrawal check, blocking legitimate withdrawals. Here, the wrong balance field (`available` only, not `total`) is updated during a credit operation, causing the same class of outcome — a legitimate withdrawal is permanently blocked due to an invalid accounting state.

---

### Impact Explanation

**High — Permanent freezing / irrecoverable lock of protocol funds in bridge flows.**

The native fee (NEAR tokens) deposited by the user to the message account and intended to be paid to the relayer/storage_payer is permanently locked inside the bridge contract. The relayer cannot withdraw the credited amount. The message account is deleted (`accounts_balances.remove(account_id)`), so the funds cannot be recovered from either side. This matches the allowed impact: *"Permanent freezing, irrecoverable lock, or unclaimable settlement of user or protocol funds in bridge, token, or vault flows."*

---

### Likelihood Explanation

**Medium.** This is triggered in the native-fee pre-deposit flow, which is a standard bridge path. Any relayer finalizing a transfer where the sender pre-deposited a native fee will have their fee permanently locked. The condition `A + balance.total > T` is almost always true in practice because the storage_payer's `available` is typically much less than `total` (storage is consumed), so adding `balance.total` to `available` will exceed `total`.

---

### Recommendation

Update **both** `storage.total` and `storage.available` when crediting the storage_payer, consistent with how `storage_deposit` operates:

```rust
// Fix: mirror the storage_deposit pattern
storage.total = storage.total.saturating_add(balance.total);
storage.available = storage.available.saturating_add(balance.total);
```

This restores the `total >= available` invariant and allows the storage_payer to withdraw the credited native fee.

---

### Proof of Concept

1. User initiates a cross-chain transfer and pre-deposits a native fee (e.g., 1 NEAR) to a dedicated message account. The message account has `total = 1 NEAR`, `available = 1 NEAR`.
2. Relayer (storage_payer) has existing storage balance: `total = 0.1 NEAR`, `available = 0.01 NEAR`.
3. Relayer finalizes the transfer; `try_to_transfer_balance_from_message_account` is called.
4. After the call: storage_payer has `total = 0.1 NEAR` (unchanged), `available = 0.01 + 1 = 1.01 NEAR`. The message account is deleted.
5. Relayer calls `storage_withdraw(None)` to claim their fee.
6. `to_withdraw = storage.available = 1.01 NEAR`.
7. `storage.total.checked_sub(1.01 NEAR)` = `0.1 - 1.01` → underflow → `None` → panic.
8. The 1 NEAR native fee is permanently locked. The relayer can only withdraw up to `0.1 NEAR` (their original `total`), losing the entire credited native fee. [1](#0-0) [2](#0-1) [3](#0-2)

### Citations

**File:** near/omni-bridge/src/storage.rs (L158-162)
```rust
            |mut storage| {
                storage.total = storage.total.saturating_add(amount);
                storage.available = storage.available.saturating_add(amount);
                storage
            },
```

**File:** near/omni-bridge/src/storage.rs (L187-211)
```rust
    pub fn storage_withdraw(&mut self, amount: Option<NearToken>) -> StorageBalance {
        assert_one_yocto();
        let account_id = env::predecessor_account_id();
        let mut storage = self
            .storage_balance_of(&account_id)
            .near_expect(StorageError::AccountNotRegistered(account_id.clone()));
        let to_withdraw = amount.unwrap_or(storage.available);
        storage.total = storage.total.checked_sub(to_withdraw).near_expect(
            StorageError::NotEnoughStorageBalance {
                requested: to_withdraw,
                available: storage.total,
            },
        );
        storage.available = storage.available.checked_sub(to_withdraw).near_expect(
            StorageError::NotEnoughStorageBalance {
                requested: to_withdraw,
                available: storage.available,
            },
        );

        self.accounts_balances.insert(&account_id, &storage);

        Promise::new(account_id).transfer(to_withdraw).detach();

        storage
```

**File:** near/omni-bridge/src/storage.rs (L260-290)
```rust
    pub(crate) fn try_to_transfer_balance_from_message_account(
        &mut self,
        account_id: &AccountId,
        native_fee: NearToken,
        storage_payer: &AccountId,
        required_storage_payer_balance: NearToken,
    ) -> Result<(), StorageError> {
        let balance = self
            .accounts_balances
            .get(account_id)
            .ok_or(StorageError::MessageAccountNotRegistered)?;

        if balance.total < native_fee {
            return Err(StorageError::NotEnoughBalanceForFee);
        }

        let mut storage = self
            .accounts_balances
            .get(storage_payer)
            .ok_or(StorageError::SignerNotRegistered)?;

        storage.available = storage.available.saturating_add(balance.total);

        if storage.available < required_storage_payer_balance.saturating_add(native_fee) {
            return Err(StorageError::SignerNotEnoughBalance);
        }

        self.accounts_balances.insert(storage_payer, &storage);
        self.accounts_balances.remove(account_id);
        Ok(())
    }
```
