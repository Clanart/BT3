Confirmed: `create_lookup_table(authority, payer, recent_slot)` derives the lookup-table address as a PDA from `[authority, recent_slot]` via `create_program_address` — the address is fully deterministic and depends only on caller-controlled inputs, not on any global nonce. This confirms the address-reuse primitive underlying the finding below: an authority can close a table at address `A` and later recreate a *different* table that resolves to the *same* address `A` (by reusing the same `recent_slot`), because nothing in the runtime binds the account key to a single lookup-table "identity" over time.

### Title
Stale, discarded ALT re-resolution lets banking-stage execute a transaction with addresses that no longer match a fresh lookup — ([File: runtime/src/bank.rs])

### Summary
The wallet report's broken invariant is: *the value a caller validated is not the value that gets executed/signed.* The direct Agave analog is `Bank::resanitize_transaction_minimally` [1](#0-0) , which is the only re-validation step performed on a buffered/aged transaction before commit in `Consumer::process_and_record_aged_transactions` [2](#0-1) . When `self.slot() > alt_invalidation_slot`, it calls `load_addresses_from_ref` again but **discards the freshly resolved addresses** (`let (_addresses, _deactivation_slot) = ...`) and only checks that resolution didn't error [3](#0-2) . The transaction that is actually locked, executed, and committed still carries the addresses captured earlier in `translate_to_runtime_view`/`load_addresses_for_view` at ingestion time [4](#0-3) .

### Finding Description
Banking-stage resolves ALT addresses once at ingestion (`load_addresses_for_view`, using the *root* bank at that moment) and embeds them into the `RuntimeTransaction`/`ResolvedTransactionView`, along with a `MaxAge{ sanitized_epoch, alt_invalidation_slot }` computed from the ALT's deactivation slot [5](#0-4) . The transaction can then sit in the scheduler/container for a while before being consumed on a later, different bank.

Before commit, `process_and_record_aged_transactions` calls `resanitize_transaction_minimally` per transaction [2](#0-1) . That function's only ALT-related safety check is:

```rust
if self.slot() > alt_invalidation_slot {
    let (_addresses, _deactivation_slot) =
        self.load_addresses_from_ref(transaction.message_address_table_lookups())?;
}
``` [3](#0-2) 

This re-resolves the table purely to see if it *still resolves* (propagating `AddressLoaderError` on failure) — it never compares the freshly-resolved addresses to the ones already baked into the transaction, and it never substitutes them. The transaction handed to `prepare_sanitized_batch_with_results`/execution keeps the addresses resolved at ingestion time [6](#0-5) .

Address Lookup Table account keys are not permanently bound to one logical table: `create_lookup_table(authority, payer, recent_slot)` derives the account address as `create_program_address([authority.as_ref(), recent_slot.to_le_bytes().as_ref()], &program_id)` — fully deterministic from caller-supplied `authority` and `recent_slot`, with no global uniqueness enforcement beyond "the derived account doesn't already exist" [7](#0-6) . Once a table is deactivated and then closed via `close_lookup_table` (which reclaims the account entirely) [8](#0-7) , the same authority can immediately re-issue `create_lookup_table` with the *same* `recent_slot` value, producing a table at the *identical* address but with entirely different `addresses` content at the same indexes.

Given this, the exploitable sequence is:
1. A buffered v0 transaction resolves ALT index 0 at table key `A` to address `X` at ingestion time (root bank slot S1); `alt_invalidation_slot` is set to an estimated-safe future slot based on the deactivation slot heuristic (`estimate_last_valid_slot`).
2. Before the transaction is consumed, the table's authority deactivates and closes table `A`, then recreates a new table at the same address `A` (same authority + same `recent_slot`) with index 0 now containing address `Y`.
3. At consumption time, `resanitize_transaction_minimally` re-resolves lookups against table `A` — this succeeds because a valid table again exists at `A` — but the resolved value (`Y`) is discarded; the transaction still executes against the stale, ingestion-time address `X`.

This exactly mirrors the report's pattern: the value that was validated/checked (fresh resolution = `Y`, and the "it still resolves" pass/fail signal) is not the value that is actually used for execution/commitment (`X`). The wallet bug was "populate silently overrides confirmed data"; the Agave analog is "resanitize silently keeps stale data even though a fresh, possibly different, resolution was just computed and thrown away."

### Impact Explanation
If exploited, a transaction could execute against a writable/readonly account address that differs from what the current, live state of the referenced lookup table would produce — e.g., a transfer destination resolved from ALT index 0 pointing to a stale address `X` instead of the currently-correct `Y`. This can misdirect fund transfers or CPI account bindings away from what the current on-chain ALT state (and thus what a fresh client-side simulation) would indicate, i.e., false execution against unintended accounts. Because this affects consensus-critical, unprivileged transaction execution paths in the leader's banking stage, it falls in the "false execution/acceptance" impact category.

### Likelihood Explanation
Likelihood is constrained by several factors I could not fully verify with available tooling: (1) whether `create_lookup_table`'s underlying account-creation path actually permits reusing an already-derived-but-now-closed address with a *different* `recent_slot`-encoded PDA for a *different* content set within the narrow window that a specific transaction remains buffered past its `alt_invalidation_slot` estimate; (2) the exact preconditions under which `self.slot() > alt_invalidation_slot` is reached for a still-pending transaction, since `calculate_max_age`/`estimate_last_valid_slot` are designed to bound this window using `slot_hashes` history [9](#0-8) . Exploiting this requires an attacker who controls the ALT's authority (i.e., controls the destination/CPI accounts referenced by the ALT) racing a specific pending transaction — this is a real, unprivileged-attacker-controlled action (not a "malicious validator/peer" assumption), but it requires timing precision and a victim transaction that stays unconfirmed long enough to cross the `alt_invalidation_slot` boundary.

### Recommendation
- **Short term:** In `resanitize_transaction_minimally`, when addresses are re-resolved after crossing `alt_invalidation_slot`, compare the freshly loaded `LoadedAddresses` against the addresses already embedded in the transaction and reject (`TransactionError::SanitizeFailure` or a dedicated `AddressLookupTableMismatch` error) if they differ, instead of discarding the result.
- **Long term:** Consider re-deriving/rebuilding the `SanitizedTransaction`'s account keys from the freshly resolved addresses rather than only validating resolution succeeded, so that "the addresses used for execution" and "the addresses most recently confirmed as valid" are provably the same value, closing the class of bug the external report describes (displayed/validated data diverging from executed data).

### Proof of Concept
Conceptual PoC (not runnable without a live cluster/authority key, and I could not fully verify PDA-reuse timing constraints from local code alone):
1. Attacker creates ALT table `T` at authority `Auth`, `recent_slot = R`, with `addresses[0] = X`.
2. Victim submits a v0 transaction referencing `T` index 0; banking stage resolves it to `X` and buffers it with `alt_invalidation_slot` computed from `T`'s (initially `u64::MAX`, i.e., not yet deactivated) deactivation slot.
3. Attacker calls `deactivate_lookup_table(T, Auth)` then, once the deactivation cooldown elapses, `close_lookup_table(T, Auth, recipient)`.
4. Attacker calls `create_lookup_table(Auth, payer, R)` again — deriving the same address `T` — and `extend_lookup_table(T, Auth, ..., [Y])`, so index 0 now resolves to `Y`.
5. If the victim's transaction is still buffered and now hits the `self.slot() > alt_invalidation_slot` branch in `resanitize_transaction_minimally`, the fresh resolution (`Y`) is computed and discarded; the transaction is still executed with the original stale value `X`, not the currently valid `Y` reflected on-chain and in the ALT program's public state.

### Citations

**File:** runtime/src/bank.rs (L3723-3744)
```rust
    /// Prepare a locked transaction batch from a list of sanitized transactions.
    pub fn prepare_sanitized_batch<'a, 'b, Tx: TransactionWithMeta>(
        &'a self,
        txs: &'b [Tx],
    ) -> TransactionBatch<'a, 'b, Tx> {
        self.prepare_sanitized_batch_with_results(txs, txs.iter().map(|_| Ok(())))
    }

    /// Prepare a locked transaction batch from a list of sanitized transactions, and their cost
    /// limited packing status
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

**File:** runtime/src/bank.rs (L3770-3807)
```rust
    pub fn resanitize_transaction_minimally(
        &self,
        transaction: &impl TransactionWithMeta,
        sanitized_epoch: Epoch,
        alt_invalidation_slot: Slot,
    ) -> Result<()> {
        if self.vote_only_bank() && !vote_parser::is_valid_vote_only_transaction(transaction) {
            return Err(TransactionError::SanitizeFailure);
        }

        // If the transaction was sanitized before this bank's epoch,
        // additional checks are necessary.
        if self.epoch() != sanitized_epoch {
            // Reserved key set may have changed, so we must verify that
            // no writable keys are reserved.
            self.check_reserved_keys(transaction)?;

            for instr in transaction.instructions_iter() {
                if instr.accounts.len() > solana_transaction_context::MAX_ACCOUNTS_PER_INSTRUCTION {
                    return Err(solana_transaction_error::TransactionError::SanitizeFailure);
                }
            }
        }

        if self.slot() > alt_invalidation_slot {
            // The address table lookup **may** have expired, but the
            // expiration is not guaranteed since there may have been
            // skipped slot.
            // If the addresses still resolve here, then the transaction is still
            // valid, and we can continue with processing.
            // If they do not, then the ATL has expired and the transaction
            // can be dropped.
            let (_addresses, _deactivation_slot) =
                self.load_addresses_from_ref(transaction.message_address_table_lookups())?;
        }

        Ok(())
    }
```

**File:** core/src/banking_stage/consumer.rs (L179-197)
```rust
    pub fn process_and_record_aged_transactions(
        &self,
        bank: &Bank,
        txs: &[impl TransactionWithMeta],
        max_ages: &[MaxAge],
        flags: &ExecutionFlags,
    ) -> ProcessTransactionBatchOutput {
        // Need to filter out transactions since they were sanitized earlier.
        // This means that the transaction may cross and epoch boundary (not allowed),
        //  or account lookup tables may have been closed.
        let pre_results = txs.iter().zip(max_ages).map(|(tx, max_age)| {
            bank.resanitize_transaction_minimally(
                tx,
                max_age.sanitized_epoch,
                max_age.alt_invalidation_slot,
            )
        });
        self.process_and_record_transactions_with_pre_results(bank, txs, pre_results, flags)
    }
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L411-472)
```rust
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

/// Load addresses from ALTs (if necessary) and return the
/// [`LoadedAddresses`] with the minimum deactivation slot.
pub(crate) fn load_addresses_for_view<D: TransactionData>(
    view: &SanitizedTransactionView<D>,
    bank: &Bank,
) -> Result<(Option<LoadedAddresses>, Slot), PacketHandlingError> {
    match view.version() {
        TransactionVersion::Legacy | TransactionVersion::V1 => Ok((None, u64::MAX)),
        TransactionVersion::V0 => bank
            .load_addresses_from_ref(view.address_table_lookup_iter())
            .map(|(loaded_addresses, deactivation_slot)| {
                (Some(loaded_addresses), deactivation_slot)
            })
            .map_err(|_| PacketHandlingError::ALTResolution),
    }
}
```

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L474-499)
```rust
/// Given the epoch, the minimum deactivation slot, and the current slot,
/// return the `MaxAge` that should be used for the transaction. This is used
/// to determine the maximum slot that a transaction will be considered valid
/// for, without re-resolving addresses or resanitizing.
///
/// This function considers the deactivation period of Address Table
/// accounts. If the deactivation period runs past the end of the epoch,
/// then the transaction is considered valid until the end of the epoch.
/// Otherwise, the transaction is considered valid until the deactivation
/// period.
///
/// Since the deactivation period technically uses blocks rather than
/// slots, the value used here is the lower-bound on the deactivation
/// period, i.e. the transaction's address lookups are valid until
/// AT LEAST this slot.
fn calculate_max_age(
    sanitized_epoch: Epoch,
    deactivation_slot: Slot,
    current_slot: Slot,
) -> MaxAge {
    let alt_min_expire_slot = estimate_last_valid_slot(deactivation_slot.min(current_slot));
    MaxAge {
        sanitized_epoch,
        alt_invalidation_slot: alt_min_expire_slot,
    }
}
```

**File:** transaction-status/src/parse_address_lookup_table.rs (L121-148)
```rust
    #[test]
    fn test_parse_create_address_lookup_table_ix() {
        let from_pubkey = Pubkey::new_unique();
        // use explicit key to have predictable bump_seed
        let authority = Pubkey::from_str("HkxY6vXdrKzoCQLmdJ3cYo9534FdZQxzBNWTyrJzzqJM").unwrap();
        let slot = 42;

        let (instruction, lookup_table_pubkey) =
            instruction::create_lookup_table(authority, from_pubkey, slot);
        let mut message = Message::new(&[instruction], None);
        assert_eq!(
            parse_address_lookup_table(
                &message.instructions[0],
                &AccountKeys::new(&message.account_keys, None)
            )
            .unwrap(),
            ParsedInstructionEnum {
                instruction_type: "createLookupTable".to_string(),
                info: json!({
                    "lookupTableAccount": lookup_table_pubkey.to_string(),
                    "lookupTableAuthority": authority.to_string(),
                    "payerAccount": from_pubkey.to_string(),
                    "systemProgram": system_program::id().to_string(),
                    "recentSlot": slot,
                    "bumpSeed": 254,
                }),
            }
        );
```

**File:** cli/src/address_lookup_table.rs (L786-846)
```rust
async fn process_close_lookup_table(
    rpc_client: &RpcClient,
    config: &CliConfig<'_>,
    lookup_table_pubkey: Pubkey,
    authority_signer_index: usize,
    recipient_pubkey: Pubkey,
) -> ProcessResult {
    let authority_signer = config.signers[authority_signer_index];

    let get_lookup_table_result = rpc_client
        .get_account_with_commitment(&lookup_table_pubkey, config.commitment)
        .await?;
    let lookup_table_account = get_lookup_table_result.value.ok_or_else(|| {
        format!("Lookup table account {lookup_table_pubkey} not found, was it already closed?")
    })?;
    if !address_lookup_table::program::check_id(&lookup_table_account.owner) {
        return Err(format!(
            "Lookup table account {lookup_table_pubkey} is not owned by the Address Lookup Table \
             program",
        )
        .into());
    }

    let lookup_table_account = AddressLookupTable::deserialize(&lookup_table_account.data)?;
    if lookup_table_account.meta.deactivation_slot == u64::MAX {
        return Err(format!(
            "Lookup table account {lookup_table_pubkey} is not deactivated. Only deactivated \
             lookup tables may be closed",
        )
        .into());
    }

    let authority_address = authority_signer.pubkey();
    let close_lookup_table_ix =
        close_lookup_table(lookup_table_pubkey, authority_address, recipient_pubkey);

    let blockhash = rpc_client.get_latest_blockhash().await?;
    let mut tx = Transaction::new_unsigned(Message::new(
        &[close_lookup_table_ix],
        Some(&config.signers[0].pubkey()),
    ));

    tx.try_sign(&[config.signers[0], authority_signer], blockhash)?;
    let result = rpc_client
        .send_and_confirm_transaction_with_spinner_and_config(
            &tx,
            config.commitment,
            RpcSendTransactionConfig {
                skip_preflight: false,
                preflight_commitment: Some(config.commitment.commitment),
                ..RpcSendTransactionConfig::default()
            },
        )
        .await;
    match result {
        Err(err) => Err(format!("Close failed: {err}").into()),
        Ok(signature) => Ok(config.output_format.formatted_string(&CliSignature {
            signature: signature.to_string(),
        })),
    }
}
```
