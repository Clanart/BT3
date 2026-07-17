Looking at the full `set_state_header` function in `chain/chain/src/state_sync/adapter.rs`, I can identify a direct analog to the external bug. The function accepts a `shard_id` parameter and a `shard_state_header` containing an embedded `chunk`, but **never verifies that `chunk.shard_id() == shard_id`**. The Merkle path check only proves the chunk is somewhere in the block's `chunk_headers_root` tree — it does not enforce which shard position the chunk occupies.

---

### Title
Missing Shard-ID Binding Check in `set_state_header` Allows Cross-Shard State Corruption During State Sync — (File: `chain/chain/src/state_sync/adapter.rs`)

### Summary
`ChainStateSyncAdapter::set_state_header` accepts a caller-supplied `shard_id` and a `ShardStateSyncResponseHeader` containing an embedded `ShardChunk`. It validates the chunk's internal proofs and its Merkle inclusion in the block, but never asserts that `chunk.shard_id() == shard_id`. Any peer can supply a valid chunk for shard X while claiming it belongs to shard Y. The header passes all checks and is persisted under `StateHeaderKey(shard_Y, sync_hash)`. Subsequent `set_state_finalize` then applies shard X's state root to shard Y's trie storage, permanently corrupting the syncing node's state.

### Finding Description

`set_state_header` performs the following checks on the received header:

1. **Chunk internal proofs** (`validate_chunk_proofs`) — validates encoded-part Merkle roots; does not inspect `shard_id`.
2. **Merkle inclusion** (`verify_path` against `sync_prev_block_header.chunk_headers_root()`) — proves the chunk hash appears somewhere in the block's chunk-header Merkle tree, but the tree covers all shards. A chunk at position 0 (shard 0) has a valid Merkle path that passes `verify_path` regardless of what `shard_id` the caller claims.
3. **Receipt proofs, state root node** — unrelated to shard binding. [1](#0-0) 

After all checks pass, the header is stored under the key derived from the caller-supplied `shard_id`: [2](#0-1) 

`set_state_finalize` then retrieves this header by `(shard_id, sync_hash)` and applies the embedded chunk's `prev_state_root()` to the trie storage for `shard_uid` derived from the parameter `shard_id` — not from the chunk itself: [3](#0-2) 

The missing check is:
```rust
if chunk.shard_id() != shard_id {
    return Err(Error::InvalidStateRequest(
        format!("chunk shard_id {} != requested shard_id {}", chunk.shard_id(), shard_id).into()
    ));
}
```

### Impact Explanation
A syncing node that downloads a malicious state sync header from any peer will:
1. Accept the header (all existing checks pass).
2. Store it under the wrong shard's DB key (`DBCol::StateHeaders`).
3. On `set_state_finalize`, apply shard X's trie data into shard Y's flat storage and trie columns.
4. Produce a corrupted `ChunkExtra` with a wrong `state_root` for shard Y.
5. Produce invalid chunks for shard Y, causing the node to be slashed or kicked out of the validator set, and permanently diverge from the canonical chain.

**Impact: High** — permanent local state corruption of a syncing validator node, causing loss of validator rewards and potential slashing.

### Likelihood Explanation
State sync is triggered whenever a node falls behind or joins the network. The header is downloaded from any reachable peer (not just trusted validators). The `StateRequestActor` serves headers to any peer that requests them, and the downloader accepts headers from any source. No authentication of the header's shard binding is performed before `set_state_header` is called. [4](#0-3) 

**Likelihood: Medium** — requires a malicious peer to be reachable during state sync, which is feasible on a public network.

### Recommendation
Add an explicit shard-ID binding check immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
// NEW: verify the chunk belongs to the requested shard
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::InvalidStateRequest(
        format!(
            "set_shard_state failed: chunk shard_id {} != requested shard_id {}",
            chunk.shard_id(), shard_id
        ).into()
    ));
}
```

This mirrors the existing shard-ID check in `compute_state_response_header`: [5](#0-4) 

### Proof of Concept

1. Node B is syncing and requests a state header for `shard_id = 1` from malicious peer M.
2. M constructs a `ShardStateSyncResponseHeader` containing the valid chunk for `shard_id = 0` (with its correct Merkle path against `chunk_headers_root`).
3. M sends this header in response to the `shard_id = 1` request.
4. Node B calls `set_state_header(shard_id=1, sync_hash, header_with_shard0_chunk)`.
5. `validate_chunk_proofs` passes (chunk 0's internal proofs are valid).
6. `verify_path(*sync_prev_block_header.chunk_headers_root(), chunk0_proof, ChunkHashHeight(chunk0_hash, ...))` passes — chunk 0 is legitimately in the block.
7. Receipt and state root checks pass.
8. Header is stored under `StateHeaderKey(shard_id=1, sync_hash)`.
9. `set_state_finalize(shard_id=1, sync_hash)` retrieves this header, extracts `chunk0.prev_state_root()`, and applies it to `shard_uid` for shard 1.
10. Shard 1's trie and flat storage are now populated with shard 0's state data. Node B's shard 1 state is permanently corrupted. [6](#0-5) [7](#0-6)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L82-85)
```rust
        let shard_ids = self.epoch_manager.shard_ids(sync_block_epoch_id)?;
        if !shard_ids.contains(&shard_id) {
            return Err(shard_id_out_of_bounds(shard_id));
        }
