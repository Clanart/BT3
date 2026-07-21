### Title
Wrong `state_diff_commitment` and `state_diff_length` in Block Hash Due to Unconditional Erasure of Deprecated Declared Classes in `CommitmentStateDiff`→`ThinStateDiff` Conversion - (`crates/blockifier/src/state/cached_state.rs`)

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally sets `deprecated_declared_classes: Vec::new()`. This `ThinStateDiff` is the sole input to `calculate_block_commitments`, which derives both the `state_diff_commitment` (Poseidon hash of the state diff) and the `state_diff_length` packed into `concatenated_counts`. Both values feed directly into `calculate_block_hash`. For any block that includes a deprecated (Cairo 0) class declaration, the sequencer commits a block hash whose `state_diff_commitment` and `state_diff_length` fields are computed over a truncated state diff, producing a wrong authoritative block hash.

### Finding Description

**Root cause — the erasure:**

`CommitmentStateDiff` has four fields (`address_to_class_hash`, `address_to_nonce`, `storage_updates`, `class_hash_to_compiled_class_hash`) and carries no `deprecated_declared_classes`. The `From` impl therefore hard-codes an empty vec:

```rust
// crates/blockifier/src/state/cached_state.rs  lines 756-767
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

**Propagation into block commitments:**

`BlockExecutionArtifacts::new` passes exactly this truncated `ThinStateDiff` to `calculate_block_commitments`:

```rust
// crates/apollo_batcher/src/block_builder.rs  lines 170-176
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [2](#0-1) 

Inside `calculate_block_commitments`, two commitment values are derived from the truncated diff:

```rust
// crates/starknet_api/src/block_hash/block_hash_calculator.rs  lines 318-327
let concatenated_counts = concat_counts(
    transactions_data.len(),
    event_leaf_elements.len(),
    state_diff.len(),          // ← missing deprecated_declared_classes count
    l1_da_mode,
);
// ...
let state_diff_task = spawn_measured_task(move || calculate_state_diff_hash(&state_diff));
// ← hash computed over diff with deprecated_declared_classes = []
```

<cite repo="Annirich/sequencer--021" path="crates/starknet_api/src/block_hash/block_hash_calculator.rs" start="

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L756-767)
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

**File:** crates/apollo_batcher/src/block_builder.rs (L170-176)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
```
