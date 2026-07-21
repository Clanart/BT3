### Title
`CommitmentStateDiff → ThinStateDiff` Conversion Silently Drops Deprecated Declared Classes, Producing Wrong State Diff Commitment and Block Hash - (`crates/blockifier/src/state/cached_state.rs`)

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion, which is the sole path used by the block-builder to produce the `ThinStateDiff` fed into `calculate_block_commitments`, unconditionally sets `deprecated_declared_classes: Vec::new()`. Because `CommitmentStateDiff` itself has no field for deprecated (Cairo 0) class declarations, and because `StateMaps → CommitmentStateDiff` silently drops `declared_contracts`, any deprecated `Declare` transaction accepted into a block causes the sequencer to compute a `state_diff_commitment` and a `concatenated_counts` that omit those class hashes. The resulting `PartialBlockHashComponents` and final block hash are therefore wrong for every block that contains at least one deprecated Declare transaction.

### Finding Description

**Step 1 – `StateMaps` tracks deprecated declarations but `CommitmentStateDiff` does not.**

`StateMaps` has a `declared_contracts: HashMap<ClassHash, bool>` field. For a Cairo 0 (deprecated) Declare transaction, the class hash is inserted here with value `true`. There is no corresponding `compiled_class_hash`, so the class hash does **not** appear in `StateMaps.compiled_class_hashes`. [1](#0-0) 

**Step 2 – `CommitmentStateDiff::from(StateMaps)` drops `declared_contracts` entirely.**

The conversion maps `compiled_class_hashes → class_hash_to_compiled_class_hash`, `class_hashes → address_to_class_hash`, `storage → storage_updates`, and `nonces → address_to_nonce`. The `declared_contracts` field is never read. [2](#0-1) 

**Step 3 – `ThinStateDiff::from(CommitmentStateDiff)` hardcodes `deprecated_declared_classes: Vec::new()`.**

This is the only conversion used in the block-builder path. The `TODO(AlonH)` comment acknowledges the placeholder but provides no mechanism to populate the field. [3](#0-2) 

**Step 4 – `BlockExecutionArtifacts::new` passes this truncated `ThinStateDiff` to `calculate_block_commitments`.** [4](#0-3) 

**Step 5 – `calculate_block_commitments` computes both the state diff hash and `concatenated_counts` from the truncated diff.**

`calculate_state_diff_hash` chains `deprecated_declared_classes` into the Poseidon hash via `chain_deprecated_declared_classes`. With an empty vec, the count field is `0` and no class hashes are chained, producing a different hash than the correct one. [5](#0-4) 

`ThinStateDiff::len()` also counts `deprecated_declared_classes.len()`, so `concatenated_counts` encodes a wrong state diff length. [6](#0-5) 

**Step 6 – The wrong `state_diff_commitment` and `concatenated_counts` propagate into the block hash.**

`calculate_block_hash` chains both `block_commitments.state_diff_commitment.0.0` and `block_commitments.concatenated_counts` into the final Poseidon hash. [7](#0-6) 

The `PartialBlockHashComponents` stored in `BlockExecutionArtifacts` and the `ProposalCommitment` derived from it are therefore wrong for any block containing a deprecated Declare transaction. [8](#0-7) 

### Impact Explanation

Any block that includes at least one accepted deprecated (Cairo 0) Declare transaction will have:
- A wrong `StateDiffCommitment` stored in `PartialBlockHashComponents`.
- A wrong `concatenated_counts` (state diff length off by the number of deprecated declared classes).
- A wrong final block hash produced by `calculate_block_hash`.

This is a **Critical** impact: wrong commitment/block hash for accepted input, matching "Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input."

### Likelihood Explanation

Deprecated Declare transactions (v0/v1) are still valid Starknet transaction types. Any unprivileged user can submit one to the gateway. The sequencer will admit and execute it. No special privilege or coordination is required to trigger the bug; a single deprecated Declare transaction in any block is sufficient.

### Recommendation

`CommitmentStateDiff` must be extended with a `deprecated_declared_classes: Vec<ClassHash>` field. `CommitmentStateDiff::from(StateMaps)` must populate it from `declared_contracts` (entries where `value == true` and no corresponding `compiled_class_hash` exists, i.e., Cairo 0 classes). The `From<CommitmentStateDiff> for ThinStateDiff` conversion must then forward this field instead of hardcoding `Vec::new()`. The `TODO(AlonH)` comment should be resolved as part of this fix.

### Proof of Concept

1. Submit a deprecated Declare transaction (Starknet tx type `DECLARE` v0 or v1, declaring a Cairo 0 class) to the sequencer gateway.
2. The gateway admits it; the batcher includes it in a block.
3. After block execution, `BlockExecutionSummary.state_diff` is a `CommitmentStateDiff` with no `deprecated_declared_classes` field. The class hash is only in `StateMaps.declared_contracts`, which was dropped.
4. `BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff.clone())`, producing a `ThinStateDiff` with `deprecated_declared_classes = []`.
5. `calculate_block_commitments` computes `state_diff_commitment = Poseidon("STARKNET_STATE_DIFF0", ..., 0 /* deprecated count */, ...)` — missing the actual class hash.
6. The correct commitment (as computed by a verifier or sync node using `ThinStateDiff::from_state_diff`) would chain `1, <class_hash>` for the deprecated declared class, producing a different Poseidon output.
7. The block hash stored by the sequencer diverges from the hash any independent verifier would compute, breaking consensus and proof verification for that block.

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

**File:** crates/apollo_batcher/src/block_builder.rs (L203-210)
```rust
    pub fn commitment(&self) -> ProposalCommitment {
        ProposalCommitment {
            partial_block_hash: PartialBlockHash::from_partial_block_hash_components(
                &self.partial_block_hash_components,
            )
            .expect("Unable to calculate the proposal commitment"),
        }
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
