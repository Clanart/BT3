### Title
`From<CommitmentStateDiff> for ThinStateDiff` hardcodes `deprecated_declared_classes: Vec::new()`, causing wrong `state_diff_length` in block hash `concatenated_counts` and wrong state diff commitment - (File: `crates/blockifier/src/state/cached_state.rs`)

### Summary

The `From<CommitmentStateDiff> for ThinStateDiff` conversion always sets `deprecated_declared_classes` to an empty vector. When a block contains deprecated (Cairo 0) class declarations, `ThinStateDiff::len()` under-counts the state diff length by exactly N (the number of deprecated declared classes). This wrong length is encoded into `concat_counts` → `concatenated_counts` inside `BlockHeaderCommitments`, which is directly chained into the block hash. The state diff commitment is also wrong because `calculate_state_diff_hash` is called with the same incomplete `ThinStateDiff`.

### Finding Description

In `crates/blockifier/src/state/cached_state.rs`, the `From<CommitmentStateDiff> for ThinStateDiff` implementation hardcodes `deprecated_declared_classes: Vec::new()`: [1](#0-0) 

`CommitmentStateDiff` has no `deprecated_declared_classes` field at all — it only tracks `address_to_class_hash`, `address_to_nonce`, `storage_updates`, and `class_hash_to_compiled_class_hash`. Cairo 0 (deprecated) declared classes have no compiled class hash, so they are invisible to `CommitmentStateDiff` and are silently dropped.

In `BlockExecutionArtifacts::new()`, this conversion is the sole source of the `ThinStateDiff` fed to block commitment calculations: [2](#0-1) 

`calculate_block_commitments` uses `state_diff.len()` to build `concat_counts`: [3](#0-2) 

`ThinStateDiff::len()` explicitly counts `deprecated_declared_classes.len()`, which is always 0 due to the hardcoded `Vec::new()`: [4](#0-3) 

The resulting `concatenated_counts` is then chained directly into the block hash: [5](#0-4) 

`concat_counts` encodes `state_diff_length` as a 64-bit field in a packed Felt: [6](#0-5) 

Additionally, `calculate_state_diff_hash` is called with the same `ThinStateDiff` (empty `deprecated_declared_classes`), producing a wrong `state_diff_commitment` that is also chained into the block hash. [7](#0-6) 

The `state_diff_length` stored in the block header (used by the P2P sync client as the authoritative target length for reassembling state diff chunks) is also derived from this same wrong `ThinStateDiff::len()`: [8](#0-7) [9](#0-8) 

### Impact Explanation

For any block containing N deprecated (Cairo 0) class declarations:

1. `state_diff.len()` is under-counted by N.
2. `concat_counts` encodes the wrong `state_diff_length` (off by N).
3. `concatenated_counts` in `BlockHeaderCommitments` is wrong.
4. The block hash is wrong — it chains the wrong `concatenated_counts` and the wrong `state_diff_commitment`.
5. The `state_diff_length` stored in the block header is wrong, causing P2P sync clients to stop collecting state diff chunks too early, resulting in an incomplete (truncated) state diff being accepted as valid.

This matches **Critical** impact: *Wrong state, receipt, event, L1 message, class hash, storage value, or revert result from blockifier/syscall/execution logic for accepted input*, and **High** impact: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value*.

### Likelihood Explanation

Deprecated (Cairo 0) class declarations are still part of the Starknet protocol (deprecated but not removed). Any unprivileged user can submit a `DECLARE` v0/v1 transaction. If the gateway admits it and the sequencer includes it in a block, the block hash and state diff commitment are silently wrong. The TODO comment `// TODO(AlonH): Remove this when the structure of storage diffs changes.` confirms this is a known structural gap, not an intentional design choice.

### Recommendation

Add a `deprecated_declared_classes` field to `CommitmentStateDiff` (and correspondingly to `StateMaps`), populate it during blockifier execution of deprecated `DECLARE` transactions, and remove the hardcoded `Vec::new()` in `From<CommitmentStateDiff> for ThinStateDiff`.

### Proof of Concept

```
1. User submits a deprecated (Cairo 0) DECLARE transaction.
2. Sequencer includes it in block B.
3. BlockExecutionArtifacts::new() calls:
       ThinStateDiff::from(commitment_state_diff.clone())
   → deprecated_declared_classes = Vec::new()  (always, regardless of actual declarations)
4. calculate_block_commitments receives ThinStateDiff with len() = L (missing 1 deprecated class).
5. concat_counts(n_txs, n_events, L, da_mode) encodes wrong state_diff_length = L instead of L+1.
6. concatenated_counts is wrong → block hash is wrong.
7. calculate_state_diff_hash(thin_state_diff) omits the deprecated class → state_diff_commitment is wrong.
8. P2P sync server serves block B with state_diff_length = L in the header.
9. P2P sync client stops collecting chunks after L items, missing the deprecated class declaration.
   → Stored state diff is incomplete; downstream state root diverges.
```

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

**File:** crates/apollo_batcher/src/block_builder.rs (L159-166)
```rust
        // TODO(Ayelet): Remove the clones.
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L253-261)
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L340-357)
```rust
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

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L374-393)
```rust
pub fn concat_counts(
    transaction_count: usize,
    event_count: usize,
    state_diff_length: usize,
    l1_data_availability_mode: L1DataAvailabilityMode,
) -> Felt {
    let l1_data_availability_byte: u8 = match l1_data_availability_mode {
        L1DataAvailabilityMode::Calldata => 0,
        L1DataAvailabilityMode::Blob => 0b10000000,
    };
    let concat_bytes = [
        to_64_bits(transaction_count).as_slice(),
        to_64_bits(event_count).as_slice(),
        to_64_bits(state_diff_length).as_slice(),
        &[l1_data_availability_byte],
        &[0_u8; 7], // zero padding
    ]
    .concat();
    Felt::from_bytes_be_slice(concat_bytes.as_slice())
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

**File:** crates/apollo_p2p_sync/src/server/mod.rs (L229-238)
```rust
        // TODO(shahak): Remove this once central sync fills the state_diff_length field.
        if header.state_diff_length.is_none() {
            header.state_diff_length = Some(
                txn.get_state_diff(block_number)?
                    .ok_or(P2pSyncServerError::BlockNotFound {
                        block_hash_or_number: BlockHashOrNumber::Number(block_number),
                    })?
                    .len(),
            );
        }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-70)
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
```
