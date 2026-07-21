### Title
`ExecutionStateReader::get_compiled_class_hash` Returns Stale V1 Hash After Migration, Causing Repeated Spurious Migration in RPC Execution and Fee Estimation - (File: crates/apollo_rpc_execution/src/state_reader.rs)

### Summary

During the compiled-class-hash migration period (V1 Pedersen → V2 Poseidon), `ExecutionStateReader::get_compiled_class_hash` reads the compiled class hash from the **original declaration block's state diff** rather than from the latest `compiled_class_hash_table` entry. After a class is migrated in block N, the original declaration block's state diff still holds V1. In every subsequent block where that class is executed, `should_migrate` compares this stale V1 against the correct V2 from `get_compiled_class_hash_v2`, concludes migration is needed again, and injects a spurious `class_hash → V2` entry into the state diff along with incorrect migration gas. This produces authoritative-looking wrong values from `starknet_estimateFee`, `starknet_simulateTransactions`, and pending-block state-update queries.

### Finding Description

**Two simultaneously active hash sources — the structural analog to the dual-controller period**

The ENS H-02 bug exploits a deprecation window where two registrar controllers are simultaneously active, each holding a different authoritative view of ownership. The sequencer has an identical structural pattern: during the compiled-class-hash migration window, two sources of truth for a class's compiled hash coexist:

| Source | Returns | Used by |
|---|---|---|
| `compiled_class_hash_table` (keyed by `(class_hash, block_number)`) | Latest value — V2 after migration | `ApolloReader::get_compiled_class_hash` (batcher path) |
| `state_diffs_table` at the original declaration block | Always V1 (never updated) | `ExecutionStateReader::get_compiled_class_hash` (RPC path) |

**The stale-read path in `ExecutionStateReader`**

`ExecutionStateReader::get_compiled_class_hash` does not read from the `compiled_class_hash_table`. Instead it:

