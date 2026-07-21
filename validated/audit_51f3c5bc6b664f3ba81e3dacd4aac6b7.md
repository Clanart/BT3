### Title
Wrong `state_diff_commitment` and Block Hash Due to Silently Dropped `deprecated_declared_classes` in `ThinStateDiff::from(CommitmentStateDiff)` — (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally sets `deprecated_declared_classes: Vec::new()`. When the proposer builds a block that includes a `DeclareV1` (Cairo 0) transaction, the `state_diff_commitment` and `concatenated_counts` (which encodes `state_diff_length`) computed inside `BlockExecutionArtifacts::new()` are both wrong. These corrupted values propagate into `PartialBlockHashComponents` and ultimately into `calculate_block_hash`, producing an incorrect block hash that is committed to storage and broadcast to the network.

---

### Finding Description

**Root cause — `From<CommitmentStateDiff> for ThinStateDiff`**

`CommitmentStateDiff` has no `deprecated_declared_classes` field:

```rust
pub struct CommitmentStateDiff {
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
``` [1](#0-0) 

The `From` impl therefore hard-codes an empty vector, with a TODO acknowledging the problem:

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
``` [2](#0-1) 

**Propagation path — `BlockExecutionArtifacts::new()`**

The proposer calls this conversion and immediately feeds the result into `calculate_block_commitments`:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
).await;
``` [3](#0-2) 

Inside `calculate_block_commitments`, two values are computed from the truncated `ThinStateDiff`:

1. **`state_diff_commitment`** — via `calculate_state_diff_hash(&state_diff)`. The hash chains over `deprecated_declared_classes`, so omitting them produces a wrong Poseidon hash.
2. **`concatenated_counts`** — via `concat_counts(..., state_diff.len(), ...)`. `ThinStateDiff::len()` adds `self.deprecated_declared_classes.len()`, which is 0 instead of the actual count, so the packed `state_diff_length` field is too small. [4](#0-3) 

`ThinStateDiff::len()` explicitly counts deprecated declared classes:

```rust
pub fn len(&self) -> usize {
    ...
    result += self.deprecated_declared_classes.len();
    ...
}
``` [5](#0-4) 

`calculate_state_diff_hash` chains them into the Poseidon hash:

```rust
hash_chain = chain_deprecated_declared_classes(
    &state_diff.deprecated_declared_classes, hash_chain);
``` [6](#0-5) 

**Corrupted `PartialBlockHashComponents` and block hash**

The wrong `header_commitments` (containing the wrong `state_diff_commitment` and `concatenated_counts`) are stored in `PartialBlockHashComponents`:

```rust
let partial_block_hash_components =
    PartialBlockHashComponents::new(&block_info, header_commitments);
``` [7](#0-6) 

`calculate_block_hash` chains both corrupted fields directly into the Poseidon hash:

```rust
.chain(&block_commitments.concatenated_counts)
.chain(&block_commitments.state_diff_commitment.0.0)
``` [8](#0-7) 

**Why the committer's internal check does not catch it**

The `state_diff_commitment` extracted for the committer comes from the same corrupted `partial_block_hash_components`:

```rust
let state_diff_commitment =
    partial_block_hash_components.header_commitments.state_diff_commitment;
``` [9](#0-8) 

The `ThinStateDiff` sent to the committer is also derived from the same `CommitmentStateDiff` via `thin_state_diff()`:

```rust
pub fn thin_state_diff(&self) -> ThinStateDiff {
    ThinStateDiff::from(self.commitment_state_diff.clone())
}
``` [10](#0-9) 

Both sides of the committer's hash verification are identically wrong, so `StateDiffHashMismatch` is never raised. The corrupted commitment silently passes through. [11](#0-10) 

---

### Impact Explanation

For any block that contains at least one `DeclareV1` (Cairo 0) transaction:

- `state_diff_commitment` in the stored `BlockHeader` is wrong (Poseidon hash computed over an incomplete state diff).
- `concatenated_counts` is wrong (state_diff_length is under-counted).
- The final block hash produced by `calculate_block_hash` is wrong.
- The wrong block hash is committed to storage and broadcast as the authoritative block hash.

This matches **Critical — Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input**, and **High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value** (any RPC call returning the block hash or state diff commitment for such a block returns a wrong value).

---

### Likelihood Explanation

`DeclareV1` transactions are valid Starknet transactions. Any sequencer that processes a block containing a Cairo 0 class declaration will silently produce a wrong block hash. No special privilege is required; any user can submit a `DeclareV1` transaction. The bug is deterministic and reproducible.

---

### Recommendation

Add `deprecated_declared_classes` to `CommitmentStateDiff` and populate it in the blockifier's state-diff construction so that `ThinStateDiff::from(CommitmentStateDiff)` can copy the real value instead of hard-coding `Vec::new()`. Remove the TODO comment once the field is properly tracked.

---

### Proof of Concept

1. Submit a `DeclareV1` transaction declaring any Cairo 0 contract class to the sequencer.
2. The sequencer includes it in a block. `BlockExecutionArtifacts::new()` is called; `ThinStateDiff::from(commitment_state_diff)` produces a `ThinStateDiff` with `deprecated_declared_classes = []`.
3. `calculate_block_commitments` computes `state_diff_commitment = H(... | 0 deprecated classes | ...)` and `state_diff_length` that is 1 less than the true length.
4. `calculate_block_hash` chains these wrong values → wrong block hash `H_wrong`.
5. `H_wrong` is stored in the block header and returned by `starknet_getBlockWithTxHashes` / `starknet_getBlockHashAndNumber`.
6. Any external verifier (e.g., the Starknet OS, a proof verifier, or a full node syncing via P2P) that recomputes the block hash from the actual state diff (which includes the deprecated class) will compute a different hash `H_correct ≠ H_wrong`, detecting a commitment inconsistency.

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

**File:** crates/apollo_batcher/src/block_builder.rs (L168-169)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
```

**File:** crates/apollo_batcher/src/block_builder.rs (L198-201)
```rust
    pub fn thin_state_diff(&self) -> ThinStateDiff {
        // TODO(Ayelet): Remove the clones.
        ThinStateDiff::from(self.commitment_state_diff.clone())
    }
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L260-261)
```rust
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L318-357)
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

    // Spawn tasks for parallel execution; each measures its own duration.
    let transaction_task = spawn_measured_task(move || {
        calculate_transaction_commitment::<Poseidon>(&transaction_leaf_elements)
    });

    let event_task =
        spawn_measured_task(move || calculate_event_commitment::<Poseidon>(&event_leaf_elements));

    let receipt_task =
        spawn_measured_task(move || calculate_receipt_commitment::<Poseidon>(&receipt_elements));

    let state_diff_task = spawn_measured_task(move || calculate_state_diff_hash(&state_diff));

    // Wait for all tasks to complete.
    let (
        (transaction_commitment, transaction_duration),
        (event_commitment, event_duration),
        (receipt_commitment, receipt_duration),
        (state_diff_commitment, state_diff_duration),
    ) = tokio::try_join!(transaction_task, event_task, receipt_task, state_diff_task)
        .expect("Failed to join block commitments tasks.");

    let commitments = BlockHeaderCommitments {
        transaction_commitment,
        event_commitment,
        receipt_commitment,
        state_diff_commitment,
        concatenated_counts,
    };
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

**File:** crates/apollo_batcher/src/batcher.rs (L784-785)
```rust
        let state_diff_commitment =
            partial_block_hash_components.header_commitments.state_diff_commitment;
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
