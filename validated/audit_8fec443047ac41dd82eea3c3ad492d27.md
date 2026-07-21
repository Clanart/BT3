### Title
`CommitmentStateDiff`→`ThinStateDiff` Conversion Unconditionally Drops `deprecated_declared_classes`, Producing Wrong `state_diff_commitment` and Block Hash for Blocks Containing Cairo 0 Class Declarations - (File: `crates/blockifier/src/state/cached_state.rs`)

---

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` implementation in `crates/blockifier/src/state/cached_state.rs` hardcodes `deprecated_declared_classes: Vec::new()`. When a block contains a deprecated (Cairo 0) class declaration, the `ThinStateDiff` produced from execution is missing those entries. This truncated diff is fed directly into `calculate_block_commitments` → `calculate_state_diff_hash`, producing a `state_diff_commitment` that does not reflect the actual state change. The same truncated diff is stored on-chain and broadcast over P2P. The resulting block hash is structurally wrong, and any downstream consumer that independently recomputes the commitment — including the SNOS prover and L1 verifier — will observe a mismatch.

---

### Finding Description

**Root cause — the silent drop:**

`CommitmentStateDiff` has no `deprecated_declared_classes` field:

```rust
pub struct CommitmentStateDiff {
    pub address_to_class_hash: IndexMap<ContractAddress, ClassHash>,
    pub address_to_nonce: IndexMap<ContractAddress, Nonce>,
    pub storage_updates: IndexMap<ContractAddress, IndexMap<StorageKey, Felt>>,
    pub class_hash_to_compiled_class_hash: IndexMap<ClassHash, CompiledClassHash>,
}
``` [1](#0-0) 

The conversion to `ThinStateDiff` therefore always produces an empty list, with a TODO acknowledging the gap:

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
``` [2](#0-1) 

**Propagation into the commitment pipeline:**

`BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff.clone())` and passes the result directly to `calculate_block_commitments`:

```rust
let (header_commitments, measurements) = calculate_block_commitments(
    &transactions_data,
    ThinStateDiff::from(commitment_state_diff.clone()),   // deprecated_declared_classes = []
    l1_da_mode,
    &block_info.starknet_version,
)
.await;
``` [3](#0-2) 

`calculate_block_commitments` calls `calculate_state_diff_hash`, which chains the deprecated declared classes count and hashes into the Poseidon commitment:

```rust
hash_chain =
    chain_deprecated_declared_classes(&state_diff.deprecated_declared_classes, hash_chain);
``` [4](#0-3) 

Because `deprecated_declared_classes` is always `[]`, the hash chains `0` for the count and no class hashes, regardless of how many Cairo 0 classes were actually declared in the block.

**`state_diff_length` is also wrong:**

`ThinStateDiff::len()` counts `deprecated_declared_classes.len()`:

```rust
result += self.deprecated_declared_classes.len();
``` [5](#0-4) 

This length feeds `concat_counts` inside `calculate_block_commitments`, producing a wrong `concatenated_counts` field in `BlockHeaderCommitments`. The same length is stored as `state_diff_length` in the block header and is used by the P2P sync client as the termination condition for collecting state diff chunks:

```rust
let target_state_diff_len = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    ...
    .state_diff_length
    .ok_or(...)?;

while current_state_diff_len < target_state_diff_len {
    ...
    current_state_diff_len += state_diff_chunk.len();
    unite_state_diffs(&mut result, state_diff_chunk)?;
}
``` [6](#0-5) 

A too-small `state_diff_length` causes the sync loop to terminate before all chunks are received, silently storing an incomplete state diff.

**The same truncated diff is committed to storage:**

`BlockExecutionArtifacts::thin_state_diff()` also calls `ThinStateDiff::from(self.commitment_state_diff.clone())`, so the diff written to storage via `commit_proposal` → `append_state_diff` is identically truncated:

```rust
pub fn thin_state_diff(&self) -> ThinStateDiff {
    ThinStateDiff::from(self.commitment_state_diff.clone())
}
``` [7](#0-6) 

The commitment and the stored diff are therefore self-consistently wrong: the P2P sync length check passes, but the actual on-chain state is missing the deprecated class declarations.

**Block hash is wrong:**

`PartialBlockHashComponents` is built from the wrong `header_commitments` (containing the wrong `state_diff_commitment`):

```rust
let partial_block_hash_components =
    PartialBlockHashComponents::new(&block_info, header_commitments);
``` [8](#0-7) 

This flows into `ProposalCommitment` and ultimately into `calculate_block_hash`, so the finalized block hash does not commit to the deprecated class declarations.

---

### Impact Explanation

**Critical — Wrong block hash / commitment for accepted input.**

Any block that includes a `DeclareV0` or `DeclareV1` transaction will have its `state_diff_commitment` computed without the declared class hashes. The finalized block hash therefore does not accurately represent the state transition. Downstream consumers that independently verify the commitment — the SNOS prover, the L1 verifier, or any external block explorer — will compute a different hash and reject the block or its proof. Additionally, syncing nodes receive a `state_diff_length` that is too small, causing them to store an incomplete state diff and diverge from the canonical state.

---

### Likelihood Explanation

`DeclareV0` and `DeclareV1` transactions are deprecated but remain valid in the current Starknet protocol. Any unprivileged user can submit such a transaction. The gateway does not unconditionally reject them. The reexecution utility explicitly notes the gap ("Blocks before v0.14.0 may include deprecated (Cairo 0) declared classes which are not represented in `CommitmentStateDiff`") and skips hash comparison for those blocks, confirming the bug is known to affect real blocks. The TODO comment in the conversion confirms the issue is unresolved in production code. [9](#0-8) 

---

### Recommendation

1. Add `deprecated_declared_classes` to `CommitmentStateDiff` (or populate it from `StateMaps.declared_contracts` during `finalize_block`).
2. Update `From<CommitmentStateDiff> for ThinStateDiff` to transfer the field instead of hardcoding `Vec::new()`.
3. Remove the TODO comment once the fix is in place.
4. Add a regression test that declares a Cairo 0 class, computes the `state_diff_commitment`, and asserts it matches the independently computed hash including the deprecated class.

---

### Proof of Concept

1. Submit a `DeclareTransactionV1` (Cairo 0 class) to the sequencer gateway.
2. The blockifier executes it; `StateMaps.declared_contracts` records `{class_hash: true}`.
3. `finalize_block` converts `StateMaps` → `CommitmentStateDiff`; `declared_contracts` is dropped (no field in `CommitmentStateDiff`).
4. `BlockExecutionArtifacts::new` calls `ThinStateDiff::from(commitment_state_diff.clone())`; `deprecated_declared_classes` is `[]`.
5. `calculate_block_commitments` calls `calculate_state_diff_hash` with `deprecated_declared_classes = []`; the Poseidon hash chains `0` for the count.
6. `PartialBlockHashComponents` is built with this wrong `state_diff_commitment`.
7. Consensus finalizes the block; the stored block hash does not commit to the declared class.
8. An independent verifier (e.g., SNOS) recomputes `calculate_state_diff_hash` with the actual state diff (which includes the deprecated class) and obtains a different hash, causing proof verification to fail.

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

**File:** crates/blockifier/src/state/cached_state.rs (L756-768)
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

**File:** crates/apollo_batcher/src/block_builder.rs (L170-176)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
```

**File:** crates/apollo_batcher/src/block_builder.rs (L178-179)
```rust
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
```

**File:** crates/apollo_batcher/src/block_builder.rs (L210-213)
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

**File:** crates/starknet_api/src/state.rs (L115-115)
```rust
        result += self.deprecated_declared_classes.len();
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-97)
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
```

**File:** crates/blockifier_reexecution/src/utils.rs (L206-208)
```rust
///
/// Note: Blocks before v0.14.0 may include deprecated (Cairo 0) declared classes which are not
/// represented in [`CommitmentStateDiff`]; those blocks skip hash comparison below.
```
