### Title
`CommitmentStateDiff::from(StateMaps)` Silently Drops `deprecated_declared_classes`, Producing Wrong `state_diff_commitment` and `state_diff_length` in Every Block Containing a Deprecated Declare Transaction — (`crates/blockifier/src/state/cached_state.rs`)

### Summary

`CommitmentStateDiff` has no field for deprecated declared classes. When `BlockExecutionArtifacts::new` converts the execution output into a `ThinStateDiff` for `calculate_block_commitments`, the resulting `ThinStateDiff` always carries `deprecated_declared_classes: Vec::new()`. Both the Poseidon `state_diff_commitment` and the `state_diff_length` packed into `concatenated_counts` are therefore computed over an incomplete state diff. The `PartialBlockHash` (the proposal commitment used in consensus) and the final block hash are wrong for any block that includes a deprecated (Cairo 0) Declare transaction.

### Finding Description

**Root cause — `declared_contracts` is silently dropped**

`StateMaps` tracks deprecated declared classes in the `declared_contracts` field:

```rust
pub struct StateMaps {
    pub nonces: HashMap<ContractAddress, Nonce>,
    pub class_hashes: HashMap<ContractAddress, ClassHash>,
    pub storage: HashMap<StorageEntry, Felt>,
    pub compiled_class_hashes: HashMap<ClassHash, CompiledClassHash>,
    pub declared_contracts: HashMap<ClassHash, bool>,   // ← Cairo-0 declarations live here
}
``` [1](#0-0) 

The `From<StateMaps> for CommitmentStateDiff` conversion ignores `declared_contracts` entirely:

```rust
impl From<StateMaps> for CommitmentStateDiff {
    fn from(diff: StateMaps) -> Self {
        Self {
            address_to_class_hash: IndexMap::from_iter(diff.class_hashes),
            storage_updates: StorageDiff::from(StorageView(diff.storage)),
            class_hash_to_compiled_class_hash: IndexMap::from_iter(diff.compiled_class_hashes),
            address_to_nonce: IndexMap::from_iter(diff.nonces),
            // diff.declared_contracts is dropped — no field for it
        }
    }
}
``` [2](#0-1) 

`CommitmentStateDiff` has no `deprecated_declared_classes` field, so the information is permanently lost.

**Propagation — `ThinStateDiff` always has an empty list**

The `From<CommitmentStateDiff> for ThinStateDiff` conversion hard-codes the field to empty, with a TODO acknowledging the gap:

```rust
impl From<CommitmentStateDiff> for ThinStateDiff {
    fn from(commitment_state_diff: CommitmentStateDiff) -> Self {
        Self {
            ...
            // TODO(AlonH): Remove this when the structure of storage diffs changes.
            deprecated_declared_classes: Vec::new(),
        }
    }
}
``` [3](#0-2) 

**Commitment construction — both outputs are wrong**

`BlockExecutionArtifacts::new` feeds this incomplete `ThinStateDiff` directly into `calculate_block_commitments`:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),   // ← deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [4](#0-3) 

Inside `calculate_block_commitments`, two values are derived from the incomplete diff:

1. `state_diff.len()` — used in `concat_counts` to build `concatenated_counts` (the `packed_lengths` field of the block hash). `ThinStateDiff::len()` explicitly counts `deprecated_declared_classes`:

```rust
pub fn len(&self) -> usize {
    ...
    result += self.deprecated_declared_classes.len();   // always 0 — wrong
    ...
}
``` [5](#0-4) 

2. `calculate_state_diff_hash(&state_diff)` — the Poseidon hash chains `deprecated_declared_classes`:

```rust
hash_chain = chain_deprecated_declared_classes(
    &state_diff.deprecated_declared_classes,   // always empty — wrong
    hash_chain
);
``` [6](#0-5) 

Both `concatenated_counts` and `state_diff_commitment` are therefore wrong, and they feed directly into `calculate_block_hash`:

```rust
.chain(&block_commitments.concatenated_counts)       // wrong state_diff_length
.chain(&block_commitments.state_diff_commitment.0.0) // wrong hash
``` [7](#0-6) 

**No existing check catches the mismatch**

The committer's optional `verify_state_diff_hash` check re-computes the hash from the same `ThinStateDiff` that was stored by the batcher — which also has empty `deprecated_declared_classes`. Both sides of the comparison are wrong in the same way, so the check passes silently. [8](#0-7) 

**Contrast with the `native_blockifier` (Python) path**

The Python-binding path explicitly reconstructs `deprecated_declared_classes` before writing the state diff:

```rust
let mut state_diff = StateDiff::try_from(py_state_diff)?;
state_diff.deprecated_declared_classes = deprecated_declared_classes;
let (mut thin_state_diff, ...) = ThinStateDiff::from_state_diff(state_diff);
``` [9](#0-8) 

The new Rust sequencer path has no equivalent step.

### Impact Explanation

For every block that contains at least one deprecated (Cairo 0) Declare transaction:

- `state_diff_length` in `concatenated_counts` is under-counted by the number of deprecated declared classes.
- `state_diff_commitment` is a Poseidon hash over an incomplete set of state changes.
- The `PartialBlockHash` (the `ProposalCommitment` exchanged during consensus) is wrong.
- The final `BlockHash` written to storage and broadcast to L1 is wrong.

A prover that correctly includes deprecated declared classes in the state diff hash will produce a proof that does not match the sequencer's block hash, causing proof verification to fail on L1. Nodes syncing via P2P use `state_diff_length` from the block header as the termination condition for collecting state diff chunks; the under-counted length causes them to stop early and miss the deprecated declared classes, producing a divergent local state.

### Likelihood Explanation

Deprecated Declare transactions (Cairo 0 / `DeclareV1`) are still accepted by the blockifier and have active test coverage. Any user who submits such a transaction triggers the bug. The sequencer has no admission-level guard that would reject deprecated Declare transactions before they reach block finalization.

### Recommendation

Add `deprecated_declared_classes` to `CommitmentStateDiff` and populate it from `StateMaps.declared_contracts` (entries where the value is `true` and no corresponding `compiled_class_hashes` entry exists, i.e., Cairo-0 classes). Propagate the field through `From<CommitmentStateDiff> for ThinStateDiff` instead of hard-coding `Vec::new()`. Remove the TODO comment once the fix is in place.

### Proof of Concept

```
1. Submit a DeprecatedDeclare (v1) transaction for a Cairo-0 class.
2. The blockifier executes it; StateMaps.declared_contracts = {class_hash: true}.
3. CommitmentStateDiff::from(state_maps) drops declared_contracts.
4. BlockExecutionArtifacts::new calls:
       ThinStateDiff::from(commitment_state_diff)
   → deprecated_declared_classes = []
5. calculate_block_commitments receives the incomplete ThinStateDiff:
   a. concat_counts(txs, events, state_diff.len(), da_mode)
      state_diff.len() is N instead of N+1  → concatenated_counts is wrong
   b. calculate_state_diff_hash(state_diff)
      chain_deprecated_declared_classes([]) → state_diff_commitment is wrong
6. PartialBlockHash::from_partial_block_hash_components uses both wrong values.
7. ProposalCommitment (consensus) and final BlockHash are wrong.
8. A prover computing the correct state_diff_hash over the full ThinStateDiff
   (including the deprecated declared class) produces a hash that does not match
   the sequencer's stored block hash → proof verification fails on L1.
```

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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L260-262)
```rust
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
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

**File:** crates/native_blockifier/src/storage.rs (L216-228)
```rust
        // Construct state diff; manually add declared classes.
        let mut state_diff = StateDiff::try_from(py_state_diff)?;
        state_diff.deprecated_declared_classes = deprecated_declared_classes;
        state_diff.declared_classes = declared_classes;

        let (mut thin_state_diff, declared_classes, deprecated_declared_classes) =
            ThinStateDiff::from_state_diff(state_diff);
        // Add the migrated class hash to the state diff.
        for (class_hash, compiled_class_hash) in migrated_class_hash_to_compiled_class_hash {
            thin_state_diff
                .class_hash_to_compiled_class_hash
                .insert(class_hash, compiled_class_hash);
        }
```
