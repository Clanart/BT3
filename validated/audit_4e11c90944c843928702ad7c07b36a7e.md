### Title
`CommitmentStateDiff`→`ThinStateDiff` Conversion Silently Drops `deprecated_declared_classes`, Producing Wrong `state_diff_commitment` and `state_diff_length` in Block Hash - (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally sets `deprecated_declared_classes: Vec::new()`. `BlockExecutionArtifacts::new()` feeds this incomplete `ThinStateDiff` into `calculate_block_commitments`, which derives both the `state_diff_commitment` (Poseidon hash over all state-diff fields) and the `state_diff_length` packed into `concatenated_counts`. Any block that includes a deprecated (Cairo 0) class declaration will therefore carry a wrong `state_diff_commitment` and a wrong `state_diff_length` in its block hash — the exact same "missing sub-component" pattern as the external dust-amount bug.

---

### Finding Description

**Root cause — the dropped field:** [1](#0-0) 

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
            deprecated_declared_classes: Vec::new(),   // ← always empty
        }
    }
}
```

`CommitmentStateDiff` itself is produced from `StateMaps` and also drops the `declared_contracts` (deprecated-class) field: [2](#0-1) 

**Propagation into block-hash computation:**

`BlockExecutionArtifacts::new()` passes this truncated `ThinStateDiff` directly to `calculate_block_commitments`: [3](#0-2) 

`calculate_block_commitments` then computes two values that enter the block hash:

1. **`state_diff_commitment`** — via `calculate_state_diff_hash`, which explicitly chains `deprecated_declared_classes`: [4](#0-3) 

   Because the field is always empty, `chain_deprecated_declared_classes` chains only the count `0`, producing a hash that differs from the correct one whenever deprecated classes are declared.

2. **`state_diff_length`** — via `ThinStateDiff::len()`, which counts `deprecated_declared_classes.len()`: [5](#0-4) 

   This length is packed into `concatenated_counts` (the `packed_lengths` field of the block hash): [6](#0-5) 

Both corrupted values are then chained into the final block hash:

<cite repo="bsaldua/sequencer--015" path="crates/starknet_api/

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L679-687)
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

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L30-42)
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
}
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-323)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );
```
