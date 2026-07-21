### Title
`CommitmentStateDiff` Missing `deprecated_declared_classes` Produces Wrong State Diff Commitment and Block Hash — (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

`CommitmentStateDiff` is a stripped-down, modified version of the full state diff that omits the `deprecated_declared_classes` field. This is the direct sequencer analog of the "Not standard ERC20" bug: a modified structure missing a key field causes the commitment calculation to silently drop Cairo 0 (deprecated) class declarations, producing a wrong `state_diff_commitment`, wrong `concatenated_counts`, and ultimately a wrong block hash for any block that includes a deprecated class declaration.

---

### Finding Description

`CommitmentStateDiff` is defined in `crates/blockifier/src/state/cached_state.rs` with four fields and no `deprecated_declared_classes`: [1](#0-0) 

`StateMaps` (the blockifier's internal write-set) tracks Cairo 0 class declarations in `declared_contracts: HashMap<ClassHash, bool>`, but **not** in `compiled_class_hashes`. When `CommitmentStateDiff::from(StateMaps)` is called, `declared_contracts` is entirely ignored: [2](#0-1) 

The `From<CommitmentStateDiff> for ThinStateDiff` conversion then hardcodes the missing field to an empty vector: [3](#0-2) 

This `ThinStateDiff` (with `deprecated_declared_classes = Vec::new()`) is passed directly into `calculate_block_commitments` inside `BlockExecutionArtifacts::new`: [4](#0-3) 

`calculate_block_commitments` calls `calculate_state_diff_hash`, which chains `deprecated_declared_classes` into the Poseidon hash: [5](#0-4) 

Specifically, `chain_deprecated_declared_classes` encodes the count and sorted list of deprecated class hashes: [6](#0-5) 

Additionally, `ThinStateDiff::len()` counts `deprecated_declared_classes.len()` as part of the state diff length: [7](#0-6) 

This length feeds `concat_counts` inside `calculate_block_commitments`: [8](#0-7) 

Both the `state_diff_commitment` and `concatenated_counts` are then chained into the final block hash: [9](#0-8) 

The resulting `BlockHeaderCommitments` is stored in `PartialBlockHashComponents`, which is persisted to storage and used for the final block hash calculation: [10](#0-9) 

---

### Impact Explanation

For any block that includes a deprecated (Cairo 0) class declaration:

1. **`state_diff_commitment`** is wrong — the Poseidon hash chains `0` deprecated classes instead of the actual set, producing a different `StateDiffCommitment` value.
2. **`concatenated_counts`** is wrong — the state diff length is under-counted by the number of deprecated class declarations, corrupting the packed field in the block hash.
3. **Block hash** is wrong — both corrupted fields are chained into `calculate_block_hash`, so the final `BlockHash` diverges from what any correct verifier (OS, prover, sync peer) would compute.
4. **`PartialBlockHash`** used for consensus proposal commitment is wrong — `PartialBlockHash::from_partial_block_hash_components` uses the same `calculate_block_hash` path with zeroed root/parent, so the proposal commitment validators compare against is also wrong.

This matches: **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input.**

---

### Likelihood Explanation

Cairo 0 class declarations (`DECLARE` v0/v1 transactions) are still part of the Starknet protocol and the blockifier supports executing them. The `StateMaps.declared_contracts` field confirms the blockifier tracks them at runtime. The `StarknetClientStateDiff` conversion also silently drops them (`old_declared_contracts: Default::default()`), and the TODO comment in the conversion acknowledges the omission is intentional pending a structural change. Whether the gateway currently admits new `DECLARE` v0/v1 transactions is not definitively gated in the code visible here, making the trigger plausible for any node that still accepts such transactions.

---

### Recommendation

Add `deprecated_declared_classes: Vec<ClassHash>` to `CommitmentStateDiff`. Populate it from `StateMaps.declared_contracts` by collecting keys whose value is `true` and that have no corresponding entry in `compiled_class_hashes` (i.e., Cairo 0 classes). Update `From<CommitmentStateDiff> for ThinStateDiff` to propagate this field instead of hardcoding `Vec::new()`. Remove the TODO comment once the fix is in place.

---

### Proof of Concept

1. A user submits a `DECLARE` v0/v1 transaction declaring a Cairo 0 class; the sequencer accepts and executes it.
2. The blockifier records `(class_hash, true)` in `StateMaps.declared_contracts` but nothing in `compiled_class_hashes`.
3. `CommitmentStateDiff::from(state_maps)` drops `declared_contracts` entirely — the deprecated class is lost.
4. `ThinStateDiff::from(commitment_state_diff)` sets `deprecated_declared_classes = Vec::new()`.
5. `calculate_state_diff_hash` chains `0` deprecated classes → wrong `state_diff_commitment`.
6. `ThinStateDiff::len()` returns a value that is `N` too small (where `N` = number of deprecated declarations) → wrong `concatenated_counts`.
7. `calculate_block_hash` chains both corrupted values → wrong block hash stored and broadcast.
8. Any verifier (OS, proof system, sync peer) recomputing the block hash from the actual state diff (which includes the deprecated class) will compute a different hash, causing proof rejection or sync failure.

### Citations

**File:** crates/blockifier/src/state/cached_state.rs (L667-677)
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

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L71-80)
```rust
fn chain_deprecated_declared_classes(
    deprecated_declared_classes: &[ClassHash],
    hash_chain: HashChain,
) -> HashChain {
    let mut sorted_deprecated_declared_classes = deprecated_declared_classes.to_vec();
    sorted_deprecated_declared_classes.sort_unstable();
    hash_chain
        .chain(&sorted_deprecated_declared_classes.len().into())
        .chain_iter(sorted_deprecated_declared_classes.iter().map(|class_hash| &class_hash.0))
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

**File:** crates/apollo_batcher/src/commitment_manager/commitment_manager_impl.rs (L520-525)
```rust
                let block_hash = calculate_block_hash(
                    &partial_block_hash_components,
                    global_root,
                    previous_block_hash,
                )?;
                Ok(FinalBlockCommitment { height, block_hash: Some(block_hash), global_root })
```
