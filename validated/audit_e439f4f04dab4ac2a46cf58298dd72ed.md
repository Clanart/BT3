### Title
Missing `shard_id` Binding Check in `set_state_header` Allows Cross-Shard State Corruption During State Sync — (File: `chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`ChainStateSyncAdapter::set_state_header` accepts a caller-supplied `shard_id` and a peer-supplied `ShardStateSyncResponseHeader` that embeds a `ShardChunk` with its own `shard_id()`. The function validates that the embedded chunk is included in the block's Merkle tree, but **never checks that `chunk.shard_id() == shard_id`**. A malicious peer can supply a header whose embedded chunk belongs to a different shard, causing the syncing node to store a corrupted state header, validate and apply state parts against the wrong shard's state root, and ultimately write a foreign shard's trie data into the target shard's storage slot.

---

### Finding Description

`set_state_header` in `chain/chain/src/state_sync/adapter.rs` takes two independent shard identifiers:

1. The caller-provided `shard_id: ShardId` parameter — the shard the syncing node intends to restore.
2. The `shard_id()` embedded inside `shard_state_header.cloned_chunk()` — the shard the chunk actually belongs to. [1](#0-0) 

The function extracts the chunk and validates it in two steps:

**Step 1–2** — `validate_chunk_proofs` checks the chunk's internal consistency (transaction Merkle root, signature, etc.) but does not compare the chunk's embedded `shard_id` against the caller-supplied `shard_id`.

**Step 3a** — `verify_path` proves the chunk is somewhere in the block's `chunk_headers_root` Merkle tree: [2](#0-1) 

The Merkle path proves the chunk is *a member* of the block's chunk list, but does **not** prove it occupies the slot corresponding to the requested `shard_id`. A block contains one chunk per shard; a chunk for shard B has a valid Merkle proof against the same `chunk_headers_root` as a chunk for shard A.

**No check is ever performed** that `chunk.shard_id() == shard_id`. The `ShardChunk` type exposes this accessor: [3](#0-2) 

After all validations pass, the corrupted header is persisted under the caller-supplied key: [4](#0-3) 

Downstream, `set_state_part` reads this stored header and extracts the state root from the embedded chunk — which is now shard B's state root — and validates incoming state parts against it: [5](#0-4) 

`apply_state_part` then writes those parts into shard A's trie storage slot (using `shard_uid` derived from the caller-supplied `shard_id=A`), but the data is shard B's trie: [6](#0-5) 

Finally, `set_state_finalize` applies shard B's chunk (transactions, receipts) under shard A's `shard_uid`, completing the corruption: [7](#0-6) 

---

### Impact Explanation

A syncing node that accepts a crafted `ShardStateSyncResponseHeader` from a malicious peer will:

1. Store a state header for shard A that contains shard B's chunk and state root.
2. Accept and store state parts that are valid for shard B's state root (not shard A's).
3. Apply shard B's trie data into shard A's storage slot.
4. Apply shard B's transactions and receipts as if they were shard A's, producing an incorrect `ChunkExtra` and state root for shard A.

The node's shard A state diverges from the canonical chain. The node will fail to produce or validate blocks for shard A, effectively being permanently knocked off the network for that shard. **Impact: High** — permanent state corruption of a syncing node, causing chain divergence.

---

### Likelihood Explanation

State sync downloads headers from peers via `StateSyncDownloader::ensure_shard_header`, which forwards the peer-supplied header directly to `set_state_header` via `StateHeaderValidationRequest`: [8](#0-7) 

Any peer the syncing node connects to can serve a crafted response. No privileged role is required. The attack requires the attacker to:

1. Serve a `ShardStateSyncResponseHeader` whose embedded chunk is for shard B (legitimately in the block, so the Merkle proof is valid).
2. Populate `incoming_receipts_proofs` with the actual incoming receipts for shard A (so the `ReceiptList(shard_id=A, ...)` hash check at step 4e passes).
3. Serve state parts that are valid for shard B's state root.

All three are constructible from public chain data. **Likelihood: Medium** — requires a malicious peer but no privileged access.

---

### Recommendation

Add an explicit shard binding check immediately after extracting the chunk from the header, before any other validation:

```rust
let chunk = shard_state_header.cloned_chunk();
// Analog fix: verify the chunk's embedded shard_id matches the requested shard_id
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This should be inserted at line 377, immediately after `cloned_chunk()` is called. [9](#0-8) 

---

### Proof of Concept

1. Syncing node N requests state header for `shard_id = A`, `sync_hash = H`.
2. Malicious peer P responds with a `ShardStateSyncResponseHeader` where:
   - `chunk` = the legitimate chunk for shard B at block `H-1` (valid Merkle proof against `chunk_headers_root`).
   - `incoming_receipts_proofs` = the actual incoming receipts for shard A at block `H` (so `ReceiptList(A, receipts)` hashes correctly against the block's outgoing receipts root).
   - `state_root_node` = shard B's state root node.
3. `set_state_header(shard_id=A, sync_hash=H, header)` passes all checks (no `chunk.shard_id() == shard_id` check) and stores the header under `StateHeaderKey(A, H)`.
4. P serves state parts for shard B's trie. `set_state_part` validates them against shard B's state root (extracted from the stored header) — they pass.
5. `apply_state_part(shard_id=A, state_root=B_root, ...)` writes shard B's trie nodes into shard A's RocksDB column.
6. `set_state_finalize(shard_id=A, sync_hash=H)` applies shard B's chunk (transactions, receipts) under shard A's `shard_uid`, producing a corrupted `ChunkExtra` for shard A.
7. Node N's shard A state is now shard B's data; N diverges from the canonical chain and cannot participate in shard A consensus. [10](#0-9)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L368-532)
```rust
    pub fn set_state_header(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        shard_state_header: ShardStateSyncResponseHeader,
    ) -> Result<(), Error> {
        let sync_block_header = self.chain_store.get_block_header(&sync_hash)?;

        let chunk = shard_state_header.cloned_chunk();
        let prev_chunk_header = shard_state_header.cloned_prev_chunk_header();

        // 1-2. Checking chunk validity
        if !validate_chunk_proofs(&chunk, self.epoch_manager.as_ref())? {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk header proofs are invalid".into(),
            ));
        }

        // Consider chunk itself is valid.

        // 3. Checking that chunks `chunk` and `prev_chunk` are included in appropriate blocks
        // 3a. Checking that chunk `chunk` is included into block at last height before sync_hash
        // 3aa. Also checking chunk.height_included
        let sync_prev_block_header =
            self.chain_store.get_block_header(sync_block_header.prev_hash())?;
        if !verify_path(
            *sync_prev_block_header.chunk_headers_root(),
            shard_state_header.chunk_proof(),
            &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(
                "set_shard_state failed: chunk isn't included into block".into(),
            ));
        }

        let block_header = get_block_header_on_chain_by_height(
            &self.chain_store,
            &sync_hash,
            chunk.height_included(),
        )?;
        // 3b. Checking that chunk `prev_chunk` is included into block at height before chunk.height_included
        // 3ba. Also checking prev_chunk.height_included - it's important for getting correct incoming receipts
        match (&prev_chunk_header, shard_state_header.prev_chunk_proof()) {
            (Some(prev_chunk_header), Some(prev_chunk_proof)) => {
                let prev_block_header =
                    self.chain_store.get_block_header(block_header.prev_hash())?;
                if !verify_path(
                    *prev_block_header.chunk_headers_root(),
                    prev_chunk_proof,
                    &ChunkHashHeight(prev_chunk_header.chunk_hash().clone(), prev_chunk_header.height_included()),
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other(
                        "set_shard_state failed: prev_chunk isn't included into block".into(),
                    ));
                }
            }
            (None, None) => {
                if chunk.height_included() != 0 {
                    return Err(Error::Other(
                    "set_shard_state failed: received empty state response for a chunk that is not at height 0".into()
                ));
                }
            }
            _ =>
                return Err(Error::Other("set_shard_state failed: `prev_chunk_header` and `prev_chunk_proof` must either both be present or both absent".into()))
        };

        // 4. Proving incoming receipts validity
        // 4a. Checking len of proofs
        if shard_state_header.root_proofs().len()
            != shard_state_header.incoming_receipts_proofs().len()
        {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
        }
        let mut hash_to_compare = sync_hash;
        for (i, receipt_response) in
            shard_state_header.incoming_receipts_proofs().iter().enumerate()
        {
            let ReceiptProofResponse(block_hash, receipt_proofs) = receipt_response;

            // 4b. Checking that there is a valid sequence of continuous blocks
            if *block_hash != hash_to_compare {
                byzantine_assert!(false);
                return Err(Error::Other(
                    "set_shard_state failed: invalid incoming receipts".into(),
                ));
            }
            let header = self.chain_store.get_block_header(&hash_to_compare)?;
            hash_to_compare = *header.prev_hash();

            let block_header = self.chain_store.get_block_header(block_hash)?;
            // 4c. Checking len of receipt_proofs for current block
            if receipt_proofs.len() != shard_state_header.root_proofs()[i].len()
                || receipt_proofs.len() != block_header.chunks_included() as usize
            {
                byzantine_assert!(false);
                return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
            }
            // We know there were exactly `block_header.chunks_included` chunks included
            // on the height of block `block_hash`.
            // There were no other proofs except for included chunks.
            // According to Pigeonhole principle, it's enough to ensure all receipt_proofs are distinct
            // to prove that all receipts were received and no receipts were hidden.
            let mut visited_shard_ids = HashSet::<ShardId>::new();
            for (j, receipt_proof) in receipt_proofs.iter().enumerate() {
                let ReceiptProof(receipts, shard_proof) = receipt_proof;
                let ShardProof { from_shard_id, to_shard_id: _, proof } = shard_proof;
                // 4d. Checking uniqueness for set of `from_shard_id`
                match visited_shard_ids.get(from_shard_id) {
                    Some(_) => {
                        byzantine_assert!(false);
                        return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                    }
                    _ => visited_shard_ids.insert(*from_shard_id),
                };
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
                // 4f. Proving the outgoing_receipts_root matches that in the block
                if !verify_path(
                    *block_header.prev_chunk_outgoing_receipts_root(),
                    block_proof,
                    root,
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
            }
        }
        // 4g. Checking that there are no more heights to get incoming_receipts
        let header = self.chain_store.get_block_header(&hash_to_compare)?;
        if header.height() != prev_chunk_header.map_or(0, |h| h.height_included()) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid incoming receipts".into()));
        }

        // 5. Checking that state_root_node is valid
        let chunk_inner = chunk.take_header().take_inner();
        if matches!(
            self.runtime_adapter.validate_state_root_node(
                shard_state_header.state_root_node(),
                chunk_inner.prev_state_root(),
            ),
            StateRootNodeValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: state_root_node is invalid".into()));
        }

        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();

        Ok(())
    }
```

**File:** chain/chain/src/state_sync/adapter.rs (L541-553)
```rust
        let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
        let chunk = shard_state_header.take_chunk();
        let state_root = *chunk.take_header().take_inner().prev_state_root();
        if matches!(
            self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
            StatePartValidationResult::Invalid
        ) {
            byzantine_assert!(false);
            return Err(Error::Other(format!(
                "set_state_part failed: validate_state_part failed. state_root={:?}",
                state_root
            )));
        }
```

**File:** core/primitives/src/sharding.rs (L1121-1126)
```rust
    pub fn shard_id(&self) -> ShardId {
        match self {
            Self::V1(chunk) => chunk.header.inner.shard_id,
            Self::V2(chunk) => chunk.header.shard_id(),
        }
    }
```

**File:** chain/chain/src/runtime/mod.rs (L1501-1527)
```rust
    fn apply_state_part(
        &self,
        shard_id: ShardId,
        state_root: &StateRoot,
        part_id: PartId,
        part: &StatePart,
        epoch_id: &EpochId,
    ) -> Result<(), Error> {
        let _timer = metrics::STATE_SYNC_APPLY_PART_DELAY
            .with_label_values(&[&shard_id.to_string()])
            .start_timer();

        let part = part
            .to_partial_state()
            .expect("Part was already validated earlier, so could never fail here");
        let ApplyStatePartResult { trie_changes, flat_state_delta, contract_codes } =
            Trie::apply_state_part(state_root, part_id, part);
        let tries = self.get_tries();
        let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, epoch_id)?;
        let mut store_update = tries.store_update();
        tries.apply_all(&trie_changes, shard_uid, &mut store_update);
        tracing::debug!(target: "chain", %shard_id, values_count = %flat_state_delta.len(), "inserting values to flat storage");
        // TODO: `apply_to_flat_state` inserts values with random writes, which can be time consuming.
        //       Optimize taking into account that flat state values always correspond to a consecutive range of keys.
        flat_state_delta.apply_to_flat_state(&mut store_update.flat_store_update(), shard_uid);
        self.precompile_contracts(epoch_id, contract_codes)?;
        store_update.commit();
```

**File:** chain/chain/src/chain_update.rs (L513-542)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, chunk_header.prev_state_root())?;
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
            ApplyChunkReason::UpdateTrackedShard,
            ApplyChunkShardContext {
                shard_uid,
                gas_limit,
                last_validator_proposals: chunk_header.prev_validator_proposals(),
                is_new_chunk: true,
                on_post_state_ready: None,
                memtrie_pin,
            },
            ApplyChunkBlockContext {
                block_type: BlockType::Normal,
                height: chunk_header.height_included(),
                prev_block_hash: *chunk_header.prev_block_hash(),
                block_timestamp: block_header.raw_timestamp(),
                gas_price,
                random_seed: *block_header.random_value(),
                congestion_info: block.block_congestion_info(),
                bandwidth_requests: block.block_bandwidth_requests(),
            },
            &receipts,
            transactions,
        )?;
```

**File:** chain/client/src/sync/state/downloader.rs (L65-90)
```rust
            let attempt = || {
                async {
                    let header = source
                        .download_shard_header(shard_id, sync_hash, handle.clone(), cancel.clone())
                        .await?;
                    // We cannot validate the header with just a Store. We need the Chain, so we queue it up
                    // so the chain can pick it up later, and we await until the chain gives us a response.
                    handle.set_status("Waiting for validation");
                    validation_sender
                        .send_async(
                            StateHeaderValidationRequest {
                                shard_id,
                                sync_hash,
                                header: header.clone(),
                            }
                            .span_wrap(),
                        )
                        .await
                        .map_err(|_| {
                            near_chain::Error::Other(
                                "Validation request could not be handled".to_owned(),
                            )
                        })??;
                    Ok::<ShardStateSyncResponseHeader, near_chain::Error>(header)
                }
            };
```
