### Title
`From<CommitmentStateDiff> for ThinStateDiff` Silently Drops Deprecated Declared Classes, Producing Wrong State-Diff Commitment and Block Hash - (File: crates/blockifier/src/state/cached_state.rs)

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally sets `deprecated_declared_classes: Vec::new()`. Because `BlockExecutionArtifacts::new` feeds this conversion directly into `calculate_block_commitments`, any block that contains a Cairo 0 (deprecated) class declaration will have its `state_diff_commitment` and `concatenated_counts` (state-diff length) computed without those classes. Both fields are chained into the Poseidon block hash, so the sequencer stores and broadcasts a wrong block hash for every such block.

### Finding Description

`CommitmentStateDiff` has no `deprecated_declared_classes` field: [1](#0-0) 

The `From` implementation therefore hard-codes an empty vec: [2](#0-1) 

`BlockExecutionArtifacts::new` calls this conversion to build the `ThinStateDiff` passed to `calculate_block_commitments`: [3](#0-2) 

Inside `calculate_block_commitments`, two values are derived from the (now-truncated) `ThinStateDiff`:

1. `state_diff_commitment` — via `calculate_state_diff_hash`, which explicitly chains `deprecated_declared_classes`: [4](#0-3) 

2. `concatenated_counts` — via `state_diff.len()`, which counts `deprecated_declared_classes.len()`: [5](#0-4) 

Both are chained into the final block hash: [6](#0-5) 

The same truncated conversion is also used by `BlockExecutionArtifacts::thin_state_diff()`: [7](#0-6) 

which is the value written to storage and propagated to syncing peers.

### Impact Explanation

For any block that includes a deprecated (Cairo 0) class declaration:

- `state_diff_commitment` is computed as if no deprecated classes were declared — a different Poseidon hash than the correct one.
- `concatenated_counts` encodes a state-diff length that is too small by the number of deprecated declarations.
- Both wrong values are chained into `calculate_block_hash`, so the stored block hash is wrong.
- The wrong block hash is returned by `get_block_hash` (RPC), used as `previous_block_hash` for the next block, and fed into `validate_proof_block_hash` for proof-facts validation.

This matches: **High — RPC execution / pending view returns an authoritative-looking wrong value**, and also touches **Critical — wrong state/class hash from blockifier execution logic for accepted input**.

### Likelihood Explanation

Cairo 0 (`Declare v0/v1`) transactions are still accepted by the gateway and processed by the blockifier. Any user (no privilege required) can submit a deprecated class declaration. The sequencer will include it in a block, silently compute the wrong commitment, and store the wrong block hash. The bug is triggered by a single such transaction.

### Recommendation

`CommitmentStateDiff` must be extended with a `deprecated_declared_classes: Vec<ClassHash>` field, populated by the blockifier when it processes `DeclareTransaction` for Cairo 0 classes. The `From<CommitmentStateDiff> for ThinStateDiff` conversion must then propagate that field instead of hard-coding `Vec::new()`. The existing TODO comment acknowledges the gap; it must be resolved before deprecated declarations can appear in production blocks.

### Proof of Concept

1. Submit a `Declare v1` (Cairo 0) transaction declaring class hash `0xdeadbeef`.
2. The sequencer executes it; the blockifier records nothing in `CommitmentStateDiff.class_hash_to_compiled_class_hash` (no compiled hash for Cairo 0) and nothing in any other field.
3. `BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff)` → `deprecated_declared_classes = []`.
4. `calculate_state_diff_hash` chains `0` (count) for deprecated classes instead of `1, 0xdeadbeef`.
5. `state_diff.len()` returns `N` instead of `N+1`, so `concatenated_counts` encodes the wrong length.
6. `calculate_block_hash` produces hash `H_wrong ≠ H_correct`.
7. `set_global_root_and_block_hash` stores `H_wrong`; RPC `starknet_getBlockWithTxHashes` returns `H_wrong` as the authoritative block hash.
8. The next block's `previous_block_hash` is `H_wrong`, propagating the error to all subsequent block hashes. [2](#0-1) [8](#0-7) [9](#0-8)

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L700-710)
```rust
#[cfg_attr(feature = "transaction_serde", derive(serde::Serialize, serde::Deserialize))]
#[derive(Clone, Debug, Default, Eq, PartialEq)]
pub struct CommitmentStateDiff {
    // Contract instance attributes (per address).
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,

    // Global attributes.
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
```

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

**File:** crates/apollo_batcher/src/block_builder.rs (L148-195)
```rust
    pub async fn new(
        block_summary: BlockExecutionSummary,
        execution_data: BlockTransactionExecutionData,
        final_n_executed_txs: usize,
    ) -> Self {
        #[cfg(feature = "os_input")]
        let initial_reads = block_summary.initial_reads;
        let BlockExecutionSummary {
            state_diff: commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            block_info,
            // TODO(Yoav): Remove the ".." when the os_input feature is removed.
            ..
        } = block_summary;
        let l1_da_mode = L1DataAvailabilityMode::from_use_kzg_da(block_info.use_kzg_da);
        let transactions_data =
            prepare_txs_hashing_data(&execution_data.execution_infos_and_signatures);
        // TODO(Ayelet): Remove the clones.
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
        record_and_log_block_commitment_measurements(block_info.block_number, measurements);
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
        let l2_gas_used = execution_data.l2_gas_used();
        Self {
            execution_data,
            commitment_state_diff,
            compressed_state_diff,
            #[cfg(feature = "os_input")]
            initial_reads,
            bouncer_weights,
            l2_gas_used,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            final_n_executed_txs,
            partial_block_hash_components,
        }
    }
```

**File:** crates/apollo_batcher/src/block_builder.rs (L210-213)
```rust
    pub fn thin_state_diff(&self) -> ThinStateDiff {
        // TODO(Ayelet): Remove the clones.
        ThinStateDiff::from(self.commitment_state_diff.clone())
    }
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

**File:** crates/starknet_api/src/state.rs (L111-122)
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-327)
```rust
    let concatenated_counts = concat_counts(
        transactions_data.len(),
        event_leaf_elements.len(),
        state_diff.len(),
        l1_da_mode,
    );

    let n_txs = transactions_data.len();
    let n_events = event_leaf_elements.len();
    let state_diff_length = state_diff.len();
```
