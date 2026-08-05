## Finding: Valid

The code confirms the described flaw in `AccountLocks::try_lock_transaction_batch`.

### Title
Intra-batch account lock conflicts are not detected, allowing simultaneous read and write locks on the same account - (File: `accounts-db/src/account_locks.rs`)

### Summary
`AccountLocks::try_lock_transaction_batch` validates every transaction in a batch against the lock table in a first pass, then applies all the actual locks in a second pass. Because the validation pass only reads `self` (`can_lock_accounts` takes `&self`) and never mutates it, all transactions in the batch are checked against the *same, pre-batch* snapshot of `write_locks`/`readonly_locks`. Locks acquired by earlier transactions in the batch are invisible to the validation of later transactions in the same batch, so two conflicting transactions (e.g. one that read-locks pubkey `P` and one that write-locks `P`) can both pass validation and then both be granted their locks in the second pass. [1](#0-0) 

### Finding Description
`try_lock_transaction_batch` is implemented as two independent loops:

1. A validation loop that calls `self.can_lock_accounts(keys)` for each transaction's keys. This call only reads `self.write_locks`/`self.readonly_locks` and does not mutate them. [2](#0-1) [3](#0-2) 

2. A locking loop that, for every entry that passed validation, calls `self.lock_accounts(keys)`, which mutates `write_locks`/`readonly_locks`. [4](#0-3) [5](#0-4) 

Since no mutation happens during the validation loop, the checks `can_read_lock`/`can_write_lock` for every transaction in the batch are all evaluated against the identical pre-batch state: [6](#0-5) 

Given a batch `[readable(P), writable(P)]`:
- Validation of tx A (read `P`): `can_read_lock(P)` → true (no existing write lock) → `Ok`.
- Validation of tx B (write `P`): `can_write_lock(P)` → true (no existing read/write lock, since tx A hasn't been locked yet) → `Ok`.
- Locking loop then applies both: `lock_readonly(P)` for A and `lock_write(P)` for B.

After this, `is_locked_readonly(&P)` and `is_locked_write(&P)` are both `true` simultaneously. [7](#0-6) 

This function is reached from `Accounts::lock_accounts`, which is the batch-locking entry point used by `Bank::try_lock_accounts_with_results` / `Bank::prepare_sanitized_batch_with_results`, i.e. the mechanism whose entire purpose is to guarantee that concurrently executed transactions (within one `TransactionBatch`) never touch the same account in conflicting ways. [8](#0-7) [9](#0-8) 

Once both locks are (incorrectly) granted, the runtime treats the two transactions as safe to execute in parallel (e.g., via the rayon-based parallel execution of a `TransactionBatch`). This breaks the mutual-exclusion invariant that account locking exists to enforce, permitting a writer transaction to mutate an account's lamport/data fields while a reader transaction concurrently observes it — a genuine torn-read/data race on shared account state.

### Impact Explanation
This breaks the fundamental invariant of the account-locking subsystem: that no two concurrently-executing transactions in the same batch may have conflicting (write vs. read/write) locks on the same account. If two transactions that reference the same account (one writable, one read-only) end up in the same `TransactionBatch`, they can be executed concurrently by the SVM's parallel execution path while one mutates the account (lamports/data) and the other reads it, producing torn/inconsistent state observations or corrupting shared bookkeeping in the accounts index/cache. This is a runtime/accounts correctness issue that can manifest as incorrect balance reads or corrupted account state during concurrent execution, which the code's own doc comment ("Lock accounts for all transactions in a batch which don't conflict with existing locks") explicitly says should not happen.

### Likelihood Explanation
The bug is deterministic given the code structure — it does not depend on timing or any privileged position. It triggers whenever a single call to `try_lock_transaction_batch` receives two transactions that reference the same pubkey with conflicting access modes (one read, one write) in the *same batch*, which is a realistic scenario for `Bank::prepare_sanitized_batch_with_results`/`Bank::try_lock_accounts` batches built from unsanitized ordering (e.g., entries during replay, or any batch construction path that does not itself perform intra-batch conflict pre-filtering before calling into `AccountLocks`).

### Recommendation
Restructure `try_lock_transaction_batch` so that validation and locking are interleaved per-transaction (check-then-lock immediately, not check-all-then-lock-all), so that each transaction's `can_lock_accounts` check observes locks already taken by earlier transactions within the same batch.

### Proof of Concept
```rust
// accounts-db/src/account_locks.rs
let mut locks = AccountLocks::default();
let p = Pubkey::new_unique();

let batch = vec![
    Ok(vec![(&p, false)].into_iter()), // tx A: read-lock P
    Ok(vec![(&p, true)].into_iter()),  // tx B: write-lock P
];

let results = locks.try_lock_transaction_batch(batch);
assert!(results[0].is_ok());
assert!(results[1].is_ok());
assert!(locks.is_locked_readonly(&p));
assert!(locks.is_locked_write(&p)); // both true simultaneously
``` [1](#0-0)

### Citations

**File:** accounts-db/src/account_locks.rs (L22-40)
```rust
    pub fn try_lock_transaction_batch<'a>(
        &mut self,
        mut validated_batch_keys: Vec<
            TransactionResult<impl Iterator<Item = (&'a Pubkey, bool)> + Clone>,
        >,
    ) -> Vec<TransactionResult<()>> {
        validated_batch_keys.iter_mut().for_each(|validated_keys| {
            if let Ok(keys) = validated_keys.as_ref()
                && let Err(e) = self.can_lock_accounts(keys.clone())
            {
                *validated_keys = Err(e);
            }
        });

        validated_batch_keys
            .into_iter()
            .map(|available_keys| available_keys.map(|keys| self.lock_accounts(keys)))
            .collect()
    }
```

**File:** accounts-db/src/account_locks.rs (L56-71)
```rust
    fn can_lock_accounts<'a>(
        &self,
        keys: impl Iterator<Item = (&'a Pubkey, bool)>,
    ) -> TransactionResult<()> {
        for (key, writable) in keys {
            if writable {
                if !self.can_write_lock(key) {
                    return Err(TransactionError::AccountInUse);
                }
            } else if !self.can_read_lock(key) {
                return Err(TransactionError::AccountInUse);
            }
        }

        Ok(())
    }
```

**File:** accounts-db/src/account_locks.rs (L73-81)
```rust
    fn lock_accounts<'a>(&mut self, keys: impl Iterator<Item = (&'a Pubkey, bool)>) {
        for (key, writable) in keys {
            if writable {
                self.lock_write(key);
            } else {
                self.lock_readonly(key);
            }
        }
    }
```

**File:** accounts-db/src/account_locks.rs (L84-91)
```rust
    fn is_locked_readonly(&self, key: &Pubkey) -> bool {
        self.readonly_locks.get(key).is_some_and(|count| *count > 0)
    }

    #[cfg_attr(feature = "dev-context-only-utils", qualifiers(pub))]
    fn is_locked_write(&self, key: &Pubkey) -> bool {
        self.write_locks.get(key).is_some_and(|count| *count > 0)
    }
```

**File:** accounts-db/src/account_locks.rs (L93-101)
```rust
    fn can_read_lock(&self, key: &Pubkey) -> bool {
        // If the key is not write-locked, it can be read-locked
        !self.is_locked_write(key)
    }

    fn can_write_lock(&self, key: &Pubkey) -> bool {
        // If the key is not read-locked or write-locked, it can be write-locked
        !self.is_locked_readonly(key) && !self.is_locked_write(key)
    }
```

**File:** accounts-db/src/accounts.rs (L452-474)
```rust
    /// This function will prevent multiple threads from modifying the same account state at the
    /// same time, possibly excluding transactions based on prior results
    #[must_use]
    pub fn lock_accounts<'a>(
        &self,
        txs: impl Iterator<Item = &'a (impl SVMMessage + 'a)>,
        results: impl Iterator<Item = Result<()>>,
        tx_account_lock_limit: usize,
    ) -> Vec<Result<()>> {
        // Validate the account locks, then get keys and is_writable if successful validation.
        // We collect to fully evaluate before taking the account_locks mutex.
        let validated_batch_keys = txs
            .zip(results)
            .map(|(tx, result)| {
                result
                    .and_then(|_| validate_account_locks(tx.account_keys(), tx_account_lock_limit))
                    .map(|_| TransactionAccountLocksIterator::new(tx).accounts_with_is_writable())
            })
            .collect::<Vec<_>>();

        let account_locks = &mut self.account_locks.lock().unwrap();
        account_locks.try_lock_transaction_batch(validated_batch_keys)
    }
```

**File:** runtime/src/bank.rs (L3733-3744)
```rust
    pub fn prepare_sanitized_batch_with_results<'a, 'b, Tx: TransactionWithMeta>(
        &'a self,
        transactions: &'b [Tx],
        transaction_results: impl Iterator<Item = Result<()>>,
    ) -> TransactionBatch<'a, 'b, Tx> {
        // this lock_results could be: Ok, AccountInUse, WouldExceedBlockMaxLimit or WouldExceedAccountMaxLimit
        TransactionBatch::new(
            self.try_lock_accounts_with_results(transactions, transaction_results),
            self,
            OwnedOrBorrowed::Borrowed(transactions),
        )
    }
```
