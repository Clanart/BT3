### Title
Missing State Diff Content Validation Against `state_diff_commitment` in P2P Sync Client — (`File: crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary

The P2P sync client assembles a `ThinStateDiff` from peer-supplied chunks and validates only that the total chunk length equals the `state_diff_length` stored in the block header. It never verifies that `calculate_state_diff_hash(&assembled_diff) == header.state_diff_commitment`. A malicious peer can therefore supply a state diff whose content differs from what the signed `state_diff_commitment` commits to, as long as the total entry count matches. The wrong state diff is written to storage, the committer builds the Patricia trie from it, and the resulting global root and all downstream RPC/execution state reads are wrong.

### Finding Description

The block header stores two independent descriptors of the state diff:

- `state_diff_commitment` — a Poseidon hash over the full content of the state diff, computed by `calculate_state_diff_hash` and committed into the block hash via `concatenated_counts` + `state_diff_commitment` fields.
- `state_diff_length` — a plain integer count of state diff entries, also packed into `concatenated_counts`. [1](#0-0) 

Both fields are part of the signed block hash, so the header's values are authentic once signature verification passes. The state diff *content*, however, is received separately over P2P as a stream of `StateDiffChunk` messages.

In `parse_data_for_block`, the client reads `target_state_diff_len` from the stored header and collects chunks until the running length counter reaches that target: [2](#0-1) 

After the loop exits at line 106–107, the function returns `Ok(Some((result, block_number)))` with no call to `calculate_state_diff_hash(&result)` and no comparison against `header.state_diff_commitment`. The only integrity check is the length counter.

The analogous check that *is* present in the committer (when `verify_state_diff_hash = true`) shows the intended pattern: [3](#0-2) 

The central sync path carries the same acknowledged gap, marked with a TODO: [4](#0-3) 

### Impact Explanation

A malicious P2P peer that has obtained a valid signed header for block N can:

1. Forward the authentic `SignedBlockHeader` (with correct `state_diff_commitment = H` and `state_diff_length = L`).
2. Craft a different `ThinStateDiff` S′ where `S′.len() == L` but `calculate_state_diff_hash(S′) != H` — e.g., by substituting storage slot values or nonces while keeping the entry count identical.
3. Send S′ as the state diff chunks. The client accepts it because `current_state_diff_len == target_state_diff_len`.

The wrong S′ is written to `apollo_storage`. The committer then reads S′ and commits it to the Patricia trie: [5](#0-4) 

This produces a wrong `global_root`, which is stored as the authoritative state root for block N. All subsequent RPC state queries (`starknet_getStorageAt`, `starknet_getNonce`, `starknet_getClassHashAt`) return values derived from the corrupted trie. If the node participates in block building, execution of subsequent blocks proceeds from a wrong state.

**Impact scope match:** High — RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value; and potentially Critical — wrong storage value / state root from execution logic for accepted input.

### Likelihood Explanation

Requires a malicious P2P peer that can obtain a valid signed header (available from any honest node on the network) and then serve the victim node during state diff sync. No privileged access is needed beyond being a reachable peer. The P2P network is explicitly untrusted, making this a realistic attacker model.

### Recommendation

After assembling the full `ThinStateDiff` from chunks, compute its hash and compare it against the `state_diff_commitment` stored in the block header before returning the result:

```rust
// After the assembly loop in parse_data_for_block:
let header = storage_reader.begin_ro_txn()?.get_block_header(block_number)?.unwrap();
if let Some(expected_commitment) = header.state_diff_commitment {
    let actual_commitment = calculate_state_diff_hash(&result);
    if actual_commitment != expected_commitment {
        return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffCommitment {
            block_number,
            expected: expected_commitment,
            actual: actual_commitment,
        }));
    }
}
```

This mirrors the existing check in `CommitterConfig::verify_state_diff_hash` and closes the gap acknowledged by the `TODO(dan)` comment in central sync. [6](#0-5) 

### Proof of Concept

1. Node A (victim) starts P2P sync from block 0.
2. Attacker peer B sends a valid `SignedBlockHeader` for block 0 with `state_diff_commitment = H`, `state_diff_length = 3`.
3. B sends three `StateDiffChunk` entries whose combined length is 3 but whose Poseidon hash is H′ ≠ H (e.g., same contract addresses/keys but different storage values).
4. `parse_data_for_block` exits the loop at line 99 with `current_state_diff_len == 3 == target_state_diff_len`, calls `validate_deprecated_declared_classes_non_conflicting`, and returns `Ok(Some((result, block_number)))` — no hash check.
5. The wrong state diff is written to storage. The committer reads it, builds the Patricia trie, and stores a wrong `global_root` for block 0.
6. `starknet_getStorageAt` on any key modified in the real state diff returns the attacker-supplied value instead of the correct one. [7](#0-6) [8](#0-7)

### Citations

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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L62-107)
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

            validate_deprecated_declared_classes_non_conflicting(&result)?;
            Ok(Some((result, block_number)))
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

**File:** crates/apollo_committer/src/committer.rs (L207-208)
```rust
        let (filled_forest, global_root) =
            self.commit_state_diff(state_diff, &mut block_measurements).await?;
```

**File:** crates/apollo_central_sync/src/lib.rs (L441-443)
```rust
    ) -> StateSyncResult {
        // TODO(dan): verifications - verify state diff against stored header.
        debug!("Storing state diff.");
```

**File:** crates/apollo_committer_config/src/config.rs (L22-23)
```rust
    pub verify_state_diff_hash: bool,
}
```

**File:** crates/apollo_storage/src/header.rs (L98-107)
```rust
    /// The state diff commitment, if available.
    pub state_diff_commitment: Option<StateDiffCommitment>,
    /// The transaction commitment, if available.
    pub transaction_commitment: Option<TransactionCommitment>,
    /// The event commitment, if available.
    pub event_commitment: Option<EventCommitment>,
    /// The receipt commitment, if available.
    pub receipt_commitment: Option<ReceiptCommitment>,
    /// The length of the state diff, if available.
    pub state_diff_length: Option<usize>,
```
