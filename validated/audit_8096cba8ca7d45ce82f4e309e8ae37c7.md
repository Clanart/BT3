### Title
`CommitmentStateDiff → ThinStateDiff` Conversion Silently Drops Deprecated Declared Classes, Producing Wrong `state_diff_commitment` and `state_diff_length` in Block Hash - (File: crates/blockifier/src/state/cached_state.rs)

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion hardcodes `deprecated_declared_classes: Vec::new()`. This `ThinStateDiff` is the sole input to `calculate_block_commitments` in `BlockExecutionArtifacts::new`. Any block containing a deprecated (Cairo 0) class declaration will produce a `state_diff_commitment` and a `state_diff_length` (packed into `concatenated_counts`) that are both wrong, causing the sequencer to commit and broadcast a block hash that no other node or prover can reproduce.

### Finding Description

**Root cause — structural omission in `CommitmentStateDiff`:**

`CommitmentStateDiff` in `crates/blockifier/src/state/cached_state.rs` has four fields: `address_to_class_hash`, `address_to_nonce`, `storage_updates`, and `class_hash_to_compiled_class_hash`. There is no `deprecated_declared_classes` field. [1](#0-0) 

When `finalize_block` converts `StateMaps` into `CommitmentStateDiff`, the `declared_contracts` field of `StateMaps` (which records every declared class, including deprecated Cairo 0 classes) is silently dropped: [2](#0-1) 

`StateMaps.declared_contracts` is the only place the blockifier records deprecated class declarations: [3](#0-2) 

**Propagation — `CommitmentStateDiff → ThinStateDiff` hardcodes empty deprecated classes:**

The `From<CommitmentStateDiff> for ThinStateDiff` impl hardcodes `deprecated_declared_classes: Vec::new()`: [4](#0-3) 

**Commitment path — both `state_diff_commitment` and `state_diff_length` are computed from this truncated diff:**

In `BlockExecutionArtifacts::new`, the truncated `ThinStateDiff` is passed directly to `calculate_block_commitments`: [5](#0-4) 

Inside `calculate_block_commitments`, two values are derived from this diff:

1. `state_diff_commitment` — via `calculate_state_diff_hash`, which explicitly chains `deprecated_declared_classes` into the Poseidon hash: [6](#0-5) 

2. `state_diff_length` — via `ThinStateDiff::len()`, which counts `deprecated_declared_classes.len()`: [7](#0-6) 

This length is packed into `concatenated_counts` via `concat_counts`: [8](#0-7) 

Both `state_diff_commitment` and `concatenated_counts` are chained into the final block hash: [9](#0-8) 

**The `verify_state_diff_hash` guard does not catch this:**

The committer's optional hash verification re-computes the commitment from the same truncated `ThinStateDiff`, so it produces the same wrong value and passes silently: [10](#0-9) 

### Impact Explanation

For every block that contains at least one deprecated (Cairo 0) class declaration:

- `state_diff_commitment` is wrong — it hashes `0` deprecated classes instead of the actual count and class hashes.
- `state_diff_length` in `concatenated_counts` is wrong — it is smaller than the true length by the number of deprecated declared classes.
- The resulting `BlockHash` is wrong — both corrupted fields are chained into the Poseidon block hash.

The wrong block hash is stored, broadcast to peers, and submitted to the prover. No other honest node or SNOS instance can reproduce it, causing consensus divergence, proof rejection, and potential L1 finality failure. This matches **Critical: Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input**, and **High: RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value** (e.g., `starknet_getStateUpdate` returns a `state_diff_commitment` that does not match the on-chain block hash).

### Likelihood Explanation

Deprecated (Cairo 0) class declarations (`DECLARE` v0/v1) remain valid Starknet transactions. Any user can submit one. The blockifier already handles them (it populates `StateMaps.declared_contracts`). The Apollo sequencer's gateway does not appear to explicitly reject them. The bug is therefore triggered by ordinary user activity, not a privileged or exotic operation.

### Recommendation

1. Add a `deprecated_declared_classes: Vec<ClassHash>` field to `CommitmentStateDiff`.
2. Populate it in `From<StateMaps> for CommitmentStateDiff` by collecting entries from `diff.declared_contracts` whose class hash is **not** present in `diff.compiled_class_hashes` (those are Sierra classes).
3. Update `From<CommitmentStateDiff> for ThinStateDiff` to forward `commitment_state_diff.deprecated_declared_classes` instead of `Vec::new()`.
4. Add a regression test in `block_builder_test.rs` that includes a deprecated class declaration and asserts that the resulting `state_diff_commitment` and `concatenated_counts` match an independently computed reference.

### Proof of Concept

1. A user submits a `DECLARE v0` transaction declaring a Cairo 0 class.
2. The blockifier executes it; `StateMaps.declared_contracts` gains entry `{class_hash_X: true}`. `StateMaps.compiled_class_hashes` is unchanged (no compiled class hash for Cairo 0).
3. `finalize_block` calls `CommitmentStateDiff::from(state_maps)` — `declared_contracts` is dropped; `CommitmentStateDiff.class_hash_to_compiled_class_hash` does not contain `class_hash_X`.
4. `BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff)` — `deprecated_declared_classes` is `[]`.
5. `calculate_block_commitments` computes:
   - `state_diff_commitment` = Poseidon(…, `0` deprecated classes, …) — **wrong**
   - `state_diff_length` = actual_length − 1 — **wrong**
6. `calculate_block_hash` chains both wrong values → wrong `BlockHash`.
7. A validator node independently computes `state_diff_commitment` = Poseidon(…, `1` deprecated class, `class_hash_X`, …) and `state_diff_length` = actual_length → different `BlockHash` → consensus failure / proof rejection. [11](#0-10) [5](#0-4) [6](#0-5)

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L322-330)
```rust
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

**File:** crates/blockifier/src/state/cached_state.rs (L669-677)
```rust
pub struct CommitmentStateDiff {
    // Contract instance attributes (per address).
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,

    // Global attributes.
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
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

**File:** crates/apollo_committer/src/committer.rs (L165-180)
```rust
        let state_diff_commitment = match state_diff_commitment {
            Some(commitment) => {
                if self.config.verify_state_diff_hash {
                    let calculated_commitment = calculate_state_diff_hash(&state_diff);
                    if commitment != calculated_commitment {
                        return Err(CommitterError::StateDiffHashMismatch {
                            provided_commitment: commitment,
                            calculated_commitment,
                            height,
                        });
                    }
                }
                commitment
            }
            None => calculate_state_diff_hash(&state_diff),
        };
```
