## Title
`TransactionBatchProcessor`'s per-block `builtin_program_cache` is not updated when a builtin is removed via Core BPF migration, allowing stale builtin execution — ([File: svm/src/transaction_processor.rs])

### Summary
`TransactionBatchProcessor` maintains two related but distinct caches that must stay in sync: the `builtin_program_ids: RwLock<HashSet<Pubkey>>` set and a derived, pre-populated `builtin_program_cache: RwLock<ProgramCacheForTxBatch>` [1](#0-0) . `add_builtin()` keeps all three structures (`builtin_program_ids`, `global_program_cache`, `builtin_program_cache`) synchronized atomically [2](#0-1) . However, the only removal path — `migrate_builtin_to_core_bpf()` — removes the program only from `builtin_program_ids` and never touches `builtin_program_cache` [3](#0-2) . This is structurally identical to the Nested `addOperator`/`removeOperator` asymmetry: additions are atomic with the cache, removals are not.

### Finding Description
`builtin_program_cache` is explicitly documented as being "populated once per block in `new_from()` from the global program cache, avoiding re-acquiring the lock and re-running extract() on every batch" [4](#0-3) . It is built by iterating the current `builtin_program_ids` snapshot and extracting matching entries from `global_program_cache` [5](#0-4) .

`add_builtin()` is the only writer that keeps `builtin_program_ids`, `global_program_cache`, and `builtin_program_cache` consistent in a single call: [2](#0-1) 

In contrast, `migrate_builtin_to_core_bpf()` (invoked from `apply_new_builtin_program_feature_transitions()` during feature activation, e.g. for stake/SPL-token program migrations) deploys the new BPF program into `global_program_cache` via `directly_invoke_loader_v3_deploy()` [6](#0-5)  and then removes the address only from `builtin_program_ids`: [3](#0-2) 

There is no code path that removes the corresponding stale `Builtin` entry from `builtin_program_cache`. Because `builtin_program_cache` is only rebuilt once per block (in `new_from()`), a bank instance that has already had its `builtin_program_cache` populated before a mid-block/epoch-boundary migration runs will retain a stale `ProgramCacheEntryType::Builtin` entry for the just-migrated program id for the remainder of that bank's lifetime — the exact "cache not synced on removal" defect described in the source report (`removeOperator()` not calling `rebuildCache()`, leaving `operatorCache` stale).

### Impact Explanation
If a transaction invoking the just-migrated program id is processed against a `TransactionBatchProcessor` whose `builtin_program_cache` was populated prior to the migration, the stale native `Builtin` entry could be served instead of the intended new Core BPF program. Native builtin dispatch bypasses normal loader-owner/account-state validation (it directly calls the registered Rust function), so this is a false-execution primitive: an unprivileged user submitting an ordinary transaction against the migrated program id could cause the validator to run the old native implementation against on-chain state that has already been rewritten to Core BPF program-data format, producing execution results inconsistent with what other validators (whose caches were rebuilt after the migration) would produce for the same transaction. This maps to the "false execution / non-determinism across validators" impact category, since it can lead to different validators executing different code for the identical transaction, which is a consensus-divergence risk.

### Likelihood Explanation
Core BPF migrations are rare (feature-gated, one-time events per program such as the stake-program-v5 upgrade), and the exposure window depends on the exact ordering between `TransactionBatchProcessor::new_from()` (which snapshots `builtin_program_ids` into `builtin_program_cache`) and `apply_new_builtin_program_feature_transitions()` (which mutates `builtin_program_ids`) within `Bank::_new_from_parent`/`finish_init`. I was not able to fully confirm, within the available tool budget, the precise call ordering that determines whether `new_from()` always runs after migrations complete for a given bank, which would eliminate the exposure. This ordering question is the key remaining uncertainty and should be verified directly in the repository (e.g., via a Devin session with full code access) before treating this as exploitable in practice.

### Recommendation
- Add a `remove_builtin(&self, program_id: &Pubkey)` helper (mirroring `add_builtin`) that atomically removes the entry from `builtin_program_ids`, `global_program_cache`, and `builtin_program_cache`, and call it from `migrate_builtin_to_core_bpf()` instead of directly mutating `builtin_program_ids`.
- Alternatively, invalidate/rebuild `builtin_program_cache` any time `builtin_program_ids` changes outside of `add_builtin`, rather than relying solely on the once-per-block `new_from()` refresh.
- Audit `Bank::_new_from_parent`/`finish_init` to guarantee `new_from()` for the transaction processor always executes strictly after all builtin-removing feature transitions for that slot, and add a debug assertion that `builtin_program_cache` never contains an id absent from `builtin_program_ids`.

### Proof of Concept
Structural PoC (code-path based, mirroring the Nested report's methodology):
1. Construct a `TransactionBatchProcessor` and call `new_from()` to populate `builtin_program_cache` while `builtin_program_ids` still contains program `P` as a native builtin [5](#0-4) .
2. Invoke `migrate_builtin_to_core_bpf(&P, ...)` on the same processor/bank instance, which deploys the BPF replacement into `global_program_cache` and removes `P` from `builtin_program_ids` only [7](#0-6) .
3. Without calling `new_from()` again, submit a transaction invoking program `P` through this same `TransactionBatchProcessor` instance; because the per-block `builtin_program_cache` still contains the pre-migration `Builtin` entry for `P` and nothing purges it, the stale native implementation remains reachable instead of the freshly-loaded BPF entry in `global_program_cache`.

Confirming whether an actual in-block transaction lookup consumes `builtin_program_cache` (versus always re-extracting from `global_program_cache` per batch) requires locating and reading the consumer of `builtin_program_cache` in `svm/src/transaction_processor.rs`, which I was unable to fully trace within this session's tool budget — a Devin session with full repository access should verify this call site before finalizing severity.

### Citations

**File:** svm/src/transaction_processor.rs (L215-221)
```rust
    /// Builtin program ids
    pub builtin_program_ids: RwLock<HashSet<Pubkey>>,

    /// Cached ProgramCacheForTxBatch pre-populated with builtin entries.
    /// Populated once per block in `new_from()` from the global program cache,
    /// avoiding re-acquiring the lock and re-running extract() on every batch.
    builtin_program_cache: RwLock<ProgramCacheForTxBatch>,
```

**File:** svm/src/transaction_processor.rs (L321-356)
```rust
    pub fn new_from(&self, slot: Slot, epoch: Epoch) -> Self {
        let builtin_program_ids = self.builtin_program_ids.read().unwrap().clone();
        let environments = self.program_runtime_environment.clone();

        // Pre-populate the builtin program cache from the global cache.
        // This is done once per block rather than once per batch.
        let mut builtin_program_cache = ProgramCacheForTxBatch::new(slot);
        let mut search_for: Vec<ProgramToLoad> = builtin_program_ids
            .iter()
            .map(|program_id| ProgramToLoad {
                program_id,
                loader: ProgramCacheEntryOwner::NativeLoader,
                match_criteria: ProgramCacheMatchCriteria::NoCriteria,
                last_modification_slot: 0,
            })
            .collect();
        self.global_program_cache.read().unwrap().extract(
            &mut search_for,
            &mut builtin_program_cache,
            &environments,
            false,
            false,
        );

        Self {
            slot,
            epoch,
            sysvar_cache: RwLock::<SysvarCache>::default(),
            epoch_boundary_preparation: self.epoch_boundary_preparation.clone(),
            global_program_cache: self.global_program_cache.clone(),
            program_runtime_environment: environments,
            builtin_program_ids: RwLock::new(builtin_program_ids),
            builtin_program_cache: RwLock::new(builtin_program_cache),
            execution_cost: self.execution_cost,
        }
    }
```

**File:** svm/src/transaction_processor.rs (L1363-1377)
```rust
    /// Add a built-in program
    pub fn add_builtin(&self, program_id: Pubkey, builtin: ProgramCacheEntry) {
        self.builtin_program_ids.write().unwrap().insert(program_id);
        let entry = Arc::new(builtin);
        self.global_program_cache.write().unwrap().assign_program(
            &self.program_runtime_environment,
            program_id,
            0,
            Arc::clone(&entry),
        );
        self.builtin_program_cache
            .write()
            .unwrap()
            .replenish(program_id, entry);
    }
```

**File:** runtime/src/bank/builtins/core_bpf_migration/mod.rs (L268-307)
```rust
        // Deploy the new target Core BPF program.
        // This step will validate the program ELF against the current runtime
        // environment, as well as update the program cache.
        self.directly_invoke_loader_v3_deploy(
            &target.program_address,
            new_target_program_data_account.data(),
        )?;

        // Calculate the lamports to burn.
        // The target program account will be replaced, so burn its lamports.
        // The target program data account might have lamports if it existed,
        // so burn its lamports if any.
        // The source buffer account will be cleared, so burn its lamports.
        // The two new program accounts will need to be funded.
        let lamports_to_burn = checked_add(
            target.program_account.lamports(),
            source.buffer_account.lamports(),
        )
        .and_then(|v| checked_add(v, target.program_data_account_lamports))?;
        let lamports_to_fund = checked_add(
            new_target_program_account.lamports(),
            new_target_program_data_account.lamports(),
        )?;
        self.update_captalization(lamports_to_burn, lamports_to_fund)?;

        // Store the new program accounts and clear the source buffer account.
        self.store_account(&target.program_address, &new_target_program_account);
        self.store_account(
            &target.program_data_address,
            &new_target_program_data_account,
        );
        self.store_account(&source.buffer_address, &AccountSharedData::default());

        // Remove the built-in program from the bank's list of built-ins.
        self.transaction_processor
            .builtin_program_ids
            .write()
            .unwrap()
            .remove(&target.program_address);

```
