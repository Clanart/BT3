### Title
Missing `state_diff_commitment` Verification Against Assembled State Diff in P2P Sync Client - (`File: crates/apollo_p2p_sync/src/client/state_diff.rs`)

### Summary

The P2P sync client assembles a `ThinStateDiff` from peer-supplied chunks using only the `state_diff_length` field from the stored block header as a termination and length-equality guard. The `state_diff_commitment` (Poseidon hash of the state diff) that is also present in the same header is never verified against the assembled result. A malicious peer can supply chunks whose individual lengths sum to the correct `state_diff_length` but whose content differs from what was committed, causing the node to persist a wrong state diff, compute a wrong global state root, and serve wrong authoritative state values over RPC.

### Finding Description

`parse_data_for_block` in `crates/apollo_p2p_sync/src/client/state_diff.rs` reads `target_state_diff_len` from the stored header and loops until `current_state_diff_len == target_state_diff_len`: [1](#0-0) 

The loop terminates and the assembled diff is accepted as soon as the length matches: [2](#0-1) 

The header also carries `state_diff_commitment` (a Poseidon hash over the full diff content): [3](#0-2) 

That commitment is stored in the block header: [4](#0-3) 

But `parse_data_for_block` never reads `state_diff_commitment` from the header and never calls `calculate_state_diff_hash` on the assembled result to compare. The only content-integrity checks are duplicate-key detection inside `unite_state_diffs` and the post-loop `validate_deprecated_declared_classes_non_conflicting` call: [5](#0-4) 

The assembled diff is then written directly to MDBX storage: [6](#0-5) 

`append_state_diff` performs no hash verification either: [7](#0-6) 

The committer's hash verification (`verify_state_diff_hash`) is only exercised in the block-production path (batcher → committer), not in the P2P sync path: [8](#0-7) 

### Impact Explanation

A malicious P2P peer supplies `StateDiffChunk` messages whose `len()` values sum to the correct `target_state_diff_len` but whose content (storage values, class hashes, nonces, compiled class hashes) differs from the real block. The node stores this fabricated `ThinStateDiff`. The committer then uses it to update the Patricia Merkle trees, producing a wrong `GlobalRoot`. Downstream effects:

- `starknet_getStorageAt`, `starknet_getClassHashAt`, `starknet_getNonce` return wrong authoritative values (High: RPC returns authoritative-looking wrong value).
- The wrong global root is embedded in subsequent block hashes and proof inputs, corrupting the commitment chain (Critical: wrong state root / block commitment).
- Fee estimation and simulation execute against wrong state, returning wrong gas/fee values (High: wrong fee/resource accounting).

### Likelihood Explanation

Any node participating in the P2P network can act as a sync peer. No privileged position is required. The attacker only needs to respond to a sync query with chunks that satisfy the length check. The header's validator signatures cannot be forged, but the peer is free to serve any chunk content once the header is accepted. The missing check is a single absent call to `calculate_state_diff_hash` after the assembly loop.

### Recommendation

After the assembly loop in `parse_data_for_block`, read `state_diff_commitment` from the stored header and verify:

```rust
let expected_commitment = header.state_diff_commitment
    .ok_or(P2pSyncClientError::OldHeaderInStorage {
        block_number,
        missing_field: "state_diff_commitment",
    })?;
let computed_commitment = calculate_state_diff_hash(&result);
if computed_commitment != expected_commitment {
    return Err(ParseDataError::BadPeer(BadPeerError::WrongStateDiffCommitment {
        block_number,
        expected: expected_commitment,
        computed: computed_commitment,
    }));
}
```

This mirrors the check already present in the committer for the block-production path.

### Proof of Concept

1. Honest node A stores a signed header for block N with `state_diff_length = 2` and `state_diff_commitment = H`.
2. Malicious peer B responds to the state diff query with two `ContractDiff` chunks, each of length 1, but with fabricated storage values (e.g., setting an ERC-20 balance slot to an attacker-controlled value). The total length is 2, satisfying the loop termination condition.
3. `parse_data_for_block` returns `Ok(Some((fabricated_diff, N)))`.
4. `write_to_storage` calls `append_state_diff(N, fabricated_diff)` — no hash check.
5. The committer reads the fabricated diff from storage and updates the Patricia trie, producing a wrong `GlobalRoot`.
6. `starknet_getStorageAt` for the manipulated slot now returns the attacker-supplied value.
7. Any proof generated over this block uses the wrong state diff as input, producing an invalid SNOS proof or a proof over wrong state. [9](#0-8) [10](#0-9)

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L27-39)
```rust
    #[latency_histogram("p2p_sync_state_diff_write_to_storage_latency_seconds", true)]
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
    }
```

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L58-110)
```rust
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L127-178)
```rust
fn unite_state_diffs(
    state_diff: &mut ThinStateDiff,
    state_diff_chunk: StateDiffChunk,
) -> Result<(), BadPeerError> {
    match state_diff_chunk {
        StateDiffChunk::ContractDiff(contract_diff) => {
            if let Some(class_hash) = contract_diff.class_hash {
                if state_diff
                    .deployed_contracts
                    .insert(contract_diff.contract_address, class_hash)
                    .is_some()
                {
                    return Err(BadPeerError::ConflictingStateDiffParts);
                }
            }
            if let Some(nonce) = contract_diff.nonce {
                if state_diff.nonces.insert(contract_diff.contract_address, nonce).is_some() {
                    return Err(BadPeerError::ConflictingStateDiffParts);
                }
            }
            if !contract_diff.storage_diffs.is_empty() {
                match state_diff.storage_diffs.get_mut(&contract_diff.contract_address) {
                    Some(storage_diffs) => {
                        for (k, v) in contract_diff.storage_diffs {
                            if storage_diffs.insert(k, v).is_some() {
                                return Err(BadPeerError::ConflictingStateDiffParts);
                            }
                        }
                    }
                    None => {
                        state_diff
                            .storage_diffs
                            .insert(contract_diff.contract_address, contract_diff.storage_diffs);
                    }
                }
            }
        }
        StateDiffChunk::DeclaredClass(declared_class) => {
            if state_diff
                .class_hash_to_compiled_class_hash
                .insert(declared_class.class_hash, declared_class.compiled_class_hash)
                .is_some()
            {
                return Err(BadPeerError::ConflictingStateDiffParts);
            }
        }
        StateDiffChunk::DeprecatedDeclaredClass(deprecated_declared_class) => {
            state_diff.deprecated_declared_classes.push(deprecated_declared_class.class_hash);
        }
    }
    Ok(())
}
```

**File:** crates/apollo_protobuf/src/proto/p2p/proto/sync/common.proto (L6-9)
```text
message StateDiffCommitment {
    uint64 state_diff_length = 1;
    Hash root = 2;
}
```

**File:** crates/apollo_protobuf/src/converters/header.rs (L209-210)
```rust
                state_diff_commitment,
                state_diff_length,
```

**File:** crates/apollo_storage/src/state/mod.rs (L514-589)
```rust
impl StateStorageWriter for StorageTxn<'_, RW> {
    #[sequencer_latency_histogram(STORAGE_APPEND_THIN_STATE_DIFF_LATENCY, false)]
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