```

**File:** chain/chain/src/state_sync/adapter.rs (L368-403)
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L525-529)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** chain/chain/src/chain_update.rs (L460-521)
```rust
        let (chunk, incoming_receipts_proofs) = match shard_state_header {
            ShardStateSyncResponseHeader::V1(shard_state_header) => (
                ShardChunk::V1(shard_state_header.chunk),
                shard_state_header.incoming_receipts_proofs,
            ),
            ShardStateSyncResponseHeader::V2(shard_state_header) => {
                (shard_state_header.chunk, shard_state_header.incoming_receipts_proofs)
            }
        };

        // Note that block headers are already synced and can be taken
        // from store on disk.
        let block_header = get_block_header_on_chain_by_height(
            &self.chain_store_update.chain_store(),
            &sync_hash,
            chunk.height_included(),
        )?;

        // Getting actual incoming receipts.
        let mut receipt_proof_responses: Vec<ReceiptProofResponse> = vec![];
        for incoming_receipt_proof in &incoming_receipts_proofs {
            let ReceiptProofResponse(hash, _) = incoming_receipt_proof;
            let block_header = self.chain_store_update.get_block_header(hash)?;
            if block_header.height() <= chunk.height_included() {
                receipt_proof_responses.push(incoming_receipt_proof.clone());
            }
        }
        let receipts = collect_receipts_from_response(&receipt_proof_responses);
        let is_genesis = block_header.height() == self.chain_store_update.get_genesis_height();
        let prev_block_header = (!is_genesis)
            .then(|| self.chain_store_update.get_block_header(block_header.prev_hash()))
            .transpose()?;

        // Prev block header should be present during state sync, since headers have been synced at
        // this point, except for genesis.
        let gas_price = if let Some(prev_block_header) = &prev_block_header {
            prev_block_header.next_gas_price()
        } else {
            block_header.next_gas_price()
        };

        let chunk_header = chunk.cloned_header();
        let gas_limit = chunk_header.gas_limit();
        let block = self.chain_store_update.get_block(block_header.hash())?;
        let transactions = chunk.to_transactions().to_vec();
        let transaction_validity = if let Some(prev_block_header) = prev_block_header {
            self.chain_store_update
                .chain_store()
                .compute_transaction_validity(&prev_block_header, &chunk)
        } else {
            vec![true; transactions.len()]
        };
        let transactions = SignedValidPeriodTransactions::new(transactions, transaction_validity);
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, chunk_header.prev_state_root())?;
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
            ApplyChunkReason::UpdateTrackedShard,
```

**File:** chain/client/src/sync/state/downloader.rs (L65-88)
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
```
