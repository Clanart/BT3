Looking at the codebase for an analog to the DEFAULTVALIDATOR immutability bug — specifically a default/initial value in commitment, proof, or state-diff paths that cannot be updated and corrupts a commitment invariant.

### Title
`CommitmentStateDiff` permanently drops `deprecated_declared_classes`, producing wrong `state_diff_commitment` and block hash for blocks with Cairo 0 declarations - (File: `crates/blockifier/src/state/cached_state.rs`)

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion hardcodes `deprecated_declared_classes: Vec::new()`. Because `CommitmentStateDiff` has no field for deprecated declared classes, and `StateMaps.declared_contracts` (which tracks Cairo 0 class declarations) is silently dropped in `From<StateMaps> for CommitmentStateDiff`, any block containing a Cairo 0 `DeclareTransaction` produces a `ThinStateDiff` with an empty `deprecated_declared_classes`. This causes `calculate_state_diff_hash` to compute a wrong `state_diff_commitment`, and `ThinStateDiff::len()` to return a wrong `state_diff_length`, both of which feed directly into the block hash calculation.

### Finding Description

**Root cause — `CommitmentStateDiff` has no `deprecated_declared_classes` field and `StateMaps.declared_contracts` is silently dropped:**

`StateMaps` tracks all class declarations in `declared_contracts: HashMap<ClassHash, bool>`. Cairo 0 (deprecated) class declarations appear *only* in `declared_contracts` — they have no compiled class hash, so they are absent from `compiled_class_hashes`. [1](#0-0) 

When `StateMaps` is converted to `CommitmentStateDiff`, `declared_contracts` is completely ignored: [2](#0-1) 

`CommitmentStateDiff` has no `deprecated_declared_classes` field at all: [3](#0-2) 

**Hardcoded empty default — the analog to DEFAULTVALIDATOR:**

When `CommitmentStateDiff` is converted to `ThinStateDiff`, `deprecated_declared_classes` is hardcoded to `Vec::new()` with no mechanism to populate it: [4](#0-3) 

**Corrupted commitment path:**

This `ThinStateDiff` (with empty `deprecated_declared_classes`) is passed directly to `calculate_block_commitments` inside `BlockExecutionArtifacts::new`: [5](#0-4) 

`calculate_state_diff_hash` explicitly chains `deprecated_declared_classes` into the Poseidon hash: [6](#0-5) 

`ThinStateDiff::len()` includes `deprecated_declared_classes.len()` in the count used for `state_diff_length` inside `concatenated_counts`: [7](#0-6) 

Both `state_diff_commitment` and `concatenated_counts` (encoding `state_diff_length`) are chained into the final block hash: [8](#0-7) 

The resulting wrong `PartialBlockHashComponents` is stored in MDBX and used by `finalize_commitment_output` to compute and persist the final block hash: [9](#0-8) 

### Impact Explanation

For every block that contains at least one Cairo 0 `DeclareTransaction`:

- `state_diff_commitment` is computed over a `ThinStateDiff` missing the declared class hashes → **wrong `StateDiffCommitment` stored and broadcast**.
- `state_diff_length` encoded in `concatenated_counts` is under-counted by the number of deprecated declarations → **wrong `concatenated_counts` field in the block hash**.
- The final `BlockHash` written to storage and returned by the RPC is wrong.
- The OS (SNOS) guesses `header_commitments` via hints; if it uses the sequencer-supplied wrong `state_diff_commitment`, the proven block hash diverges from the correct one, causing proof verification failure or acceptance of a proof over a wrong state.

This matches: **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input**, and **High — RPC execution returns an authoritative-looking wrong value**.

### Likelihood Explanation

Cairo 0 class declarations remain valid Starknet transactions. The codebase itself contains a TODO acknowledging future redeclaration of Cairo 0 classes: [10](#0-9) 

Any unprivileged user submitting a `DeclareTransaction` for a Cairo 0 contract triggers the bug. No special permissions or network position are required.

### Recommendation

1. Add a `deprecated_declared_classes: Vec<ClassHash>` field to `CommitmentStateDiff`.
2. In `From<StateMaps> for CommitmentStateDiff`, populate it from `diff.declared_contracts` by filtering for class hashes that are **not** present in `diff.compiled_class_hashes` (i.e., Cairo 0 declarations).
3. In `From<CommitmentStateDiff> for ThinStateDiff`, propagate `commitment_state_diff.deprecated_declared_classes` instead of `Vec::new()`.

### Proof of Concept

1. A user submits a `DeclareTransaction` for a Cairo 0 class `C`.
2. The blockifier records `(C, true)` in `StateMaps.declared_contracts`; `compiled_class_hashes` is empty for `C`.
3. `From<StateMaps> for CommitmentStateDiff` drops `declared_contracts` entirely — `C` is lost.
4. `From<CommitmentStateDiff> for ThinStateDiff` sets `deprecated_declared_classes: Vec::new()`.
5. `calculate_state_diff_hash` hashes `[..., 0 deprecated classes, ...]` instead of `[..., 1, C, ...]` → wrong `StateDiffCommitment`.
6. `concat_counts(n_txs, n_events, state_diff.len(), da_mode)` uses `len()` that excludes `C` → wrong `concatenated_counts`.
7. `calculate_block_hash` chains both wrong values → wrong `BlockHash` written to MDBX and returned by RPC.
8. Any proof generated over this block either fails verification (if the OS independently computes the correct hash) or attests to a wrong block hash. [11](#0-10) [12](#0-11) [13](#0-12)

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

**File:** crates/blockifier/src/state/cached_state.rs (L390-396)
```rust
            // TODO(Yoni, 1/8/2024): consider forbid redeclaration of Cairo 0, to be able to use
            // strict subtraction here, for completeness.
            declared_contracts: subtract_mappings(
                &self.declared_contracts,
                &other.declared_contracts,
            ),
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

**File:** crates/blockifier/src/state/cached_state.rs (L690-702)
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L245-282)
```rust
pub fn calculate_block_hash(
    partial_block_hash_components: &PartialBlockHashComponents,
    state_root: GlobalRoot,
    previous_block_hash: BlockHash,
) -> StarknetApiResult<BlockHash> {
    let block_hash_version: BlockHashVersion =
        partial_block_hash_components.starknet_version.try_into()?;
    let block_commitments = &partial_block_hash_components.header_commitments;
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

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L520-525)
```rust
                let block_hash = calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?;
                Ok(FinalBlockCommitment { height, block_hash: Some(block_hash), global_root })
```
