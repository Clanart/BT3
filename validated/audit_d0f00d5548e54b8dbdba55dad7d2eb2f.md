### Title
`_compute_state_diff_length` Omits `deprecated_declared_classes`, Producing an Underestimated `state_diff_length` That Corrupts the Block Commitment — (`File: echonet/echo_center.py`)

---

### Summary

`BlobTransformer._compute_state_diff_length` in `echonet/echo_center.py` calculates the state-diff length without counting `deprecated_declared_classes`. The canonical Rust implementation `ThinStateDiff::len()` includes that field. The Python function's result is stored as the block header's `state_diff_length`, which is embedded in `concat_counts` and therefore in the block hash / proposal commitment. Any block that contains deprecated (Cairo 0) class declarations will have a wrong `state_diff_length`, a wrong `concatenated_counts` field, and therefore a wrong block hash commitment.

---

### Finding Description

**Canonical length definition (Rust):**

`ThinStateDiff::len()` in `crates/starknet_api/src/state.rs` counts five categories:

```rust
result += self.deployed_contracts.len();
result += self.class_hash_to_compiled_class_hash.len();
result += self.deprecated_declared_classes.len();   // ← included
result += self.nonces.len();
for (_, storage_diffs) in &self.storage_diffs {
    result += storage_diffs.len();
}
``` [1](#0-0) 

**Buggy Python implementation:**

`_compute_state_diff_length` in `echonet/echo_center.py` sums only four categories and silently drops `deprecated_declared_classes`:

```python
return (
    len(state_diff["address_to_class_hash"])
    + len(state_diff["class_hash_to_compiled_class_hash"])
    + len(state_diff["nonces"]["L1"])
    + sum(len(slots) for slots in storage_updates.values())
    # deprecated_declared_classes is NOT counted
)
``` [2](#0-1) 

Meanwhile, `_build_thin_state_diff` (called for the commitment hash path) hard-codes `"deprecated_declared_classes": []`, so the Rust CLI that computes the state-diff commitment hash also sees an empty list:

```python
return {
    ...
    "deprecated_declared_classes": [],   # ← always empty
    ...
}
``` [3](#0-2) 

This means both the `state_diff_length` integer stored in the header **and** the `StateDiffCommitment` Poseidon hash computed by `calculate_state_diff_hash` are wrong whenever a block contains deprecated class declarations.

**How `state_diff_length` enters the block hash:**

`calculate_block_commitments` calls `concat_counts(transactions_data.len(), event_leaf_elements.len(), state_diff.len(), l1_da_mode)`, which packs the length into a single `Felt` (`concatenated_counts`) that is chained directly into the Poseidon block hash: [4](#0-3) [5](#0-4) 

The `concatenated_counts` field is then chained into `calculate_block_hash`: [6](#0-5) 

`BlockExecutionArtifacts::new` calls `calculate_block_commitments` and stores the result in `partial_block_hash_components`, which is used to derive the `ProposalCommitment` that consensus nodes vote on: [7](#0-6) [8](#0-7) 

The P2P sync client uses `state_diff_length` from the stored block header as the termination condition for reassembling state-diff chunks from peers: [9](#0-8) 

---

### Impact Explanation

**Wrong block hash / proposal commitment (Critical):** When a block contains any deprecated (Cairo 0) class declarations, the `state_diff_length` written to the header is smaller than the true value. This propagates into `concatenated_counts` and therefore into the Poseidon block hash. The echonet node will compute and broadcast a different `PartialBlockHash` / `ProposalCommitment` than a correct node, causing consensus disagreement or acceptance of a commitment that does not match the canonical chain.

**Wrong `StateDiffCommitment` (Critical):** `_build_thin_state_diff` passes `deprecated_declared_classes: []` to the block-hash CLI, so the Poseidon hash over the state diff omits the actual deprecated class hashes. The stored `state_diff_commitment` in the block header is therefore wrong, and the committer's `verify_state_diff_hash` check will fail or be bypassed.

**Wrong state-diff length used by P2P sync (High):** The underestimated `state_diff_length` stored in the header causes `StateDiffStreamBuilder::parse_data_for_block` to terminate early, accepting an incomplete state diff from a peer as if it were complete. This results in a wrong authoritative state diff being written to storage.

---

### Likelihood Explanation

Deprecated (Cairo 0) class declarations are a normal, historical part of Starknet. Any echonet replay of a block that includes such a declaration will trigger the bug. The trigger requires no special privilege — it is a property of the block data being replayed.

---

### Recommendation

1. In `_compute_state_diff_length`, add the count of `deprecated_declared_classes`:

```python
@staticmethod
def _compute_state_diff_length(blob: JsonObject) -> int:
    state_diff = blob["state_diff"]
    storage_updates = state_diff["storage_updates"]["L1"]
    return (
        len(state_diff["address_to_class_hash"])
        + len(state_diff["class_hash_to_compiled_class_hash"])
        + len(state_diff.get("deprecated_declared_classes", []))  # ← add this
        + len(state_diff["nonces"]["L1"])
        + sum(len(slots) for slots in storage_updates.values())
    )
```

2. In `_build_thin_state_diff`, populate `deprecated_declared_classes` from the blob instead of hard-coding `[]`:

```python
"deprecated_declared_classes": state_diff.get("deprecated_declared_classes", []),
```

3. Add a unit test that exercises a blob containing deprecated class declarations and asserts that the computed `state_diff_length` and `state_diff_commitment` match the values produced by the Rust `ThinStateDiff::len()` and `calculate_state_diff_hash` paths.

---

### Proof of Concept

1. Construct a blob whose `state_diff` contains one entry in `deprecated_declared_classes`, e.g. `["0xdeadbeef"]`, and zero entries in all other fields.
2. Call `BlobTransformer._compute_state_diff_length(blob)` → returns `0`.
3. Call `ThinStateDiff { deprecated_declared_classes: vec![class_hash!("0xdeadbeef")], ..Default::default() }.len()` in Rust → returns `1`.
4. The discrepancy of `1` propagates into `concat_counts`, producing a different `concatenated_counts` felt, and therefore a different Poseidon block hash, than the canonical Rust path.
5. The `_build_thin_state_diff` call passes `deprecated_declared_classes: []` to the CLI, so `calculate_state_diff_hash` hashes an empty list instead of `["0xdeadbeef"]`, producing a wrong `StateDiffCommitment` that is stored in the block header.

### Citations

**File:** crates/starknet_api/src/state.rs (L111-122)
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

**File:** echonet/echo_center.py (L493-502)
```python
    @staticmethod
    def _build_thin_state_diff(blob: JsonObject) -> JsonObject:
        state_diff = blob["state_diff"]
        return {
            "deployed_contracts": state_diff["address_to_class_hash"],
            "storage_diffs": state_diff["storage_updates"]["L1"],
            "class_hash_to_compiled_class_hash": state_diff["class_hash_to_compiled_class_hash"],
            "deprecated_declared_classes": [],
            "nonces": state_diff["nonces"]["L1"],
        }
```

**File:** echonet/echo_center.py (L552-561)
```python
    @staticmethod
    def _compute_state_diff_length(blob: JsonObject) -> int:
        state_diff = blob["state_diff"]
        storage_updates = state_diff["storage_updates"]["L1"]
        return (
            len(state_diff["address_to_class_hash"])
            + len(state_diff["class_hash_to_compiled_class_hash"])
            + len(state_diff["nonces"]["L1"])
            + sum(len(slots) for slots in storage_updates.values())
        )
```

**File:** crates/starknet_api/src/block_hash/block_hash_calculator.rs (L259-261)
```rust
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

**File:** crates/apollo_batcher/src/block_builder.rs (L170-179)
```rust
        let (header_commitments, measurements) = calculate_block_commitments(
            &transactions_data,
            ThinStateDiff::from(commitment_state_diff.clone()),
            l1_da_mode,
            &block_info.starknet_version,
        )
        .await;
        record_and_log_block_commitment_measurements(block_info.block_number, measurements);
        let partial_block_hash_components =
            PartialBlockHashComponents::new(&block_info, header_commitments);
```

**File:** crates/apollo_batcher/src/block_builder.rs (L215-222)
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-104)
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

            if current_state_diff_len != target_state_diff_len {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffLength {
                    expected_length: target_state_diff_len,
                    possible_lengths: vec![prev_result_len, current_state_diff_len],
                }));
            }
```
