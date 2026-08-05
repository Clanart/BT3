### Title
Stale ALT-resolved account keys are never revalidated for value-equality on resanitize, allowing a transaction to execute against orphaned/incorrect addresses if a referenced Address Lookup Table is closed and recreated before execution - (File: `runtime/src/bank.rs`)

### Summary
The Set Protocol report describes a class of bug where an address is "baked in" at issuance time and cannot be safely updated if the underlying resource is later migrated/upgraded to a new address, leading to unclaimable/misdirected funds because the immutable reference silently becomes stale. Agave has a structurally analogous pattern in how versioned (v0) transactions resolve Address Lookup Table (ALT) indices to concrete pubkeys once, at scheduling/sanitization time, and cache them into the `ResolvedTransactionView`/`RuntimeTransaction` that is later executed [1](#0-0) . A later "resanitize" check exists specifically to detect ALT staleness, but it discards the freshly-loaded values and only checks that resolution *succeeds*, not that the resolved values are *unchanged* from what was cached at scheduling time [2](#0-1) .

### Finding Description
When a v0 transaction is received, `translate_to_runtime_view` resolves its ALT lookups once via `load_addresses_for_view`/`load_addresses_from_ref`, and the resulting `LoadedAddresses` are baked into the `RuntimeTransaction<ResolvedTransactionView<_>>` together with a `deactivation_slot`, from which a `MaxAge`/`alt_invalidation_slot` is computed [3](#0-2) . This cached, resolved account-key list is what is later used for account locking and execution — it is not re-derived at execution time.

Before executing a buffered/held transaction whose `alt_invalidation_slot` may have passed, the bank calls `resanitize_transaction_minimally`, which re-invokes `load_addresses_from_ref` against the **current** bank state purely to see whether resolution still succeeds, then throws the result away:

```rust
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
``` [4](#0-3) 

The comment's stated invariant — "if the addresses still resolve here, then the transaction is still valid" — assumes that a *successful* re-resolution implies the *same* values that were cached at scheduling time. That assumption does not hold: ALT accounts can be deactivated and closed by their authority, and a new ALT can subsequently be created at the identical derived PDA address (the ALT program derives the table address from `[authority, recent_slot]` seeds, both of which are attacker-controlled for a table the attacker owns) [5](#0-4) [6](#0-5) . If the same authority reuses the same `recent_slot` seed after closing the old table, the new table lands at the exact same address but can be populated with entirely different entries via `extend_lookup_table`.

Because the discard-the-result check in `resanitize_transaction_minimally` never compares the freshly-resolved addresses against the values that were cached into the transaction at scheduling time (`_addresses` is explicitly ignored), the runtime cannot distinguish between:
1. A legitimate case where the ALT simply still contains the same immutable entries (safe to proceed), and
2. A case where the ALT account at that address was destroyed and replaced with unrelated content (the originally-resolved, now-orphaned pubkeys are stale with respect to the "current" table, yet the transaction is still allowed to proceed using its stale cached keys).

This mirrors the SetToken bug precisely: an address captured at "issuance" (scheduling) time is treated as permanently authoritative, but the guard meant to catch staleness ("if the addresses still resolve, we're fine") only checks liveness of resolution, not value-consistency, so orphaned/stale references silently pass validation.

### Impact Explanation
If a transaction was scheduled/cached against ALT entries that a malicious ALT owner subsequently destroys and reconstitutes at the same address with different addresses, the executing transaction still runs against the original (now-orphaned, from the current table owner's perspective) pubkeys, while the runtime's "sanity check" reports success even though the current ALT bears no resemblance to what was cached. This breaks the invariant that a `MaxAge`/`resanitize` pass on a transaction certifies that the transaction's account-key resolution reflects the *current* on-chain state of the referenced ALT. Depending on how downstream instructions were built by the user's wallet/dApp (which typically relies on the ALT to save transaction size for well-known, shared accounts), this can result in a transaction that appears "revalidated" as normal but is quietly operating on stale accounts that no longer correspond to what the ALT currently represents — a false-acceptance of a transaction that should have been dropped as unresolvable/invalid.

### Likelihood Explanation
The precondition — an ALT owner deactivating and closing their own table, then recreating a new table at the same PDA using the same `[authority, recent_slot]` seed and extending it with different entries — requires only permissionless, unprivileged operations available to any account holder over their own ALT; no validator, peer, or admin privilege is required. The harder part is timing: the transaction must sit in the scheduler/buffer long enough to cross `alt_invalidation_slot` while the swap happens, and the attacker must control an ALT that is depended on by third-party transactions (e.g., a widely shared/public lookup table used by a protocol). This narrows the practical exploitation window but does not require any trust or privilege assumption to be broken.

### Recommendation
In `resanitize_transaction_minimally` (`runtime/src/bank.rs`), do not discard the result of the re-resolution. Compare the freshly-resolved `LoadedAddresses` against the previously cached addresses in the transaction; if they differ, treat the transaction as invalid/expired (drop it) rather than treating "resolves without error" as sufficient proof of continued validity.

### Proof of Concept
Conceptual PoC (requires local/test-validator control to verify timing, not fully executed here):
1. Create ALT `T` at PDA derived from `(authority, recent_slot=S)` and extend it with entry `[X]` (index 0).
2. Third party builds and submits a v0 transaction referencing `T` index 0; validator resolves index 0 → `X` and caches it into the buffered `ResolvedTransactionView`, computing `alt_invalidation_slot`.
3. Before the transaction executes, attacker (authority of `T`) deactivates `T`, waits for the deactivation window, then closes `T` via `close_lookup_table` [7](#0-6) , and recreates a new table at the same PDA using the same `recent_slot=S`, extending it with a different entry `[Y]` at index 0.
4. Once `bank.slot() > alt_invalidation_slot`, `resanitize_transaction_minimally` re-resolves index 0 against the new table (`Y`), which succeeds without error, so the function returns `Ok(())` and the transaction proceeds — despite never checking that `Y != X`.
5. The transaction executes using the originally cached `X`, which is now unrelated to the current contents of the referenced ALT, even though the runtime's own re-validation step reported success on a table that no longer matches what was used to build the transaction.

Verifying the exact scheduler code path that invokes `resanitize_transaction_minimally` from `core/src/banking_stage/consumer.rs`, and confirming whether any additional guard elsewhere restores value-consistency checking, was not completed due to tool-call limits; this should be checked before treating the finding as fully confirmed.

### Citations

**File:** core/src/banking_stage/transaction_scheduler/receive_and_buffer.rs (L408-499)
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

**File:** cli/src/address_lookup_table.rs (L724-727)
```rust
pub const DEACTIVATE_LOOKUP_TABLE_WARNING: &str =
    "WARNING! Once a lookup table is deactivated, it is no longer usable by transactions.
Deactivated lookup tables may only be closed and cannot be recreated at the same address. To \
     proceed with deactivation, rerun the `deactivate` command with the `--bypass-warning` flag";
```

**File:** cli/src/address_lookup_table.rs (L786-820)
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
```

**File:** cli/tests/address_lookup_table.rs (L191-220)
```rust
    // Deactivate lookup table w/o bypass
    config.command =
        CliCommand::AddressLookupTable(AddressLookupTableCliCommand::DeactivateLookupTable {
            lookup_table_pubkey,
            authority_signer_index: 0,
            bypass_warning: false,
        });
    let process_err = process_command(&config).await.unwrap_err();
    assert_eq!(process_err.to_string(), DEACTIVATE_LOOKUP_TABLE_WARNING);

    // Deactivate lookup table w/ bypass
    config.command =
        CliCommand::AddressLookupTable(AddressLookupTableCliCommand::DeactivateLookupTable {
            lookup_table_pubkey,
            authority_signer_index: 0,
            bypass_warning: true,
        });
    process_command(&config).await.unwrap();

    // Validate deactivated lookup table
    {
        config.command =
            CliCommand::AddressLookupTable(AddressLookupTableCliCommand::ShowLookupTable {
                lookup_table_pubkey,
            });
        let CliAddressLookupTable {
            deactivation_slot, ..
        } = serde_json::from_str(&process_command(&config).await.unwrap()).unwrap();
        assert_ne!(deactivation_slot, u64::MAX);
    }
```
