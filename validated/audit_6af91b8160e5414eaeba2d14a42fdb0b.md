### Title
`deprecated_declared_classes` Permanently Zeroed in Sequencer Block Production Path Corrupts `state_diff_commitment` and Block Hash — (`crates/blockifier/src/state/cached_state.rs`)

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion, which is the sole path used by the sequencer's batcher to produce the `ThinStateDiff` fed into `calculate_block_commitments`, unconditionally sets `deprecated_declared_classes: Vec::new()`. Any block containing a deprecated (Cairo 0) Declare transaction will therefore have a wrong `state_diff_commitment`, a wrong `concatenated_counts` (state-diff-length field), and consequently a wrong block hash. The bug is structurally identical to the seed report: a required allocation is silently omitted, causing the commitment to be computed over an incomplete dataset.

### Finding Description

**Root cause — the always-empty field:**

```rust
// crates/blockifier/src/state/cached_state.rs
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
```

`CommitmentStateDiff` is produced from `StateMaps`, which has no field for deprecated declared classes. The blockifier executes deprecated Declare transactions and stores the class in state, but never writes the class hash into `StateMaps`. Consequently `CommitmentStateDiff` never carries deprecated declared classes, and the conversion above hard-codes the field to an empty vector.

**Propagation into the block commitment pipeline:**

`BlockExecutionArtifacts::thin_state_diff()` calls this conversion:

```rust
// crates/apollo_batcher/src/block_builder.rs
pub fn thin_state_diff(&self) -> ThinStateDiff {
    ThinStateDiff::from(self.commitment_state_diff.clone())
}
```

That `ThinStateDiff` is passed directly to `calculate_block_commitments`:

```rust
// crates/apollo_batcher/src/block_builder.rs  (BlockExecutionArtifacts::new)
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),  // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
```

Inside `calculate_block_commitments`, two values are derived from the (incomplete) `ThinStateDiff`:

1. **`state_diff_commitment`** — via `calculate_state_diff_hash`, which chains `deprecated_declared_classes` into the Poseidon hash:

```rust
// crates/starknet_api/src/block_hash/state_diff_hash.rs
hash_chain = chain_deprecated_declared_classes(
    &state_diff.deprecated_declared_classes,   // always []
    hash_chain
);
```

2. **`concatenated_counts`** — via `concat_counts(…, state_diff.len(), …)`, where `ThinStateDiff::len()` adds `deprecated_declared_classes.len()` (always 0):

```rust
// crates/starknet_api/src/state.rs
result += self.deprecated_declared_classes.len();   // always 0
```

Both corrupted values are then chained into the final block hash:

```rust
// crates/starknet_api/src/block_hash/block_hash_calculator.rs
.chain(&block_commitments.concatenated_counts)          // wrong
.chain(&block_commitments.state_diff_commitment.0.0)    // wrong
```

**The committer does not catch this.** When `commit_block_inner` re-derives the state diff hash, it uses the same incomplete `ThinStateDiff` (also produced via the same conversion), so the re-derived hash matches the already-wrong commitment and no mismatch error is raised.

**The stored state diff is also incomplete.** The same `ThinStateDiff` is written to MDBX storage via `append_state_diff`, so `starknet_getStateUpdate` RPC responses and P2P state-diff sync messages will omit deprecated declared classes for sequencer-produced blocks.

### Impact Explanation

For any block that contains one or more deprecated (Cairo 0) Declare transactions:

- `state_diff_commitment` in `BlockHeaderCommitments` is wrong (missing the deprecated class hashes from the Poseidon chain).
- `concatenated_counts` is wrong (state-diff length is under-counted).
- The final block hash is wrong (both corrupted fields are chained into it).
- The `ThinStateDiff` persisted in storage is incomplete, causing wrong authoritative values for `starknet_getStateUpdate` and P2P sync.
- Any external verifier (OS/prover, full node) that computes the state diff hash from the actual declared classes will disagree with the sequencer's commitment, breaking proof verification and cross-node agreement.

This matches the **High** impact scope: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value* and *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input*.

### Likelihood Explanation

Deprecated Declare transactions (Cairo 0) remain valid in the current Starknet protocol and are accepted by the gateway. Any unprivileged user can submit one. The bug is triggered on every block that contains such a transaction. No special privileges or race conditions are required.

### Recommendation

`CommitmentStateDiff` (and `StateMaps`) must be extended to track deprecated declared class hashes. When a deprecated Declare transaction is executed, the class hash should be appended to a new `deprecated_declared_classes: Vec<ClassHash>` field in `StateMaps` / `CommitmentStateDiff`. The `From<CommitmentStateDiff> for ThinStateDiff` conversion should then propagate this field instead of hard-coding `Vec::new()`.

Until the data model is extended, the batcher must obtain the deprecated declared class hashes from a separate source (e.g., the blockifier's class cache) and inject them into the `ThinStateDiff` before calling `calculate_block_commitments`.

### Proof of Concept

1. Submit a deprecated Declare transaction (Cairo 0 class) to the sequencer.
2. After the block is produced, call `starknet_getStateUpdate` for that block. Observe that `deprecated_declared_classes` is empty.
3. Independently compute `calculate_state_diff_hash` over the state diff with the declared class hash included. The result will differ from the `state_diff_commitment` stored in the block header.
4. Verify that `calculate_block_hash` over the stored `PartialBlockHashComponents` (which embeds the wrong `state_diff_commitment` and `concatenated_counts`) produces a different hash than one computed with the correct state diff commitment.

**Key code locations:**

- Always-empty field: [1](#0-0) 
- Conversion used in batcher: [2](#0-1) 
- `thin_state_diff()` method: [3](#0-2) 
- `deprecated_declared_classes` chained into state diff hash: [4](#0-3) 
- `deprecated_declared_classes.len()` counted in state diff length: [5](#0-4) 
- Both corrupted values chained into block hash: [6](#0-5)

### Citations

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

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L35-41)
```rust
    hash_chain =
        chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
    hash_chain = hash_chain.chain(&Felt::ONE) // placeholder.
        .chain(&Felt::ZERO); // placeholder.
    hash_chain = chain_storage_diffs(&state_diff.storage_diffs, hash_chain);
    hash_chain = chain_nonces(&state_diff.nonces, hash_chain);
    StateDiffCommitment(PoseidonHash(hash_chain.get_poseidon_hash()))
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L260-262)
```rust
            .chain(&block_commitments.concatenated_counts)
            .chain(&block_commitments.state_diff_commitment.0.0)
            .chain(&block_commitments.transaction_commitment.0)
```
