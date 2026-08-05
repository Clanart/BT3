No vulnerability found for this question. The premise of a lock-vs-execution divergence does not hold in this codebase.

**Why the invariant holds:**

`Bank::verify_transaction_with_serialized_message` builds the sanitized transaction via `RuntimeTransaction::try_create` → `SanitizedTransaction::try_new`, passing the bank itself as the `AddressLoader`. [1](#0-0) 

Address-table lookups are resolved into a single `AccountKeys` view (static keys + ALT-loaded writable + ALT-loaded readonly) *before* duplicate detection is performed — the duplicate check in `validate_account_locks`/`has_duplicates` operates on this fully-resolved `AccountKeys`, which is exactly why the code comment in the banking-stage view-based path explicitly states duplicate validation "must be after resolution to catch ALT duplicates." [2](#0-1) [3](#0-2) 

Once sanitized, the transaction carries this single resolved `AccountKeys`/`is_writable` view for its entire lifetime — `Accounts::lock_accounts` re-derives lock keys from `tx.account_keys()` and re-runs `validate_account_locks` right before actually taking locks, using the identical accessor that `SVMMessage`/`SanitizedTransaction` exposes to execution and commit. [4](#0-3) 

The unified scheduler path likewise builds its lock contexts directly from `transaction.message().account_keys()`/`is_writable`, i.e., the same canonical post-sanitize view, and the code explicitly documents that `validate_account_locks` is protocol-consensus-critical precisely because duplicate-address transactions can't be safely handled by the scheduler otherwise. [5](#0-4) 

For the transaction-view/TPU packet path used by banking stage, the same pattern is followed: `ResolvedTransactionView` resolves ALT addresses first, then `validate_account_locks` is invoked on the resolved `account_keys()`. [6](#0-5) 

There is no separate "lock-time" account list computed independently from the "execution/commit-time" list — both derive from the same immutable, already-ALT-resolved `SanitizedTransaction`/`ResolvedTransactionView` object created once at sanitization. Since the duplicate check happens strictly after ALT resolution and operates on the same combined key list that lock-taking and execution consume, there is no way for an attacker to craft a transaction where the writable/readonly alias set differs between lock acquisition and commit — any duplicate (static-static, static-ALT, or ALT-ALT) is rejected with `TransactionError::AccountLoadedTwice` before locks are ever taken.

### Citations

**File:** runtime/src/bank.rs (L5563-5571)
```rust
            };

            RuntimeTransaction::try_create(
                tx,
                MessageHash::Precomputed(message_hash),
                None,
                self,
                self.get_reserved_account_keys(),
            )
```

**File:** accounts-db/src/account_locks.rs (L142-154)
```rust
/// Validate account locks before locking.
pub fn validate_account_locks(
    account_keys: AccountKeys,
    tx_account_lock_limit: usize,
) -> TransactionResult<()> {
    if account_keys.len() > tx_account_lock_limit {
        Err(TransactionError::TooManyAccountLocks)
    } else if has_duplicates(account_keys) {
        Err(TransactionError::AccountLoadedTwice)
    } else {
        Ok(())
    }
}
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L408-455)
```rust
/// Perform sanitization checks and transition from data to an executable
/// [`RuntimeTransaction`]. This additionally returns the minimum slot for
/// ALT deactivation, if any. If no minimum slot, Slot::MAX is returned.
pub(crate) fn translate_to_runtime_view<D: TransactionData>(
    data: D,
    bank: &Bank,
    transaction_account_lock_limit: usize,
    sanitize_config: &SanitizeConfig,
) -> Result<(RuntimeTransaction<ResolvedTransactionView<D>>, u64), PacketHandlingError> {
    // Parsing and basic sanitization checks
    let Ok(view) = SanitizedTransactionView::try_new_sanitized(data, sanitize_config) else {
        return Err(PacketHandlingError::Sanitization);
    };

    let Ok(view) = RuntimeTransaction::<SanitizedTransactionView<_>>::try_new(
        view,
        MessageHash::Compute,
        None,
    ) else {
        return Err(PacketHandlingError::Sanitization);
    };

    // Discard non-vote packets if in vote-only mode.
    if bank.vote_only_bank() && !view.is_simple_vote_transaction() {
        return Err(PacketHandlingError::Sanitization);
    }

    if usize::from(view.total_num_accounts()) > transaction_account_lock_limit {
        return Err(PacketHandlingError::LockValidation);
    }

    let (loaded_addresses, deactivation_slot) = load_addresses_for_view(&view, bank)?;

    let Ok(view) = RuntimeTransaction::<ResolvedTransactionView<_>>::try_new(
        view,
        loaded_addresses,
        bank.get_reserved_account_keys(),
    ) else {
        return Err(PacketHandlingError::Sanitization);
    };

    // Validate no duplicate accounts (must be after resolution to catch ALT duplicates)
    if validate_account_locks(view.account_keys(), transaction_account_lock_limit).is_err() {
        return Err(PacketHandlingError::LockValidation);
    }

    Ok((view, deactivation_slot))
}
```

**File:** accounts-db/src/accounts.rs (L455-474)
```rust
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

**File:** unified-scheduler-logic/src/lib.rs (L1332-1358)
```rust
        // It's crucial for tasks to be validated with
        // `account_locks::validate_account_locks()` prior to the creation.
        // That's because it's part of protocol consensus regarding the
        // rejection of blocks containing malformed transactions
        // (`AccountLoadedTwice` and `TooManyAccountLocks`). Even more,
        // `SchedulingStateMachine` can't properly handle transactions with
        // duplicate addresses (those falling under `AccountLoadedTwice`).
        //
        // However, it's okay for now not to call `::validate_account_locks()`
        // here.
        //
        // Currently `replay_stage` is always calling
        //`::validate_account_locks()` regardless of whether unified-scheduler
        // is enabled or not at the blockstore
        // (`Bank::prepare_sanitized_batch()` is called in
        // `process_entries()`).
        //
        // As for `banking_stage` with unified scheduler, it will need to run
        // `validate_account_locks()` at least once somewhere in the code path.
        // In the distant future, this function (`create_task()`) should be
        // adjusted so that both stages do the checks before calling this or do
        // the checks here, to simplify the two code paths regarding the
        // essential `validate_account_locks` validation.
        //
        // Lastly, `validate_account_locks()` is currently called in
        // `DefaultTransactionHandler::handle()` via
        // `Bank::prepare_unlocked_batch_from_single_tx()` as well.
```
