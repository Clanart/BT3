### Title
`CommitmentStateDiff → ThinStateDiff` Conversion Silently Drops `deprecated_declared_classes`, Producing Wrong `state_diff_commitment` and `concatenated_counts` in Block Hash — (`crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion in the blockifier unconditionally sets `deprecated_declared_classes: Vec::new()`. Because `BlockExecutionArtifacts::new` derives the `ThinStateDiff` fed to `calculate_block_commitments` exclusively through this conversion, any block that contains a deprecated (Cairo 0) class declaration will produce a `state_diff_commitment` and a `state_diff_length` (inside `concatenated_counts`) that are both wrong. Both values are chained into the Poseidon block hash, so the sequencer's block hash diverges from the value the OS/prover computes, and the `ThinStateDiff` written to storage and broadcast over state-sync is also missing the deprecated class entries.

---

### Finding Description

**Root cause — the conversion:**

```rust
// crates/blockifier/src/state/cached_state.rs  lines 690-701
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            deployed_contracts: commitment_state_diff.address_to_class_hash,
            storage_diffs: commitment_state_diff.storage_updates,
            class_hash_to_compiled_class_hash: commitment_state_diff
                .class_hash_to_compiled_class_hash,
            nonces: commitment_state_diff.address_to_nonce,
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),   // ← always empty
        }
    }
}
``` [1](#0-0) 

`CommitmentStateDiff` has no `deprecated_declared_classes` field. The blockifier tracks class declarations in `StateMaps.declared_contracts: HashMap<ClassHash, bool>`, but `CommitmentStateDiff::from(StateMaps)` never copies that field:

```rust
// lines 679-687
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: StorageDiff::from(StorageView(diff.storage)),
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
            // diff.declared_contracts is silently discarded
        }
    }
}
``` [2](#0-1) 

**Propagation into block commitments:**

`BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff.clone())` and passes the result directly to `calculate_block_commitments`:

```rust
// crates/apollo_batcher/src/block_builder.rs  lines 160-166
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [3](#0-2) 

`calculate_block_commitments` spawns a task that calls `calculate_state_diff_hash`, which chains `deprecated_declared_classes` into the Poseidon hash:

```rust
// crates/starknet_api/src/block_hash/state_diff_hash.rs  lines 30-41
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
    ...
    hash_chain = chain_deprecated_declared_classes(
        &state_diff.deprecated_declared_classes,   // always [] from proposer path
        hash_chain,
    );
    ...
}
``` [4](#0-3) 

`calculate_block_commitments` also computes `state_diff.len()` for `concatenated_counts`:

```rust
// crates/starknet_api/src/block_hash/block_hash_calculator.rs  lines 318-323
let concatenated_counts = concat_counts(
    transactions_data.len(),
    event_leaf_elements.len(),
    state_diff.len(),   // ThinStateDiff::len() counts deprecated_declared_classes.len()
    l1_da_mode,
);
``` [5](#0-4) 

`ThinStateDiff::len()` explicitly counts `deprecated_declared_classes`:

```rust
// crates/starknet_api/src/state.rs  lines 110-121
pub fn len(&self) -> usize {
    let mut result = 0usize;
    result += self.deployed_contracts.len();
    result += self.class_hash_to_compiled_class_hash.len();
    result += self.deprecated_declared_classes.len();   // always 0 from proposer path
    result += self.nonces.len();
    ...
}
``` [6](#0-5) 

Both `state_diff_commitment` and `concatenated_counts` are chained into the final block hash:

```rust
// crates/starknet_api/src/block_hash/block_hash_calculator.rs  lines 260-261
.chain(&block_commitments.concatenated_counts)
.chain(&block_commitments.state_diff_commitment.0.0)
``` [7](#0-6) 

**Storage and sync path:**

`BlockExecutionArtifacts::thin_state_diff()` also uses the same conversion, so the `ThinStateDiff` written to MDBX storage via `append_state_diff` and sent in `DecisionReachedResponse` for state sync will also have empty `deprecated_declared_classes`:

```rust
// crates/apollo_batcher/src/block_builder.rs  lines 198-201
pub fn thin_state_diff(&self) -> ThinStateDiff {
    ThinStateDiff::from(self.commitment_state_diff.clone())  // deprecated_declared_classes = []
}
``` [8](#0-7) 

---

### Impact Explanation

For any block that contains a deprecated (Cairo 0) `DECLARE` transaction:

1. **Wrong `state_diff_commitment`** — `chain_deprecated_declared_classes` receives an empty slice instead of the actual declared class hashes. The Poseidon hash is different from what the OS/prover computes, so the sequencer's `state_diff_commitment` is wrong.

2. **Wrong `state_diff_length` in `concatenated_counts`** — `ThinStateDiff::len()` returns a value that is `N` too small (where `N` is the number of deprecated classes declared in the block). `concatenated_counts` packs this into a 64-bit field, so the packed felt is wrong.

3. **Wrong block hash** — Both corrupted values are chained into the Poseidon block hash. The sequencer broadcasts and stores a block hash that the OS/prover cannot reproduce.

4. **Wrong `ThinStateDiff` in storage and state sync** — Validators and syncing nodes receive a `ThinStateDiff` with empty `deprecated_declared_classes`, so they cannot reconstruct the correct state diff commitment either.

This matches the **Critical** impact: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input*, and the **High** impact: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value*.

---

### Likelihood Explanation

Deprecated (Cairo 0) `DECLARE` transactions (version 0 and version 1) are still valid Starknet transactions. The gateway does not reject them. Any user who submits a valid deprecated declare transaction triggers the bug. The sequencer will accept and execute it, then silently produce a wrong block hash. The bug is unprivileged and requires only a standard user transaction.

---

### Recommendation

**Short term:** Add `deprecated_declared_classes` to `CommitmentStateDiff` (or carry it alongside it through `BlockExecutionSummary`). Populate it from `StateMaps.declared_contracts` by filtering for entries where the class has no `compiled_class_hash` (i.e., Cairo 0 classes). Update `CommitmentStateDiff::from(StateMaps)` and `From<CommitmentStateDiff> for ThinStateDiff` accordingly, and remove the `TODO(AlonH)` placeholder.

**Long term:** Add a test in `block_builder_test` that executes a deprecated `DECLARE` transaction and asserts that the resulting `ThinStateDiff.deprecated_declared_classes` is non-empty and that `calculate_state_diff_hash` produces the expected commitment. Add a similar regression test in `state_diff_hash_test`.

---

### Proof of Concept

1. Submit a valid Cairo 0 `DECLARE` transaction (version 0 or 1) to the sequencer gateway.
2. The batcher executes it; `StateMaps.declared_contracts` records `{class_hash: true}`.
3. `CommitmentStateDiff::from(state_maps)` discards `declared_contracts`; `CommitmentStateDiff.class_hash_to_compiled_class_hash` is empty for this class (Cairo 0 has no compiled class hash).
4. `ThinStateDiff::from(commitment_state_diff)` sets `deprecated_declared_classes = []`.
5. `calculate_state_diff_hash` chains `0` deprecated classes; the OS/prover chains `1`.
6. `ThinStateDiff::len()` returns `N` instead of `N+1`; `concatenated_counts` encodes the wrong length.
7. `calculate_block_hash` produces a hash that differs from the OS output.
8. The sequencer stores and broadcasts this wrong block hash; proof verification fails.

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L679-688)
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
```

**File:** crates/blockifier/src/state/cached_state.rs (L690-701)
```rust
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

**File:** crates/apollo_batcher/src/block_builder.rs (L198-201)
```rust
    pub fn thin_state_diff(&self) -> ThinStateDiff {
        // TODO(Ayelet): Remove the clones.
        ThinStateDiff::from(self.commitment_state_diff.clone())
    }
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L30-41)
```rust
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
    let mut hash_chain = HashChain::new();
    hash_chain = hash_chain.chain(&STARKNET_STATE_DIFF0);
    hash_chain = chain_deployed_contracts(&state_diff.deployed_contracts, hash_chain);
    hash_chain = chain_declared_classes(&state_diff.class_hash_to_compiled_class_hash, hash_chain);
    hash_chain =
        chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
    hash_chain = hash_chain.chain(&Felt::ONE) // placeholder.
        .chain(&Felt::ZERO); // placeholder.
    hash_chain = chain_storage_diffs(&state_diff.storage_diffs, hash_chain);
    hash_chain = chain_nonces(&state_diff.nonces, hash_chain);
    StateDiffCommitment(PoseidonHash(hash_chain.get_poseidon_hash()))
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-282)
```rust
    Ok(BlockHash(
        HashChain::new()
            .chain(&block_hash_version.clone().into())
            .chain(&partial_block_hash_components.block_number.0.into())
            .chain(&state_root.0)
            .chain(&partial_block_hash_components.sequencer.0)
            .chain(&partial_block_hash_components.timestamp.0.into())
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
            .chain(&block_commitments.event_commitment.0)
            .chain(&block_commitments.receipt_commitment.0)
            .chain_iter(
                gas_prices_to_hash(
                    &partial_block_hash_components.l1_gas_price,
                    &partial_block_hash_components.l1_data_gas_price,
                    &partial_block_hash_components.l2_gas_price,
                    &block_hash_version,
                )
                .iter(),
            )
            .chain(
                &Felt::try_from(&partial_block_hash_components.starknet_version)
                    .expect("Expect ASCII version"),
            )
            .chain(&Felt::ZERO)
            .chain(&previous_block_hash.0)
            .get_poseidon_hash(),
    ))
}
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-323)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );
```

**File:** crates/starknet_api/src/state.rs (L110-121)
```rust
    pub fn len(&self) -> usize {
        let mut result = 0usize;
        result += self.deployed_contracts.len();
        result += self.class_hash_to_compiled_class_hash.len();
        result += self.deprecated_declared_classes.len();
        result += self.nonces.len();

        for (_contract_address, storage_diffs) in &self.storage_diffs {
            result += storage_diffs.len();
        }
        result
    }
```
