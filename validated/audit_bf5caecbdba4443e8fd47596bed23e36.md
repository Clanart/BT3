### Title
Stale `declared_classes_block_table` Secondary Index Causes `ExecutionStateReader::get_compiled_class_hash` to Return Wrong Compiled Class Hash After CASM Migration - (File: `crates/apollo_rpc_execution/src/state_reader.rs`)

---

### Summary

`ExecutionStateReader::get_compiled_class_hash` resolves a class's compiled class hash by first looking up the **first-ever declaration block** from the `declared_classes_block_table` secondary index, then reading the compiled class hash from the state diff at that block. When a CASM hash migration updates the compiled class hash in a later block, the `declared_classes_block_table` is never updated (it is write-once), so the RPC execution layer permanently reads the stale pre-migration compiled class hash. This is the direct sequencer analog of the NFTPool `ownerToId` bug: a secondary index that maps an entity to its first-seen block is not updated when the primary state changes.

---

### Finding Description

**Two storage structures track compiled class hashes:**

1. `compiled_class_hash_table`: keyed by `(ClassHash, BlockNumber)`, written on every declaration **and** every CASM migration. Supports cursor-based "latest-before-block" lookup. This is the authoritative, versioned table.

2. `declared_classes_block_table`: keyed by `ClassHash`, maps to the **first** block in which the class appeared in `class_hash_to_compiled_class_hash`. Written **only once** — the `if not_declared` guard in `append_state_diff` prevents any update on subsequent appearances. [1](#0-0) 

When a CASM hash migration occurs (the sequencer re-emits a class's `class_hash_to_compiled_class_hash` entry with a new `CompiledClassHash` value in a later block M), `write_compiled_class_hashes` correctly appends a new row `(class_hash, M) → H2` to `compiled_class_hash_table`. [2](#0-1) 

But `declared_classes_block_table` is **not updated** — it still points to the original declaration block N.

`ExecutionStateReader::get_compiled_class_hash` (used by the RPC execution path for `estimate_fee`, `simulate`, and `call`) performs a two-step lookup:

1. Read `declared_classes_block_table[class_hash]` → block N (stale).
2. Read `get_state_diff(N).class_hash_to_compiled_class_hash[class_hash]` → H1 (old hash). [3](#0-2) 

By contrast, `ApolloReader::get_compiled_class_hash` (used by the batcher/execution path) calls `get_compiled_class_hash_at`, which does a cursor-based scan of `compiled_class_hash_table` and correctly returns H2. [4](#0-3) 

The CASM hash migration is a real, production-enabled feature. The `BlockExecutionSummary` carries `compiled_class_hashes_for_migration`, and the resulting state diff includes migrated classes in `class_hash_to_compiled_class_hash`. [5](#0-4) 

The test in `transaction_executor_test.rs` explicitly confirms that a migration-only block populates `state_diff.class_hash_to_compiled_class_hash` with the new hash for an already-declared class. [6](#0-5) 

---

### Impact Explanation

After a CASM hash migration at block M:

- `declared_classes_block_table[C]` = N (original declaration block, never updated)
- `compiled_class_hash_table[(C, N)]` = H1 (old hash)
- `compiled_class_hash_table[(C, M)]` = H2 (new hash)

Any RPC call that triggers `ExecutionStateReader::get_compiled_class_hash(C)` returns H1 instead of H2. This causes:

- `starknet_estimateFee` for a `Declare` transaction carrying `compiled_class_hash = H2` to simulate against the wrong state, producing an incorrect fee or a spurious failure.
- `starknet_simulateTransactions` to return an authoritative-looking wrong execution result.
- `starknet_call` on contracts that branch on the compiled class hash to return wrong values.

This matches: **High. RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

---

### Likelihood Explanation

The trigger requires only that the sequencer has performed at least one CASM hash migration (a normal sequencer operation controlled by `enable_casm_hash_migration`). Once any migration has been committed, every subsequent RPC execution query for the affected class hash will return the stale value. No privileged access or malicious peer is required — any user querying the RPC after a migration is affected.

---

### Recommendation

Replace the two-step `declared_classes_block_table → state_diff` lookup in `ExecutionStateReader::get_compiled_class_hash` with a direct call to `get_compiled_class_hash_at(state_number, class_hash)`, which uses the versioned `compiled_class_hash_table` and correctly returns the latest compiled class hash at the queried state number. This is exactly what `ApolloReader::get_compiled_class_hash` already does correctly. [7](#0-6) 

Alternatively, update `declared_classes_block_table` on every write to `class_hash_to_compiled_class_hash` (not just the first), but this would change the semantics of that table and require auditing all callers.

---

### Proof of Concept

```
// Setup: class C declared at block 0 with compiled_class_hash H1.
// CASM migration at block 1 updates compiled_class_hash to H2.

// After block 1 is committed:

// declared_classes_block_table[C] = 0  (never updated by append_state_diff)
// compiled_class_hash_table[(C, 0)] = H1
// compiled_class_hash_table[(C, 1)] = H2

// ApolloReader (batcher path) — correct:
let h = apollo_reader.get_compiled_class_hash(C);
assert_eq!(h, H2);  // uses get_compiled_class_hash_at → cursor scan → H2

// ExecutionStateReader (RPC path) — stale:
let h = exec_state_reader.get_compiled_class_hash(C);
// Step 1: declared_classes_block_table[C] = 0
// Step 2: get_state_diff(0).class_hash_to_compiled_class_hash[C] = H1
assert_eq!(h, H1);  // WRONG — returns pre-migration hash

// Consequence: starknet_estimateFee / starknet_simulateTransactions
// for a Declare tx with compiled_class_hash=H2 executes against H1,
// producing wrong fee or spurious AlreadyDeclared / ClassHashNotFound error.
```

### Citations

**File:** crates/apollo_storage/src/state/mod.rs (L549-554)
```rust
        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(&self.txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(&self.txn, class_hash, &block_number)?;
            }
        }
```

**File:** crates/apollo_storage/src/state/mod.rs (L800-811)
```rust
#[latency_histogram("storage_write_nonce_latency_seconds", false)]
fn write_compiled_class_hashes<'env>(
    compiled_class_hashes: &IndexMap<ClassHash, CompiledClassHash>,
    txn: &DbTransaction<'env, RW>,
    block_number: BlockNumber,
    compiled_class_hash_table: &'env CompiledClassHashTable<'env>,
) -> StorageResult<()> {
    for (class_hash, compiled_class_hash) in compiled_class_hashes {
        compiled_class_hash_table.insert(txn, &(*class_hash, block_number), compiled_class_hash)?;
    }
    Ok(())
}
```

**File:** crates/apollo_storage/src/state/mod.rs (L1066-1088)
```rust
fn get_compiled_class_hash_at<'env, Mode: TransactionKind>(
    first_irrelevant_block: BlockNumber,
    class_hash: &ClassHash,
    txn: &'env DbTransaction<'env, Mode>,
    compiled_class_hash_table: &'env CompiledClassHashTable<'env>,
) -> StorageResult<Option<CompiledClassHash>> {
    let db_key = (*class_hash, first_irrelevant_block);
    // Find the previous db item.
    let mut cursor = compiled_class_hash_table.cursor(txn)?;
    cursor.lower_bound(&db_key)?;
    let res = cursor.prev()?;
    match res {
        None => Ok(None),
        Some(((got_class_hash, _got_block_number), value)) => {
            if got_class_hash != *class_hash {
                // The previous item belongs to different class hash, which means there is no
                // previous state diff for this item.
                return Ok(None);
            };
            // The previous db item indeed belongs to this address and key.
            Ok(Some(value))
        }
    }
```

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L163-208)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        if let Some(pending_data) = &self.maybe_pending_data {
            for DeclaredClassHashEntry { class_hash: other_class_hash, compiled_class_hash } in
                &pending_data.declared_classes
            {
                if class_hash == *other_class_hash {
                    return Ok(*compiled_class_hash);
                }
            }
        }

        let maybe_block_number = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_reader()
            .map_err(storage_err_to_state_err)?
            .get_class_definition_block_number(&class_hash)
            .map_err(storage_err_to_state_err)?;

        // Cairo 0 classes (and undeclared classes) do not have a compiled class hash.
        // According to the trait, return the default value.
        let Some(block_number) = maybe_block_number else {
            return Ok(CompiledClassHash::default());
        };

        let state_diff = self
            .storage_reader
            .begin_ro_txn()
            .map_err(storage_err_to_state_err)?
            .get_state_diff(block_number)
            .map_err(storage_err_to_state_err)?
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing state diff at block {block_number}."
            )))?;

        let compiled_class_hash = state_diff
            .class_hash_to_compiled_class_hash
            .get(&class_hash)
            .ok_or(StateError::StateReadError(format!(
                "Inner storage error. Missing class declaration at block {block_number}, class \
                 {class_hash}."
            )))?;

        Ok(*compiled_class_hash)
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L125-140)
```rust
pub struct BlockExecutionArtifacts {
    // Note: The execution_infos must be ordered to match the order of the transactions in the
    // block.
    pub execution_data: BlockTransactionExecutionData,
    pub commitment_state_diff: CommitmentStateDiff,
    pub compressed_state_diff: Option<CommitmentStateDiff>,
    pub bouncer_weights: BouncerWeights,
    pub l2_gas_used: GasAmount,
    pub casm_hash_computation_data_sierra_gas: CasmHashComputationData,
    pub casm_hash_computation_data_proving_gas: CasmHashComputationData,
    pub compiled_class_hashes_for_migration: CompiledClassHashesForMigration,
    // The number of transactions executed by the proposer out of the transactions that were sent.
    // This value includes rejected transactions.
    pub final_n_executed_txs: usize,
    partial_block_hash_components: PartialBlockHashComponents,
}
```

**File:** crates/blockifier/src/blockifier/transaction_executor_test.rs (L505-522)
```rust
        // Verify that the migration is applied to the state diff.
        // State diff class hash to compiled class hash contains both migration and declared
        // classes. But this block only contains the migration.
        assert_eq!(
            block_execution_summary.state_diff.class_hash_to_compiled_class_hash,
            executed_class_hashes
                .iter()
                .map(|&class_hash| (
                    class_hash,
                    state
                        .get_compiled_class_hash_v2(
                            class_hash,
                            &state.get_compiled_class(class_hash).unwrap()
                        )
                        .unwrap()
                ))
                .collect::<IndexMap<_, _>>()
        );
```

**File:** crates/apollo_state_reader/src/apollo_state.rs (L243-254)
```rust
    fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
        let state_number = StateNumber(self.latest_block);
        match self
            .reader()?
            .get_state_reader()
            .and_then(|sr| sr.get_compiled_class_hash_at(state_number, &class_hash))
        {
            Ok(Some(compiled_class_hash)) => Ok(compiled_class_hash),
            Ok(None) => Ok(CompiledClassHash::default()),
            Err(err) => Err(StateError::StateReadError(err.to_string())),
        }
    }
```
