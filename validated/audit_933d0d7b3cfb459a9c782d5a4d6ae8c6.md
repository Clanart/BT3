### Title
`deprecated_declared_classes` Silently Dropped from `state_diff_commitment` and `concatenated_counts` — (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

When the blockifier finalizes a block, `StateMaps` (which tracks all state changes including `declared_contracts` for deprecated/Cairo-0 class declarations) is converted to `CommitmentStateDiff` via `From<StateMaps> for CommitmentStateDiff`. This conversion silently drops `declared_contracts`. The subsequent `From<CommitmentStateDiff> for ThinStateDiff` then hardcodes `deprecated_declared_classes: Vec::new()`. The resulting `ThinStateDiff` is fed directly into `calculate_block_commitments`, which computes both the `state_diff_commitment` (Poseidon hash) and the `concatenated_counts` field (which encodes `state_diff.len()`). Both values are wrong for any block containing a deprecated (V0/V1) Declare transaction, because the declared class hashes are never included.

---

### Finding Description

**Root cause — two-step silent drop:**

**Step 1** — `From<StateMaps> for CommitmentStateDiff` drops `declared_contracts`:

```rust
// crates/blockifier/src/state/cached_state.rs  lines 679-688
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: StorageDiff::from(StorageView(diff.storage)),
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
            // diff.declared_contracts is silently dropped
        }
    }
}
``` [1](#0-0) 

**Step 2** — `From<CommitmentStateDiff> for ThinStateDiff` hardcodes an empty vec:

```rust
// crates/blockifier/src/state/cached_state.rs  lines 690-701
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            ...
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
}
``` [2](#0-1) 

**Step 3** — `BlockExecutionArtifacts::new` passes this truncated `ThinStateDiff` to `calculate_block_commitments`:

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

**Step 4** — `calculate_block_commitments` uses `state_diff.len()` for `concatenated_counts` and `state_diff_length`, and spawns `calculate_state_diff_hash` which chains `deprecated_declared_classes` into the Poseidon hash:

```rust
// crates/starknet_api/src/block_hash/block_hash_calculator.rs  lines 318-327
let concatenated_counts = concat_counts(
    transactions_data.len(),
    event_leaf_elements.len(),
    state_diff.len(),   // wrong: missing deprecated class count
    l1_da_mode,
);
let state_diff_length = state_diff.len();  // wrong
``` [4](#0-3) 

```rust
// crates/starknet_api/src/block_hash/state_diff_hash.rs  lines 30-41
pub fn calculate_state_diff_hash(state_diff: &ThinStateDiff) -> StateDiffCommitment {
    ...
    hash_chain = chain_deprecated_declared_classes(
        &state_diff.deprecated_declared_classes,  // always empty from batcher path
        hash_chain
    );
    ...
}
``` [5](#0-4) 

**Trigger path:** A deprecated Declare transaction (V0 or V1) writes `declared_contracts: {class_hash: true}` into `StateMaps` but nothing into `compiled_class_hashes`. Sierra classes (V2/V3) write into `compiled_class_hashes` and therefore survive the conversion. Deprecated classes have no `compiled_class_hash` and are exclusively tracked in `declared_contracts`, which is the field that is dropped. [6](#0-5) 

The `finalize_block` function confirms the conversion path:

```rust
// crates/blockifier/src/blockifier/transaction_executor.rs  lines 273, 300
let state_diff = block_state.to_state_diff()?.state_maps;
...
Ok(BlockExecutionSummary {
    state_diff: state_diff.into(),  // StateMaps → CommitmentStateDiff, drops declared_contracts
    ...
})
``` [7](#0-6) 

---

### Impact Explanation

For every block that contains at least one deprecated (V0/V1) Declare transaction:

1. **`state_diff_commitment`** (a `StateDiffCommitment(PoseidonHash(...))`) stored in the block header and used in the block hash is computed over a `ThinStateDiff` that is missing the deprecated class hashes. The SNOS, which independently computes the state diff commitment from the actual on-chain state changes, will include those class hashes and produce a different value. This breaks the proof invariant: the sequencer's committed `state_diff_commitment` ≠ the SNOS-computed value, causing proof verification to fail.

2. **`concatenated_counts`** (packed into the block hash via `concat_counts`) encodes `state_diff_length = state_diff.len()`. Because `deprecated_declared_classes` is empty, `state_diff_length` is under-counted by the number of deprecated class declarations in the block. This corrupts the `concatenated_counts` field of the block hash.

3. **`state_diff_length`** stored in `StorageBlockHeader` (and used by the P2P sync layer to validate received state diff chunks) is also wrong, potentially causing sync peers to reject or accept wrong-length state diffs. [8](#0-7) [9](#0-8) 

The corrupted values are: the `state_diff_commitment` Poseidon hash (missing deprecated class hash leaves), the `concatenated_counts` felt (wrong `state_diff_length` sub-field), and the `state_diff_length` header field.

---

### Likelihood Explanation

Deprecated Declare transactions (V0/V1) are still part of the Starknet API and are represented as first-class transaction types throughout the codebase (`DeclareTransaction::V0`, `DeclareTransaction::V1`). The RPC spec served by the node explicitly lists `DECLARE_TXN_V0` and `DECLARE_TXN_V1`. Any unprivileged user who submits a V0 or V1 Declare transaction that passes gateway validation triggers this path. The TODO comment (`// TODO(AlonH): Remove this when the structure of storage diffs changes.`) confirms the gap is known but unresolved. [10](#0-9) 

---

### Recommendation

`CommitmentStateDiff` must be extended with a `deprecated_declared_classes: Vec<ClassHash>` field, populated from `StateMaps.declared_contracts` (entries where `value == true` and no corresponding `compiled_class_hash` exists). The `From<CommitmentStateDiff> for ThinStateDiff` conversion must then propagate this field instead of hardcoding `Vec::new()`. The TODO comment should be resolved by making this structural change now rather than deferring it.

Alternatively, `finalize_block` can extract `deprecated_declared_classes` directly from `StateMaps.declared_contracts` before the `into()` conversion and pass them separately into `BlockExecutionSummary`, then inject them into the `ThinStateDiff` used for commitment calculation.

---

### Proof of Concept

1. Submit a `DeclareTransaction::V1` (deprecated Cairo-0 class) through the gateway.
2. The transaction executes; `StateMaps.declared_contracts` = `{C: true}`, `StateMaps.compiled_class_hashes` = `{}`.
3. `finalize_block` calls `state_diff.into()` → `CommitmentStateDiff { class_hash_to_compiled_class_hash: {}, ... }` — `C` is gone.
4. `BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff.clone())` → `deprecated_declared_classes: []`.
5. `calculate_state_diff_hash` hashes `chain_deprecated_declared_classes(&[], ...)` — `C` is not chained.
6. `ThinStateDiff::len()` returns `N` instead of `N+1`; `concatenated_counts` encodes the wrong length.
7. The SNOS independently computes `chain_deprecated_declared_classes(&[C], ...)` → different hash.
8. Proof verification fails: sequencer's `state_diff_commitment` ≠ SNOS `state_diff_commitment`. [11](#0-10) [12](#0-11) [13](#0-12) [14](#0-13)

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L320-330)
```rust
#[cfg_attr(feature = "transaction_serde", derive(serde::Serialize, serde::Deserialize))]
#[derive(Clone, Debug, Default, PartialEq, Eq)]
pub struct StateMaps {
    pub nonces: HashMap<ContractAddress, Nonce>,
    pub class_hashes: HashMap<ContractAddress, ClassHash>,
    // TODO(Yoni): consider changing type to HashMap<ContractAddress, HashMap<StorageKey, Felt>>.
    #[cfg_attr(feature = "transaction_serde", serde(with = "storage_map_serializer"))]
    pub storage: HashMap<StorageEntry, Felt>,
    pub compiled_class_hashes: HashMap<ClassHash, CompiledClassHash>,
    pub declared_contracts: HashMap<ClassHash, bool>,
}
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

**File:** crates/apollo_batcher/src/block_builder.rs (L142-183)
```rust
impl BlockExecutionArtifacts {
    pub async fn new(
        BlockExecutionSummary {
            state_diff: commitment_state_diff,
            compressed_state_diff,
            bouncer_weights,
            casm_hash_computation_data_sierra_gas,
            casm_hash_computation_data_proving_gas,
            compiled_class_hashes_for_migration,
            block_info,
        }: BlockExecutionSummary,
        execution_data: BlockTransactionExecutionData,
        final_n_executed_txs: usize,
    ) -> Self {
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

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L29-42)
```rust
/// ).
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

**File:** crates/blockifier/src/blockifier/transaction_executor.rs (L273-307)
```rust
    let state_diff = block_state.to_state_diff()?.state_maps;

    let compressed_state_diff = if block_context.versioned_constants.enable_stateful_compression {
        Some(compress(&state_diff, block_state, alias_contract_address)?.into())
    } else {
        None
    };

    // Take CasmHashComputationData from bouncer,
    // and verify that class hashes are the same.
    let casm_hash_computation_data_sierra_gas =
        mem::take(bouncer.get_mut_casm_hash_computation_data_sierra_gas());
    let casm_hash_computation_data_proving_gas =
        mem::take(bouncer.get_mut_casm_hash_computation_data_proving_gas());

    assert_eq!(
        casm_hash_computation_data_sierra_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .collect::<std::collections::HashSet<_>>(),
        casm_hash_computation_data_proving_gas
            .class_hash_to_casm_hash_computation_gas
            .keys()
            .collect::<std::collections::HashSet<_>>()
    );

    Ok(BlockExecutionSummary {
        state_diff: state_diff.into(),
        compressed_state_diff,
        bouncer_weights: *bouncer.get_bouncer_weights(),
        casm_hash_computation_data_sierra_gas,
        casm_hash_computation_data_proving_gas,
        compiled_class_hashes_for_migration: class_hashes_to_migrate.into_values().collect(),
        block_info: block_context.block_info.clone(),
    })
```

**File:** crates/apollo_storage/src/header.rs (L106-108)
```rust
    /// The length of the state diff, if available.
    pub state_diff_length: Option<usize>,
    /// The number of transactions in this block.
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-103)
```rust
            let target_state_diff_len = storage_reader
                .begin_ro_txn()?
                .get_block_header(block_number)?
                .expect("A header with number lower than the header marker is missing")
                .state_diff_length
                .ok_or(P2pSyncClientError::OldHeaderInStorage {
                    block_number,
                    missing_field: "state_diff_length",
                })?;

            while current_state_diff_len < target_state_diff_len {
                let maybe_state_diff_chunk = state_diff_chunks_response_manager
                    .next()
                    .await
                    .ok_or(ParseDataError::BadPeer(BadPeerError::SessionEndedWithoutFin {
                        type_description: Self::TYPE_DESCRIPTION,
                    }))?;
                let Some(state_diff_chunk) = maybe_state_diff_chunk?.0 else {
                    if current_state_diff_len == 0 {
                        return Ok(None);
                    } else {
                        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                            expected_length: target_state_diff_len,
                            possible_lengths: vec![current_state_diff_len],
                        }));
                    }
                };
                prev_result_len = current_state_diff_len;
                if state_diff_chunk.is_empty() {
                    return Err(ParseDataError::BadPeer(BadPeerError::EmptyStateDiffPart));
                }
                // It's cheaper to calculate the length of `state_diff_part` than the length of
                // `result`.
                current_state_diff_len += state_diff_chunk.len();
                unite_state_diffs(&mut result, state_diff_chunk)?;
            }

            if current_state_diff_len != target_state_diff_len {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                    expected_length: target_state_diff_len,
                    possible_lengths: vec![prev_result_len, current_state_diff_len],
                }));
```

**File:** crates/starknet_api/src/transaction.rs (L353-359)
```rust
#[derive(Debug, Clone, Eq, PartialEq, Hash, Deserialize, Serialize, PartialOrd, Ord)]
pub enum DeclareTransaction {
    V0(DeclareTransactionV0V1),
    V1(DeclareTransactionV0V1),
    V2(DeclareTransactionV2),
    V3(DeclareTransactionV3),
}
```

**File:** crates/starknet_api/src/state.rs (L109-121)
```rust
    /// This has the same value as `state_diff_length` in the corresponding `BlockHeader`.
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
