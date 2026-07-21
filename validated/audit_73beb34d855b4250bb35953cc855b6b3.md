### Title
`BlockExecutionArtifacts::thin_state_diff()` Omits `deprecated_declared_classes`, Producing Wrong `state_diff_commitment` and `state_diff_length` in Block Hash — (`crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion unconditionally hardcodes `deprecated_declared_classes: Vec::new()`. This incomplete `ThinStateDiff` is the sole input to `calculate_block_commitments` inside `BlockExecutionArtifacts::new()`, and is also the value returned by `BlockExecutionArtifacts::thin_state_diff()` — which is stored in storage and forwarded to the committer. For any block that contains a deprecated (Cairo 0) class declaration, the `state_diff_commitment`, `state_diff_length` (embedded in `concatenated_counts`), and the Patricia-trie update are all wrong, producing an incorrect block hash and state root.

---

### Finding Description

**Root cause — hardcoded empty `deprecated_declared_classes`**

`CommitmentStateDiff` has no field for deprecated class declarations:

```rust
pub struct CommitmentStateDiff {
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
``` [1](#0-0) 

When this is converted to `ThinStateDiff`, the field is silently zeroed:

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

**Propagation path 1 — wrong `state_diff_commitment` and `concatenated_counts` in block hash**

`BlockExecutionArtifacts::new()` feeds this incomplete `ThinStateDiff` directly into `calculate_block_commitments`:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),   // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [3](#0-2) 

Inside `calculate_block_commitments`, two block-hash components are derived from the incomplete diff:

1. `state_diff_commitment` — computed by `calculate_state_diff_hash`, which chains `deprecated_declared_classes` into the Poseidon hash:

```rust
hash_chain = chain_deprecated_declared_classes(
    &state_diff.deprecated_declared_classes, hash_chain);
``` [4](#0-3) 

2. `concatenated_counts` — computed by `concat_counts` using `state_diff.len()`, which counts `deprecated_declared_classes.len()`:

```rust
result += self.deprecated_declared_classes.len();
``` [5](#0-4) 

Both are chained directly into the final block hash:

```rust
.chain(&block_commitments.concatenated_counts)
.chain(&block_commitments.state_diff_commitment.0.0)
``` [6](#0-5) 

**Propagation path 2 — wrong state diff stored in storage and sent to committer**

`BlockExecutionArtifacts::thin_state_diff()` returns the same incomplete diff:

```rust
pub fn thin_state_diff(&self) -> ThinStateDiff {
    ThinStateDiff::from(self.commitment_state_diff.clone())
}
``` [7](#0-6) 

In `decision_reached`, this diff is both committed to storage and forwarded to the committer:

```rust
let state_diff = block_execution_artifacts.thin_state_diff();
...
self.commit_proposal_and_block(height, state_diff.clone(), ...)
...
self.write_commitment_results_and_add_new_task(height, state_diff.clone(), ...)
``` [8](#0-7) 

The committer uses this diff to update the Patricia trie (class trie), so the global root (state root) will also be wrong for any block containing deprecated class declarations.

**Contrast with RPC execution path**

The RPC simulation path (`induced_state_diff`) correctly handles deprecated class hashes by accepting them as an explicit parameter:

```rust
deprecated_declared_classes: deprecated_declared_class_hash
    .map_or_else(Vec::new, |class_hash| vec![class_hash]),
``` [9](#0-8) 

This asymmetry means `starknet_simulateTransactions` would return a state diff that includes the deprecated class, while the actual committed block would not — a direct inconsistency between the RPC view and the canonical chain state.

**P2P sync breakage**

The P2P sync client uses `state_diff_length` from the stored block header as the termination condition for assembling state diff chunks:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    ...
    .state_diff_length ...;

while current_state_diff_len < target_state_diff_len { ... }
``` [10](#0-9) 

Because `state_diff_length` is computed from the incomplete `ThinStateDiff`, syncing nodes will never request the deprecated-class chunks, permanently missing those declarations from their local state.

---

### Impact Explanation

For any block containing a deprecated (Cairo 0) class declaration:

- **Wrong `state_diff_commitment`** — the Poseidon hash over the state diff omits the deprecated class entries, producing a different felt value than the correct one.
- **Wrong `concatenated_counts`** — `state_diff_length` is under-counted, corrupting the packed field that encodes tx count, event count, and state diff length in the block hash.
- **Wrong block hash** — both corrupted felts are chained into the final Poseidon block hash.
- **Wrong global root** — the class trie is not updated with the deprecated class, so the Patricia-trie root returned by the committer is wrong, further corrupting the block hash.
- **Wrong authoritative RPC value** — `starknet_getStateUpdate` returns a state diff missing the deprecated class.
- **Broken P2P sync** — syncing nodes permanently miss the deprecated class declaration.

This matches the impact category: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input* and *Wrong compiled class, CASM/native artifact, class hash, or contract code selected for execution*.

---

### Likelihood Explanation

The trigger is any block that includes a deprecated Declare transaction (Declare v1 for a Cairo 0 class). Such transactions are unprivileged — any user can submit them. The fact that `induced_state_diff` explicitly accepts a `deprecated_declared_class_hash` parameter, and that `ThinStateDiff` has a `deprecated_declared_classes` field used throughout storage, sync, and hash logic, confirms the protocol expects these to occur. The TODO comment (`// TODO(AlonH): Remove this when the structure of storage diffs changes`) acknowledges the gap but does not close it. No gateway-level check was found that would unconditionally reject Declare v1 transactions.

---

### Recommendation

Add a `deprecated_declared_classes: Vec<ClassHash>` field to `CommitmentStateDiff` (or to `BlockExecutionSummary`). Populate it from the blockifier's state tracking when a deprecated Declare transaction is executed. Update `From<CommitmentStateDiff> for ThinStateDiff` to use this field instead of `Vec::new()`, and remove the TODO comment once the field is populated correctly.

---

### Proof of Concept

1. A user submits a Declare v1 transaction declaring a Cairo 0 class `C` with class hash `H`.
2. The sequencer includes it in block `B`.
3. `finalize_block` produces `BlockExecutionSummary { state_diff: CommitmentStateDiff { ... } }` — `H` is absent from all fields of `CommitmentStateDiff`.
4. `BlockExecutionArtifacts::new()` calls `ThinStateDiff::from(commitment_state_diff.clone())`, yielding `deprecated_declared_classes: []`.
5. `calculate_state_diff_hash` computes `state_diff_commitment` without `H` → wrong felt.
6. `ThinStateDiff::len()` returns `N` instead of `N+1` → `concat_counts` encodes wrong `state_diff_length` → wrong `concatenated_counts` felt.
7. `calculate_block_hash` chains both wrong felts → block hash `BH_wrong ≠ BH_correct`.
8. The committer updates the Patricia trie without `H` → global root `GR_wrong ≠ GR_correct`.
9. `starknet_getStateUpdate(B)` returns a state diff without `H`.
10. P2P-syncing nodes, using `state_diff_length = N` from the header, never request the chunk for `H` and permanently miss the class declaration.

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

**File:** crates/apollo_batcher/src/block_builder.rs (L198-201)
```rust
    pub fn thin_state_diff(&self) -> ThinStateDiff {
        // TODO(Ayelet): Remove the clones.
        ThinStateDiff::from(self.commitment_state_diff.clone())
    }
```

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L35-36)
```rust
    hash_chain =
        chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
```

**File:** crates/starknet_api/src/state.rs (L114-115)
```rust
        result += self.deprecated_declared_classes.len();
        result += self.nonces.len();
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L260-261)
```rust
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
```

**File:** crates/apollo_batcher/src/batcher.rs (L767-801)
```rust
        let state_diff = block_execution_artifacts.thin_state_diff();
        let n_txs = u64::try_from(block_execution_artifacts.tx_hashes().len())
            .expect("Number of transactions should fit in u64");
        let n_rejected_txs =
            u64::try_from(block_execution_artifacts.execution_data.rejected_tx_hashes.len())
                .expect("Number of rejected transactions should fit in u64");
        let n_reverted_count = u64::try_from(
            block_execution_artifacts
                .execution_data
                .execution_infos_and_signatures
                .values()
                .filter(|(info, _)| info.revert_error.is_some())
                .count(),
        )
        .expect("Number of reverted transactions should fit in u64");
        let partial_block_hash_components =
            block_execution_artifacts.partial_block_hash_components();
        let state_diff_commitment =
            partial_block_hash_components.header_commitments.state_diff_commitment;
        let parent_proposal_commitment = self.get_parent_proposal_commitment(height)?;
        self.commit_proposal_and_block(
            height,
            state_diff.clone(),
            block_execution_artifacts.address_to_nonce(),
            block_execution_artifacts.execution_data.consumed_l1_handler_tx_hashes,
            block_execution_artifacts.execution_data.rejected_tx_hashes,
            StorageCommitmentBlockHash::Partial(partial_block_hash_components),
        )
        .await?;

        self.write_commitment_results_and_add_new_task(
            height,
            state_diff.clone(), // TODO(Nimrod): Remove the clone here.
            Some(state_diff_commitment),
        )
```

**File:** crates/apollo_rpc_execution/src/execution_utils.rs (L141-143)
```rust
        deprecated_declared_classes: deprecated_declared_class_hash
            .map_or_else(Vec::new, |class_hash| vec![class_hash]),
        nonces: blockifier_state_diff.address_to_nonce,
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-72)
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
```