1. Calls `get_class_definition_block_number(&class_hash)` to find the block where the class was first declared (block M).
2. Fetches the full `ThinStateDiff` for block M via `get_state_diff(block_number)`.
3. Returns `state_diff.class_hash_to_compiled_class_hash.get(&class_hash)` — the V1 hash recorded at declaration time. [1](#0-0) 

After migration in block N > M, the `compiled_class_hash_table` has a new entry `(class_hash, N) → V2`, but the state diff for block M is immutable and still holds V1. `ExecutionStateReader::get_compiled_class_hash` therefore permanently returns V1 for any class that was migrated.

**`should_migrate` incorrectly fires on every subsequent execution**

`should_migrate` decides whether a class needs migration by comparing the two hash sources: [2](#0-1) 

- `state_reader.get_compiled_class_hash(class_hash)` → V1 (stale, from declaration block's state diff)
- `state_reader.get_compiled_class_hash_v2(class_hash, ...)` → V2 (correct, from class manager or `executable_class_hash_v2` table)

Since V1 ≠ V2, `should_migrate` returns `Some((class_hash, (V2, V1)))` on every invocation, even though the class was already migrated.

**`finalize_block` writes a spurious migration entry into the state diff**

`finalize_block` calls `set_compiled_class_hash_migration`, which writes V2 into the `CachedState` cache: [3](#0-2) 

Then `to_state_diff()` computes `writes.diff(&initial_reads)`. Because the initial read was V1 (stale) and the write is V2, the diff includes `class_hash → V2` as if it were a new migration event: [4](#0-3) 

This spurious entry propagates into `CommitmentStateDiff.class_hash_to_compiled_class_hash`: [5](#0-4) 

And from there into `ThinStateDiff` used for state diff commitment calculation: [6](#0-5) 

**Contrast with the correct batcher path**

`ApolloReader::get_compiled_class_hash` uses `get_compiled_class_hash_at(state_number, &class_hash)`, which reads the latest entry from the `compiled_class_hash_table` via a cursor. After migration, this correctly returns V2, so `should_migrate` returns `None` and no spurious entry is produced in the actual block: [7](#0-6) 

The RPC execution path (`ExecutionStateReader`) and the batcher path (`ApolloReader`) give different answers for the same question, exactly mirroring the dual-controller inconsistency in H-02.

### Impact Explanation

Every call to `starknet_estimateFee` or `starknet_simulateTransactions` that executes a previously-migrated Cairo 1 class will:

1. **Return wrong gas**: Migration gas is charged again for a class that does not need migration, inflating the fee estimate. Users who rely on this estimate for transaction submission will overpay or have transactions rejected.

2. **Return wrong state diff**: The simulated `class_hash_to_compiled_class_hash` map contains a spurious `class_hash → V2` entry. Clients that use simulation output to verify state transitions (e.g., wallets, dApps, indexers) receive an authoritative-looking wrong value.

3. **Wrong pending state update**: If `ExecutionStateReader` is used for pending block execution, `starknet_getStateUpdate(pending)` returns a state diff with spurious migration entries, misleading any consumer of pending state.

This matches the allowed impact: **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.**

### Likelihood Explanation

- `enable_casm_hash_migration` is a `versioned_constants` flag that will be enabled in production during the migration window. [8](#0-7) 
- Once enabled, any Cairo 1 class declared before the migration and executed after it is migrated will trigger the bug.
- No special privileges are required. Any user can call `starknet_estimateFee` or `starknet_simulateTransactions` with a transaction that invokes a migrated class.
- The condition (migrated class re-executed) is routine — account contracts, ERC-20 tokens, and other frequently-used contracts will be migrated and then executed in every subsequent block.

### Recommendation

Replace the declaration-block state-diff lookup in `ExecutionStateReader::get_compiled_class_hash` with a call to `get_compiled_class_hash_at(self.state_number, &class_hash)`, which reads the latest value from the `compiled_class_hash_table` and correctly reflects post-migration V2 hashes:

```rust
fn get_compiled_class_hash(&self, class_hash: ClassHash) -> StateResult<CompiledClassHash> {
    // Check pending first (unchanged).
    if let Some(pending_data) = &self.maybe_pending_data {
        for DeclaredClassHashEntry { class_hash: other_class_hash, compiled_class_hash }
            in &pending_data.declared_classes
        {
            if class_hash == *other_class_hash {
                return Ok(*compiled_class_hash);
            }
        }
    }

    // Use the versioned compiled_class_hash_table, not the original declaration block's state diff.
    match self
        .storage_reader
        .begin_ro_txn()
        .map_err(storage_err_to_state_err)?
        .get_state_reader()
        .map_err(storage_err_to_state_err)?
        .get_compiled_class_hash_at(self.state_number, &class_hash)
        .map_err(storage_err_to_state_err)?
    {
        Some(hash) => Ok(hash),
        None => Ok(CompiledClassHash::default()),
    }
}
```

This aligns `ExecutionStateReader` with `ApolloReader`, eliminating the dual-source inconsistency.

### Proof of Concept

```
Block 0: Declare class C with compiled_class_hash = V1 (Pedersen).
         state_diffs_table[0].class_hash_to_compiled_class_hash[C] = V1
         compiled_class_hash_table[(C, 0)] = V1

Block 1: Migration enabled. Transaction executes class C.
         finalize_block:
           should_migrate(C):
             get_compiled_class_hash(C) via ExecutionStateReader
               → get_class_definition_block_number(C) = 0
               → get_state_diff(0).class_hash_to_compiled_class_hash[C] = V1  ← correct
             get_compiled_class_hash_v2(C) = V2
             V1 ≠ V2 → migrate
           set_compiled_class_hash_migration: writes C → V2 into cache
           to_state_diff: initial_read=V1, write=V2 → diff includes C → V2
         compiled_class_hash_table[(C, 1)] = V2  ← migration committed

Block 2: Transaction executes class C again.
         finalize_block (RPC fee estimation uses ExecutionStateReader):
           should_migrate(C):
             get_compiled_class_hash(C) via ExecutionStateReader
               → get_class_definition_block_number(C) = 0  ← still block 0
               → get_state_diff(0).class_hash_to_compiled_class_hash[C] = V1  ← STALE
             get_compiled_class_hash_v2(C) = V2
             V1 ≠ V2 → migrate  ← WRONG: already migrated in block 1
           Spurious C → V2 in state diff, spurious migration gas in fee estimate.

         (Batcher path with ApolloReader):
           get_compiled_class_hash(C):
             → get_compiled_class_hash_at(state_number=2, C)
             → compiled_class_hash_table cursor finds (C, 1) → V2  ← CORRECT
           should_migrate: V2 == V2 → None  ← no spurious migration
```

The RPC fee estimation and simulation for block 2 return wrong gas and a wrong state diff containing a spurious `C → V2` migration entry, while the actual block built by the batcher is correct.

### Citations

**File:** crates/apollo_rpc_execution/src/state_reader.rs (L174-207)
```rust
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
```

**File:** crates/blockifier/src/utils.rs (L122-143)
```rust
pub fn should_migrate(
    state_reader: &impl StateReader,
    class_hash: ClassHash,
) -> StateResult<Option<(ClassHash, CompiledClassHashV2ToV1)>> {
    let state_compiled_class_hash = state_reader.get_compiled_class_hash(class_hash)?;
    match state_compiled_class_hash {
        // Class hash does not exist in the state, or is a Cairo 0 class.
        CompiledClassHash(hash) if hash == StarkHash::ZERO => Ok(None),
        state_compiled_class_hash => {
            let compiled_class_hash_v2 = state_reader.get_compiled_class_hash_v2(
                class_hash,
                &state_reader.get_compiled_class(class_hash)?,
            )?;
            // If the state compiled class hash is compiled class hash v2, the class should not
            // migrate.
            if state_compiled_class_hash == compiled_class_hash_v2 {
                return Ok(None);
            }
            Ok(Some((class_hash, (compiled_class_hash_v2, state_compiled_class_hash))))
        }
    }
}
```

**File:** crates/blockifier/src/state/compiled_class_hash_migration.rs (L18-36)
```rust
    fn set_compiled_class_hash_migration(
        &mut self,
        class_hashes_to_migrate: &HashMap<ClassHash, CompiledClassHashV2ToV1>,
    ) -> StateResult<()> {
        for (class_hash, (compiled_class_hash_v2, compiled_class_hash_v1)) in
            class_hashes_to_migrate
        {
            // Sanity check: the compiled class hashes should not be equal.
            assert_ne!(
                compiled_class_hash_v1, compiled_class_hash_v2,
                "Classes for migration should hold v1 (Poseidon) hash in the state."
            );

            // TODO(Meshi): Consider panic here instead of returning an error.
            self.set_compiled_class_hash(*class_hash, *compiled_class_hash_v2)?;
        }

        Ok(())
    }
```

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L265-273)
```rust
    if !block_context.versioned_constants.enable_casm_hash_migration {
        assert!(
            class_hashes_to_migrate.is_empty(),
            "Class hashes to migrate should be empty when migration is disabled"
        );
    }
    block_state.set_compiled_class_hash_migration(&class_hashes_to_migrate)?;

    let state_diff = block_state.to_state_diff()?.state_maps;
```

**File:** crates/blockifier/src/state/cached_state.rs (L679-701)
```rust
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: StorageDiff::from(StorageView(diff.storage)),
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
        }
    }
}

impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff
                .class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L160-166)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
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
