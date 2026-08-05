No vulnerability found for this question.

**Reasoning:**

The claimed invariant — "late failures must roll back every consensus-relevant state effect" — is already enforced by existing mechanisms, and the artifacts named in the question (program cache entries, logs, counters) are not consensus-relevant state to begin with.

1. **Account state rollback is comprehensive and already covers the consensus-relevant surface.** On failure, `TransactionBatchProcessor::load_and_execute_sanitized_transactions` calls `account_loader.update_accounts_for_failed_tx` with only the transaction's `RollbackAccounts` (fee payer and/or nonce), discarding every other account mutation from the batch cache, while a success path calls `update_accounts_for_successful_tx` instead [1](#0-0) . `RollbackAccounts` is computed pre-execution and captures exactly the fee-payer/nonce state that is allowed to survive a failed transaction [2](#0-1) .

2. **A lamports-conservation check catches any inconsistency regardless of how late the failure occurs.** After `process_message`, the sum of all transaction account lamports is compared against the pre-execution sum, and any mismatch is converted into `TransactionError::UnbalancedTransaction`, which is then treated as an execution failure subject to the same rollback path [3](#0-2) . Rent-state transitions are independently verified via `verify_changes`/`TransactionAccountStateInfo` [4](#0-3) .

3. **Program cache mutations from a failed transaction are never merged into the shared/global cache.** `program_cache_for_tx_batch.merge(&executed_tx.programs_modified_by_tx)` is only invoked in the `Ok(_)` (successful) branch; the failure branch skips it entirely [5](#0-4) . So even a deep CPI graph that deploys/upgrades programs and then fails at the very end cannot leak a modified program entry into the batch's or global program cache.

4. **Program-cache population itself is not an exploitable "side effect" in the consensus sense.** `load_program_with_pubkey` (called from `Bank::load_program` and `replenish_program_cache`) derives a `ProgramCacheEntry` deterministically from on-chain program/program-data account bytes at a given slot [6](#0-5) . Every validator observing the same slot's account state computes the identical cache entry independent of which transaction happened to trigger the load or whether that transaction ultimately succeeded or failed, so populating this cache from a doomed transaction cannot cause divergent bank hashes/state roots — it's a memoization of already-committed on-chain data, not new state.

5. **Logs and usage counters are explicitly non-consensus artifacts.** Log messages are attached to `CommittedTransaction`/`ExecutionDetails` per-transaction and are informational only [7](#0-6) ; cache hit/miss and usage counters (`increment_usage_counter`, `stats.reset()`) are local bookkeeping used for eviction/telemetry and are reset per bank, not folded into the bank hash [8](#0-7) [9](#0-8) .

Given that (a) all consensus-relevant account/lamport/rent state is already gated by `RollbackAccounts` + balance/rent-state verification, and (b) the cache/log/counter artifacts named in the question are deterministic or explicitly out-of-consensus, there is no reachable path by which an unprivileged attacker's deep-CPI, late-failing transaction could leak a state effect that breaks consensus. Existing checks already stop this.

### Citations

**File:** svm/src/transaction_processor.rs (L592-620)
```rust
                        // Successful transactions need to update the account loader cache as future
                        // transactions in the batch may depend on them.
                        (Ok(_), _) => {
                            account_loader.update_accounts_for_successful_tx(
                                tx,
                                &executed_tx.loaded_transaction.accounts,
                                &executed_tx.loaded_transaction.touched_flags,
                                self.slot,
                            );
                            // Also update local program cache with modifications made by the
                            // transaction, if it executed successfully.
                            program_cache_for_tx_batch.merge(&executed_tx.programs_modified_by_tx);

                            Ok(ProcessedTransaction::Executed(Box::new(executed_tx)))
                        }
                        // If the transaction failed & drop on failure is set then we don't want to
                        // update the accounts as this transaction will be dropped from the batch.
                        (Err(err), true) => Err(err.clone()),
                        // Unsuccessful transactions will still update rollback accounts (fee payer,
                        // nonce, etc).
                        (Err(_), false) => {
                            account_loader.update_accounts_for_failed_tx(
                                &executed_tx.loaded_transaction.rollback_accounts,
                                self.slot,
                            );

                            Ok(ProcessedTransaction::Executed(Box::new(executed_tx)))
                        }
                    }
```

**File:** svm/src/transaction_processor.rs (L910-922)
```rust
        let mut count_hits_and_misses = true;
        loop {
            // Lock the global cache.
            let global_program_cache = self.global_program_cache.read().unwrap();
            // Figure out which program needs to be loaded next.
            let program_to_load = global_program_cache.extract(
                &mut missing_programs,
                program_cache_for_tx_batch,
                program_runtime_environment_for_execution,
                increment_usage_counter,
                count_hits_and_misses,
            );
            count_hits_and_misses = false;
```

**File:** svm/src/rollback_accounts.rs (L64-99)
```rust
impl RollbackAccounts {
    pub(crate) fn new(
        nonce: Option<NonceInfo>,
        fee_payer_address: Pubkey,
        mut fee_payer_account: AccountSharedData,
        fee_payer_loaded_rent_epoch: Epoch,
    ) -> Self {
        if let Some(nonce) = nonce {
            if &fee_payer_address == nonce.address() {
                // `nonce` contains an AccountSharedData which has already been advanced to the current DurableNonce
                // `fee_payer_account` is an AccountSharedData as it currently exists on-chain
                // thus if the nonce account is being used as the fee payer, we need to update that data here
                // so we capture both the data change for the nonce and the lamports/rent epoch change for the fee payer
                fee_payer_account.set_data_from_slice(nonce.account().data());

                RollbackAccounts::SameNonceAndFeePayer {
                    nonce: (fee_payer_address, fee_payer_account),
                }
            } else {
                RollbackAccounts::SeparateNonceAndFeePayer {
                    nonce: (nonce.address, nonce.account),
                    fee_payer: (fee_payer_address, fee_payer_account),
                }
            }
        } else {
            // When rolling back failed transactions which don't use nonces, the
            // runtime should not update the fee payer's rent epoch so reset the
            // rollback fee payer account's rent epoch to its originally loaded
            // rent epoch value. In the future, a feature gate could be used to
            // alter this behavior such that rent epoch updates are handled the
            // same for both nonce and non-nonce failed transactions.
            fee_payer_account.set_rent_epoch(fee_payer_loaded_rent_epoch);
            RollbackAccounts::FeePayerOnly {
                fee_payer: (fee_payer_address, fee_payer_account),
            }
        }
```

**File:** svm/src/transaction_account_state_info.rs (L105-125)
```rust
pub(crate) fn verify_changes(
    pre_state_infos: &[TransactionAccountStateInfo],
    post_state_infos: &[TransactionAccountStateInfo],
    transaction_context: &TransactionContext,
) -> Result<()> {
    for (i, (pre_state_info, post_state_info)) in
        pre_state_infos.iter().zip(post_state_infos).enumerate()
    {
        if let (Some(pre_state_info), Some(post_state_info)) =
            (pre_state_info.info.as_ref(), post_state_info.info.as_ref())
        {
            check_rent_state(
                &pre_state_info.rent_state,
                &post_state_info.rent_state,
                transaction_context,
                i as IndexOfAccount,
            )?;
        }
    }
    Ok(())
}
```

**File:** svm/src/program_loader.rs (L99-193)
```rust
pub fn load_program_with_pubkey<CB: TransactionProcessingCallback>(
    callbacks: &CB,
    program_runtime_environment: &ProgramRuntimeEnvironment,
    pubkey: &Pubkey,
    current_slot: Slot,
    execute_timings: &mut ExecuteTimings,
) -> Option<(Arc<ProgramCacheEntry>, Slot)> {
    #[cfg(feature = "metrics")]
    let mut load_program_metrics = LoadProgramMetrics {
        program_id: pubkey.to_string(),
        ..LoadProgramMetrics::default()
    };
    #[cfg(not(feature = "metrics"))]
    let _ = execute_timings;

    let (load_result, last_modification_slot) = load_program_accounts(callbacks, pubkey)?;
    let loaded_program = match load_result {
        ProgramAccountLoadResult::InvalidAccountData(owner) => Ok(
            ProgramCacheEntry::new_tombstone(current_slot, owner, ProgramCacheEntryType::Closed),
        ),

        ProgramAccountLoadResult::ProgramOfLoaderV1(program_account) => ProgramCacheEntry::new(
            program_account.owner(),
            ProgramRuntimeEnvironment::clone(program_runtime_environment),
            0,
            program_account.data(),
            #[cfg(feature = "metrics")]
            &mut load_program_metrics,
        )
        .map_err(|_| (0, ProgramCacheEntryOwner::LoaderV1)),

        ProgramAccountLoadResult::ProgramOfLoaderV2(program_account) => ProgramCacheEntry::new(
            program_account.owner(),
            ProgramRuntimeEnvironment::clone(program_runtime_environment),
            0,
            program_account.data(),
            #[cfg(feature = "metrics")]
            &mut load_program_metrics,
        )
        .map_err(|_| (0, ProgramCacheEntryOwner::LoaderV2)),

        ProgramAccountLoadResult::ProgramOfLoaderV3(
            program_account,
            programdata_account,
            deployment_slot,
        ) => programdata_account
            .data()
            .get(UpgradeableLoaderState::size_of_programdata_metadata()..)
            .ok_or(())
            .and_then(|programdata| {
                ProgramCacheEntry::new(
                    program_account.owner(),
                    ProgramRuntimeEnvironment::clone(program_runtime_environment),
                    deployment_slot,
                    programdata,
                    #[cfg(feature = "metrics")]
                    &mut load_program_metrics,
                )
                .map_err(|_| ())
            })
            .map_err(|_| (deployment_slot, ProgramCacheEntryOwner::LoaderV3)),

        ProgramAccountLoadResult::ProgramOfLoaderV4(program_account, deployment_slot) => {
            program_account
                .data()
                .get(LoaderV4State::program_data_offset()..)
                .ok_or(())
                .and_then(|elf_bytes| {
                    ProgramCacheEntry::new(
                        &loader_v4::id(),
                        ProgramRuntimeEnvironment::clone(program_runtime_environment),
                        deployment_slot,
                        elf_bytes,
                        #[cfg(feature = "metrics")]
                        &mut load_program_metrics,
                    )
                    .map_err(|_| ())
                })
                .map_err(|_| (deployment_slot, ProgramCacheEntryOwner::LoaderV4))
        }
    }
    .unwrap_or_else(|(deployment_slot, owner)| {
        let env = ProgramRuntimeEnvironment::clone(program_runtime_environment);
        ProgramCacheEntry::new_tombstone(
            deployment_slot,
            owner,
            ProgramCacheEntryType::FailedVerification(env),
        )
    });

    #[cfg(feature = "metrics")]
    load_program_metrics.submit_datapoint(&mut execute_timings.details);
    loaded_program.update_access_slot(current_slot);
    Some((Arc::new(loaded_program), last_modification_slot))
}
```

**File:** runtime/src/bank.rs (L1596-1601)
```rust
        new.transaction_processor
            .global_program_cache
            .write()
            .unwrap()
            .stats
            .reset();
```

**File:** runtime/src/bank.rs (L4479-4492)
```rust
                        Ok(CommittedTransaction {
                            status: execution_details.status,
                            log_messages: execution_details.log_messages,
                            inner_instructions: execution_details.inner_instructions,
                            return_data: execution_details.return_data,
                            executed_units,
                            fee_details,
                            loaded_account_stats: TransactionLoadedAccountsStats {
                                loaded_accounts_count: loaded_accounts.len(),
                                loaded_accounts_data_size,
                            },
                            fee_payer_post_balance,
                        })
                    }
```
