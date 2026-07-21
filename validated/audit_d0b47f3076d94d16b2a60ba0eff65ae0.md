### Title
Missing state diff commitment verification in P2P sync state diff assembly - (File: crates/apollo_p2p_sync/src/client/state_diff.rs)

### Summary
`parse_data_for_block` assembles a `ThinStateDiff` from peer-supplied `StateDiffChunk` messages and validates only that the accumulated chunk-length sum equals `state_diff_length` from the stored block header. It never computes the Poseidon hash of the assembled diff and compares it against the `state_diff_commitment` also present in that header. A malicious peer can substitute arbitrary state diff content — preserving only the total length count — and cause the node to persist a wrong state diff, producing wrong storage values, class hashes, and nonces for every subsequent RPC query against that block.

### Finding Description

In `parse_data_for_block` the function:

1. Reads `target_state_diff_len` from the stored header's `state_diff_length` field.
2. Accumulates `current_state_diff_len += state_diff_chunk.len()` for each received chunk.
3. Merges each chunk into `result` via `unite_state_diffs`.
4. After the loop, calls `validate_deprecated_declared_classes_non_conflicting` to reject duplicate deprecated class hashes.
5. Returns the assembled `ThinStateDiff`. [1](#0-0) 

At no point does the function read `state_diff_commitment` from the stored header, call `calculate_state_diff_hash` on the assembled diff, or compare the result against the stored commitment. [2](#0-1) 

The `state_diff_commitment` is stored in the header (received from the peer and written by the header sync) but is never used as a validation gate for the assembled state diff content. [3](#0-2) 

This is the direct analog to the external bug. In `EscrowManager.createLock()`, the beneficiary of each vesting wallet is checked (analogous to checking the total chunk length) but the wallet address itself is not validated against a whitelist/factory (analogous to not checking the assembled content against `state_diff_commitment`). Both allow a "poisoned" input — a custom contract that does nothing on `transfer()` there, an arbitrary-content chunk here — to satisfy the only guard that is present.

The `chain_deprecated_declared_classes` function chains the count and sorted elements of `deprecated_declared_classes`. [4](#0-3) 

If the assembled diff has wrong content (different class hashes, storage values, nonces), the computed hash diverges from the legitimate `state_diff_commitment`, but this discrepancy is never detected.

`ThinStateDiff::len()` counts `deprecated_declared_classes.len()` (a `Vec`, not a set), so the length check is purely arithmetic and carries no content guarantee. [5](#0-4) 

`StateDiffChunk::len()` returns 1 for both `DeclaredClass` and `DeprecatedDeclaredClass`, so any combination of K chunks of those types satisfies a `target_state_diff_len = K` check regardless of which class hashes they carry. [6](#0-5) 

### Impact Explanation

A malicious P2P peer can send state diff chunks that form a completely different state diff from the actual block, as long as the total `StateDiffChunk::len()` sum equals `state_diff_length` from the header. The wrong diff is stored via `append_state_diff` and becomes the authoritative state for that block number. [7](#0-6) 

All subsequent RPC queries for storage values, class hashes, nonces, and deployed contracts for that block return wrong values. The node's Patricia trie is built from wrong data, causing the computed state root to diverge from the actual chain. This matches the **High** impact: "RPC execution, fee estimation, tracing, simulation, or pending view returns an authoritative-looking wrong value."

### Likelihood Explanation

Any P2P peer the node connects to can exploit this. The peer needs only the `state_diff_length` of the target block, which is public information carried in the block header. No special privileges are required. The peer sends any combination of `StateDiffChunk` messages that sum to the correct length with arbitrary content.

### Recommendation

After `validate_deprecated_declared_classes_non_conflicting` and before returning, compute the state diff hash and compare it against the stored commitment:

```rust
// crates/apollo_p2p_sync/src/client/state_diff.rs, after line 106
let header = storage_reader
    .begin_ro_txn()?
    .get_block_header(block_number)?
    .expect("header must exist");
if let Some(expected_commitment) = header.state_diff_commitment {
    let actual_commitment = calculate_state_diff_hash(&result);
    if actual_commitment != expected_commitment {
        return Err(ParseDataError::BadPeer(
            BadPeerError::WrongStateDiffCommitment {
                expected: expected_commitment,
                actual: actual_commitment,
            },
        ));
    }
}
```

`calculate_state_diff_hash` is already available in `starknet_api::block_hash::state_diff_hash`. [2](#0-1) 

### Proof of Concept

1. A malicious peer connects to the syncing node.
2. The peer sends a signed block header for block N with `state_diff_length = K` and an arbitrary `state_diff_commitment = C`. The header sync stores it without verifying signature validity (only signature count is checked). [8](#0-7) 

3. The state diff sync reads `target_state_diff_len = K` from the stored header. [9](#0-8) 

4. The peer sends K `StateDiffChunk` messages — for example, K `DeprecatedDeclaredClass` chunks with attacker-chosen class hashes, or K storage updates with attacker-chosen keys and values — that sum to length K but represent a completely different state diff from the actual block.
5. `parse_data_for_block` accepts the chunks: total length matches, no duplicates. [10](#0-9) 

6. The wrong state diff is stored via `append_state_diff`.
7. RPC calls to `starknet_getStorageAt`, `starknet_getClassAt`, `starknet_getNonce` for block N return the attacker-chosen wrong values.
8. The node's state root for block N diverges from the actual chain, and any proof or commitment derived from this node's storage is incorrect.

### Citations

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L26-39)
```rust
impl BlockData for (ThinStateDiff, BlockNumber) {
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

**File:** crates/apollo_p2p_sync/src/client/state_diff.rs (L59-107)
```rust
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

**File:** crates/starknet_api/src/block_hash/state_diff_hash.rs (L71-80)
```rust
fn chain_deprecated_declared_classes(
    deprecated_declared_classes: &[ClassHash],
    hash_chain: HashChain,
) -> HashChain {
    let mut sorted_deprecated_declared_classes = deprecated_declared_classes.to_vec();
    sorted_deprecated_declared_classes.sort_unstable();
    hash_chain
        .chain(&sorted_deprecated_declared_classes.len().into())
        .chain_iter(sorted_deprecated_declared_classes.iter().map(|class_hash| &class_hash.0))
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

**File:** crates/apollo_protobuf/src/sync.rs (L147-162)
```rust
    pub fn len(&self) -> usize {
        match self {
            StateDiffChunk::ContractDiff(contract_diff) => {
                let mut result = contract_diff.storage_diffs.len();
                if contract_diff.class_hash.is_some() {
                    result += 1;
                }
                if contract_diff.nonce.is_some() {
                    result += 1;
                }
                result
            }
            StateDiffChunk::DeclaredClass(_) => 1,
            StateDiffChunk::DeprecatedDeclaredClass(_) => 1,
        }
    }
```

**File:** crates/apollo_storage/src/state/mod.rs (L514-590)
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
    }
```

**File:** crates/apollo_p2p_sync/src/client/header.rs (L104-120)
```rust
            if block_number
                != signed_block_header.block_header.block_header_without_hash.block_number
            {
                return Err(ParseDataError::BadPeer(BadPeerError::HeadersUnordered {
                    expected_block_number: block_number,
                    actual_block_number: signed_block_header
                        .block_header
                        .block_header_without_hash
                        .block_number,
                }));
            }
            if signed_block_header.signatures.len() != ALLOWED_SIGNATURES_LENGTH {
                return Err(ParseDataError::BadPeer(BadPeerError::WrongSignaturesLength {
                    signatures: signed_block_header.signatures,
                }));
            }
            Ok(Some(signed_block_header))
```
