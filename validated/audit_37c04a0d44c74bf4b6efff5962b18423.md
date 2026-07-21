### Title
P2P Sync State Diff Accepted Without Commitment Hash Validation, Allowing Malicious Peer to Inject Wrong Storage/Class/Nonce Values - (File: crates/apollo_p2p_sync/src/client/state_diff.rs)

### Summary

`parse_data_for_block` in the P2P sync state diff stream builder validates only the **length** of a received state diff against the stored block header, but never validates the state diff's **Poseidon hash** against the `state_diff_commitment.root` that is already stored in the authenticated block header. Any unprivileged P2P peer can therefore serve a state diff of the correct length but with arbitrary wrong content (storage values, class hashes, nonces), which is accepted and written verbatim into the node's storage.

### Finding Description

The `StateDiffCommitment` protobuf message carries two fields: `state_diff_length` (a count) and `root` (the Poseidon hash of the state diff). [1](#0-0) 

Block headers received over P2P are signed by the sequencer and stored with both fields. The `calculate_block_commitments` function computes `state_diff_commitment` as a Poseidon hash and embeds it in the block hash. [2](#0-1) 

When the P2P sync client later fetches the state diff for a block, `parse_data_for_block` reads only `state_diff_length` from the stored header and uses it as the sole acceptance criterion: [3](#0-2) 

After accumulating chunks until `current_state_diff_len == target_state_diff_len`, the assembled `ThinStateDiff` is returned and written directly to storage: [4](#0-3) 

There is no step that computes `calculate_state_diff_hash(&result)` and compares it against the `state_diff_commitment.root` already present in the stored header. The analog to the StWSX oracle bug is exact: just as `oracleReportRewards()` assumed the WSX balance was already present without checking, `parse_data_for_block` assumes the state diff content is correct by checking only the count, not the cryptographic commitment.

By contrast, the `apollo_committer` does perform this check when `verify_state_diff_hash` is enabled: [5](#0-4) 

And the central sync path carries an explicit TODO acknowledging the same missing check: [6](#0-5) 

The P2P path has no such guard and no such TODO.

### Impact Explanation

A malicious peer sends state diff chunks whose total `len()` equals `target_state_diff_len` but whose storage diffs, class hashes, or nonces are fabricated. The node stores the forged diff via `append_state_diff`: [7](#0-6) 

This corrupts the authoritative storage tables for contract storage, nonces, deployed contracts, and compiled class hashes. Every downstream consumer of these tables — the RPC server (`starknet_getStorageAt`, `starknet_getStateUpdate`, `starknet_getClassAt`), the gateway's state reader, and the proof manager's initial-reads path — will return or use the wrong values. This matches the **High** impact: *RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value.*

### Likelihood Explanation

The P2P sync is the production sync path when `p2p_sync_client_config` is set. Any peer that the node connects to can serve state diff responses. No special privilege is required; the attacker only needs to be a reachable peer. The length constraint is trivially satisfiable by constructing a diff with the same number of entries but different values.

### Recommendation

After assembling the full `ThinStateDiff` from chunks, compute its Poseidon commitment and compare it against the `state_diff_commitment.root` stored in the block header before returning `Ok(Some(...))`. Concretely, in `parse_data_for_block`, after the length check passes, add:

```rust
let stored_commitment = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("header must exist")
    .state_diff_commitment   // StateDiffCommitment { root, length }
    .root;
let computed = calculate_state_diff_hash(&result);
if computed != stored_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::StateDiffHashMismatch { ... }));
}
```

This mirrors the existing guard in `apollo_committer` and closes the same gap that the StWSX audit identified in the oracle reporting functions.

### Proof of Concept

1. Node A runs with `p2p_sync_client_config` enabled and has already stored a signed block header for block N containing `state_diff_commitment = { length: 3, root: 0xABC... }`.
2. Attacker peer B responds to Node A's state diff request for block N with three `StateDiffChunk` entries (satisfying `current_state_diff_len == 3`) but with fabricated storage values — e.g., setting the STRK fee-token balance of an address to `u128::MAX`.
3. `parse_data_for_block` accepts the diff (length matches), `write_to_storage` calls `append_state_diff`, and the forged storage values are committed to the MDBX tables.
4. A subsequent `starknet_getStorageAt` RPC call for that address and block returns `u128::MAX` — an authoritative-looking wrong value — with no error.
5. The `state_diff_commitment.root` (`0xABC...`) stored in the header is never consulted during this entire flow. [8](#0-7)

### Citations

**File:** crates/apollo_protobuf/src/proto/p2p/proto/sync/common.proto (L6-9)
```text
message StateDiffCommitment {
    uint64 state_diff_length = 1;
    Hash root = 2;
}
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L28-38)
```rust
    fn write_to_storage<'a>(
        self: Box<Self>,
        storage_writer: &'a mut StorageWriter,
        _class_manager_client: &'a mut SharedClassManagerClient,
    ) -> BoxFuture<'a, Result<(), P2pSyncClientError>> {
        async move {
            storage_writer.begin_rw_txn()?.append_state_diff(self.1, self.0)?.commit()?;
            STATE_SYNC_STATE_MARKER.set_lossy(self.1.unchecked_next().0);
            Ok(())
        }
        .boxed()
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L50-110)
```rust
    #[latency_histogram("p2p_sync_state_diff_parse_data_for_block_latency_seconds", true)]
    fn parse_data_for_block<'a>(
        state_diff_chunks_response_manager: &'a mut ClientResponsesManager<
            DataOrFin<StateDiffChunk>,
        >,
        block_number: BlockNumber,
        storage_reader: &'a StorageReader,
    ) -> BoxFuture<'a, Result<Option<Self::Output>, ParseDataError>> {
        async move {
            let mut result = ThinStateDiff::default();
            let mut prev_result_len = 0;
            let mut current_state_diff_len = 0;
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
        }
        .boxed()
    }
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

**File:** crates/apollo_central_sync/src/lib.rs (L441-443)
```rust
    ) -> StateSyncResult {
        // TODO(dan): verifications - verify state diff against stored header.
        debug!("Storing state diff.");
```

**File:** crates/apollo_storage/src/state/mod.rs (L516-589)
```rust
    fn append_state_diff(
        self,
        block_number: BlockNumber,
        thin_state_diff: ThinStateDiff,
    ) -> StorageResult<Self> {
        let file_offset_table = self.txn.open_table(&self.tables.file_offsets)?;
        let markers_table = self.open_table(&self.tables.markers)?;
        let state_diffs_table = self.open_table(&self.tables.state_diffs)?;
        let nonces_table = self.open_table(&self.tables.nonces)?;
        let deployed_contracts_table = self.open_table(&self.tables.deployed_contracts)?;
        let storage_table = self.open_table(&self.tables.contract_storage)?;
        let declared_classes_block_table = self.open_table(&self.tables.declared_classes_block)?;
        let deprecated_declared_classes_block_table =
            self.open_table(&self.tables.deprecated_declared_classes_block)?;
        let compiled_class_hash_table = self.open_table(&self.tables.compiled_class_hash)?;

        // Write state.
        write_deployed_contracts(
            &thin_state_diff.deployed_contracts,
            &self.txn,
            block_number,
            &deployed_contracts_table,
            &nonces_table,
        )?;
        write_storage_diffs(
            &thin_state_diff.storage_diffs,
            &self.txn,
            block_number,
            &storage_table,
        )?;
        // Must be called after write_deployed_contracts since the nonces are updated there.
        write_nonces(&thin_state_diff.nonces, &self.txn, block_number, &nonces_table)?;

        for (class_hash, _) in &thin_state_diff.class_hash_to_compiled_class_hash {
            let not_declared = declared_classes_block_table.get(&self.txn, class_hash)?.is_none();
            if not_declared {
                declared_classes_block_table.insert(&self.txn, class_hash, &block_number)?;
            }
        }

        write_compiled_class_hashes(
            &thin_state_diff.class_hash_to_compiled_class_hash,
            &self.txn,
            block_number,
            &compiled_class_hash_table,
        )?;

        for class_hash in thin_state_diff.deprecated_declared_classes.iter() {
            // Cairo0 classes can be declared in different blocks. The first block to declare the
            // class is recorded here.
            if deprecated_declared_classes_block_table.get(&self.txn, class_hash)?.is_none() {
                deprecated_declared_classes_block_table.insert(
                    &self.txn,
                    class_hash,
                    &block_number,
                )?;
            }
        }

        // Write state diff.
        let location = self.file_handlers.append_state_diff(&thin_state_diff);
        state_diffs_table.append(&self.txn, &block_number, &location)?;
        file_offset_table.upsert(&self.txn, &OffsetKind::ThinStateDiff, &location.next_offset())?;

        update_marker_to_next_block(&self.txn, &markers_table, MarkerKind::State, block_number)?;

        advance_compiled_class_marker_over_blocks_without_classes(
            &self.txn,
            &markers_table,
            &state_diffs_table,
            &self.file_handlers,
        )?;

        Ok(self)
```
