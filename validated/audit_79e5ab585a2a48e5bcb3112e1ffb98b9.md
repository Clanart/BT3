### Title
State-Sync Block Validation Error Silently Ignored, Invalid Block Committed to Orphan Pool — (File: chain/client/src/client_actor.rs)

### Summary

In `maybe_receive_state_sync_blocks`, three separate code paths call `validate_block`, detect a failure, log it with `tracing::error!` and fire `byzantine_assert!(false)`, but then **fall through** and unconditionally commit the invalid block to the orphan pool or persistent storage. Because the chunk-header hash is computed from the header *inner* (excluding the signature field), an unprivileged peer can strip or forge chunk-header signatures on a legitimately-observed block without changing the block hash. The tampered block passes the hash-equality gate (`block_hash == sync_hash`), fails `validate_block`, but is still persisted. The first write wins in the orphan pool, so the subsequent arrival of the genuine block is silently dropped, leaving the node unable to advance its chain after state sync completes.

### Finding Description

`maybe_receive_state_sync_blocks` is the entry point for blocks received during state sync. It is called from the `Handler<SpanWrapped<BlockResponse>>` implementation **before** `receive_block_impl`, which means the `verify_block_hash_and_signature` guard present in the normal block-receive path is bypassed entirely for state-sync blocks. [1](#0-0) 

Inside `maybe_receive_state_sync_blocks`, three branches share the same broken pattern:

```
if let Err(err) = self.client.chain.validate_block(&block) {
    byzantine_assert!(false);          // no-op in release builds
    tracing::error!(...);              // error is logged …
}
// … but execution falls through unconditionally:
self.client.chain.save_orphan(block, Provenance::NONE, false);
``` [2](#0-1) [3](#0-2) [4](#0-3) 

`validate_block` delegates to `validate_block_impl`, which verifies chunk-header signatures via `verify_chunk_header_signature_by_hash`: [5](#0-4) 

The chunk hash used for signature verification is computed from the header *inner* only, not from the signature field:

```rust
chunk.hash = ShardChunkHeaderV3::compute_hash(&chunk.inner);
```

Therefore, replacing a chunk-header signature with an invalid one does **not** change the chunk hash, does not change the `chunk_headers_root` committed in the block header, and does not change the block hash. The tampered block is indistinguishable from the legitimate block by hash alone.

`save_orphan` uses the block hash as the deduplication key and silently drops any later arrival with the same hash: [6](#0-5) 

After state sync completes, `reset_heads_post_state_sync` calls `check_orphans`, which dequeues the tampered `sync_hash` block and attempts to process it via `start_process_block_async`. Full block preprocessing detects the invalid chunk signatures and rejects the block. The legitimate block, which arrived later and was silently dropped, is no longer available. [7](#0-6) 

### Impact Explanation

A node completing state sync saves the tampered `sync_hash` block as its sole orphan candidate for the new epoch's first block. When `check_orphans` fires, the block is rejected due to invalid chunk signatures. The legitimate block was already discarded by the deduplication guard. The node is left with no valid candidate to advance its chain, stalling post-sync block production and validation until the node re-requests the block or the orphan pool evicts the stale entry and a fresh download succeeds. For the `prev_hash` and extra-block branches, the tampered block is written to persistent storage; the block header (which is hash-correct) is used for head-setting and tail computation, so the immediate state-reconstruction impact is lower, but the stored body with invalid signatures can cause downstream processing failures.

### Likelihood Explanation

Any peer connected to a syncing node can observe the `sync_hash` on the network, obtain the corresponding block, strip or replace its chunk-header signatures (a trivial byte-level edit), and send the result. No validator key or privileged role is required. The attack window is the entire duration of state sync, which can span many minutes on a slow node. The `byzantine_assert!(false)` is a no-op in release builds, so the only observable signal is a log line.

### Recommendation

Add an explicit `return` (or propagate the error) immediately after each `validate_block` failure inside `maybe_receive_state_sync_blocks`. The three affected sites are lines 1933–1936, 1944–1947, and 1960–1963 of `chain/client/src/client_actor.rs`. Additionally, consider calling `verify_block_hash_and_signature` before `validate_block` in this function, consistent with the guard already present in `receive_block_impl`.

### Proof of Concept

1. Observe the network during a target node's state sync to learn `sync_hash`.
2. Obtain the block with that hash from any honest peer.
3. Deserialize the block, replace every `ShardChunkHeader.signature` field with a random 64-byte value, and re-serialize. The block hash is unchanged because `chunk_hash = hash(inner)` excludes the signature.
4. Send the tampered block to the syncing node as a `BlockResponse`.
5. `maybe_receive_state_sync_blocks` matches `block_hash == sync_hash`, calls `validate_block`, which returns `Err(InvalidChunk("Invalid chunk header signature …"))`, logs the error, and then calls `save_orphan` with the tampered block.
6. The legitimate block arrives later; `save_orphan` sees the hash already present and discards it.
7. After state sync completes, `check_orphans` processes the tampered block, which is rejected. The node cannot advance its chain. [8](#0-7) [9](#0-8) [10](#0-9)

### Citations

**File:** chain/client/src/client_actor.rs (L621-633)
```rust
impl Handler<SpanWrapped<BlockResponse>> for ClientActor {
    fn handle(&mut self, msg: SpanWrapped<BlockResponse>) {
        let BlockResponse { block, peer_id, was_requested } = msg.span_unwrap();
        tracing::debug!(target: "client", block_height = block.header().height(), block_hash = ?block.header().hash(), "received block response");
        let blocks_at_height =
            self.client.chain.chain_store().get_all_block_hashes_by_height(block.header().height());
        if was_requested || blocks_at_height.is_empty() {
            // This is a very sneaky piece of logic.
            if self.maybe_receive_state_sync_blocks(Arc::clone(&block)) {
                // A node is syncing its state. Don't consider receiving
                // blocks other than the few special ones that State Sync expects.
                return;
            }
```

**File:** chain/client/src/client_actor.rs (L1907-1977)
```rust
    fn maybe_receive_state_sync_blocks(&mut self, block: Arc<Block>) -> bool {
        let SyncStatus::StateSync(StateSyncStatus { sync_hash, .. }) =
            self.client.sync_handler.sync_status
        else {
            return false;
        };

        let Ok(header) = self.client.chain.get_block_header(&sync_hash) else {
            return true;
        };

        let block: MaybeValidated<Arc<Block>> = Arc::clone(&block).into();
        let block_hash = *block.hash();

        // Notice that the blocks are saved differently:
        // * save_orphan() for the sync hash block
        // * save_block() for the prev block and all the extra blocks
        //
        // The sync hash block is saved to the orphan pool where it will
        // wait to be processed after state sync is completed.
        //
        // The other blocks do not need to be processed and are saved
        // directly to storage.

        if block_hash == sync_hash {
            // The first block of the new epoch.
            if let Err(err) = self.client.chain.validate_block(&block) {
                byzantine_assert!(false);
                tracing::error!(target: "client", ?err, ?block_hash, "received an invalid block during state sync");
            }
            tracing::debug!(target: "sync", block_hash=?block.hash(), "maybe_receive_state_sync_blocks - save sync hash block");
            self.client.chain.save_orphan(block, Provenance::NONE, false);
            return true;
        }

        if &block_hash == header.prev_hash() {
            // The last block of the previous epoch.
            if let Err(err) = self.client.chain.validate_block(&block) {
                byzantine_assert!(false);
                tracing::error!(target: "client", ?err, ?block_hash, "received an invalid block during state sync");
            }
            tracing::debug!(target: "sync", block_hash=?block.hash(), "maybe_receive_state_sync_blocks - save prev hash block");
            // Prev sync block will have its refcount increased later when processing sync block.
            if let Err(err) = self.client.chain.save_block(block) {
                tracing::error!(target: "client", ?err, ?block_hash, "failed to save a block during state sync");
            }
            return true;
        }

        let extra_block_hashes = self.client.chain.get_extra_sync_block_hashes(&header.prev_hash());
        tracing::trace!(target: "sync", ?extra_block_hashes, "maybe_receive_state_sync_blocks: extra block hashes for state sync");

        if extra_block_hashes.contains(&block_hash) {
            if let Err(err) = self.client.chain.validate_block(&block) {
                byzantine_assert!(false);
                tracing::error!(target: "client", ?err, ?block_hash, "received an invalid block during state sync");
            }
            // Extra blocks needed when there are missing chunks.
            tracing::debug!(target: "sync", block_hash=?block.hash(), "maybe_receive_state_sync_blocks - save extra block");
            if let Err(err) = self.client.chain.save_block(block) {
                tracing::error!(target: "client", ?err, ?block_hash, "failed to save a block during state sync");
            } else {
                // save_block() does not increase refcount, and for extra blocks we need to increase the refcount manually.
                let mut store_update = self.client.chain.mut_chain_store().store_update();
                store_update.inc_block_refcount(&block_hash).unwrap();
                store_update.commit().unwrap();
            }
            return true;
        }
        true
    }
```

**File:** chain/chain/src/chain.rs (L747-760)
```rust
    /// Do basic validation of a block upon receiving it. Check that block is
    /// well-formed (various roots match).
    pub fn validate_block(&self, block: &MaybeValidated<Arc<Block>>) -> Result<(), Error> {
        block
            .validate_with(|block| {
                Chain::validate_block_impl(
                    self.epoch_manager.as_ref(),
                    &self.genesis_block(),
                    block,
                )
                .map(|_| true)
            })
            .map(|_| ())
    }
```

**File:** chain/chain/src/chain.rs (L762-824)
```rust
    fn validate_block_impl(
        epoch_manager: &dyn EpochManagerAdapter,
        genesis_block: &Block,
        block: &Block,
    ) -> Result<(), Error> {
        let epoch_id = block.header().epoch_id();
        let shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;

        for (shard_index, chunk_header) in block.chunks().iter().enumerate() {
            let shard_id = shard_layout.get_shard_id(shard_index)?;
            if chunk_header.is_genesis() {
                // Special case: genesis chunks can be in non-genesis blocks and don't have a signature
                // We must verify that content matches and signature is empty.
                // TODO: this code will not work when genesis block has different number of chunks as the current block
                // https://github.com/near/nearcore/issues/4908
                let chunks = genesis_block.chunks();
                let genesis_chunk = chunks.get(shard_index);
                let genesis_chunk = genesis_chunk.ok_or_else(|| {
                    Error::InvalidChunk(format!(
                        "genesis chunk not found for shard {}, genesis block has {} chunks",
                        shard_id,
                        chunks.len(),
                    ))
                })?;

                if genesis_chunk.chunk_hash() != chunk_header.chunk_hash()
                    || genesis_chunk.signature() != chunk_header.signature()
                {
                    return Err(Error::InvalidChunk(format!(
                        "genesis chunk mismatch for shard {}. genesis chunk hash: {:?}, chunk hash: {:?}, genesis signature: {}, chunk signature: {}",
                        shard_id,
                        genesis_chunk.chunk_hash(),
                        chunk_header.chunk_hash(),
                        genesis_chunk.signature(),
                        chunk_header.signature()
                    )));
                }
            } else if chunk_header.is_new_chunk() {
                if chunk_header.shard_id() != shard_id {
                    return Err(Error::InvalidShardId(chunk_header.shard_id()));
                }
                let parent_hash = block.header().prev_hash();
                if chunk_header.prev_block_hash() != parent_hash {
                    return Err(Error::InvalidChunk(format!(
                        "chunk prev_block_hash mismatch for shard {}: chunk has {:?}, block has {:?}",
                        shard_id,
                        chunk_header.prev_block_hash(),
                        parent_hash,
                    )));
                }
                if !verify_chunk_header_signature_by_hash(epoch_manager, &chunk_header)? {
                    byzantine_assert!(false);
                    return Err(Error::InvalidChunk(format!(
                        "Invalid chunk header signature for shard {}, chunk hash: {:?}",
                        shard_id,
                        chunk_header.chunk_hash()
                    )));
                }
            }
        }
        block.check_validity().map_err(|e| <BlockValidityError as Into<Error>>::into(e))?;
        Ok(())
    }
```

**File:** chain/chain/src/chain.rs (L1592-1633)
```rust
    pub fn reset_heads_post_state_sync(
        &mut self,
        sync_hash: CryptoHash,
        block_processing_artifacts: &mut BlockProcessingArtifact,
        apply_chunks_done_sender: Option<ApplyChunksDoneSender>,
    ) -> Result<(), Error> {
        // Get header we were syncing into.
        let header = self.get_block_header(&sync_hash)?;
        let prev_hash = *header.prev_hash();
        let prev_block = self.get_block(&prev_hash)?;

        // Check which blocks were downloaded during state sync
        // and set the tail and chunk tail accordingly
        let tail_block_hash = self
            .get_extra_sync_block_hashes(&prev_hash)
            .into_iter()
            .min_by_key(|block_hash| self.get_block_header(block_hash).unwrap().height())
            .unwrap_or(prev_hash);
        let tail_block = self.get_block(&tail_block_hash)?;

        let new_tail = tail_block.header().height();
        let new_chunk_tail = tail_block.chunks().min_height_included().unwrap();
        tracing::debug!(target: "sync", ?new_tail, ?new_chunk_tail, "adjusting tail for sync blocks");

        let tip = Tip::from_header(prev_block.header());
        let final_head = Tip::from_header(self.genesis.header());
        // Update related heads now.
        let mut chain_store_update = self.mut_chain_store().store_update();
        chain_store_update.save_body_head(&tip)?;
        // Reset final head to genesis since at this point we don't have the last final block.
        chain_store_update.save_final_head(&final_head)?;
        // New Tail can not be earlier than `prev_block.header.inner_lite.height`
        chain_store_update.update_tail(new_tail);
        // New Chunk Tail can not be earlier than minimum of height_created in Block `prev_block`
        chain_store_update.update_chunk_tail(new_chunk_tail);
        chain_store_update.commit()?;

        // Check if there are any orphans unlocked by this state sync.
        // We can't fail beyond this point because the caller will not process accepted blocks
        //    and the blocks with missing chunks if this method fails
        self.check_orphans(prev_hash, block_processing_artifacts, apply_chunks_done_sender);
        Ok(())
```

**File:** chain/chain/src/orphan.rs (L285-306)
```rust
    pub fn save_orphan(
        &mut self,
        block: MaybeValidated<Arc<Block>>,
        provenance: Provenance,
        requested_missing_chunks: bool,
    ) {
        let block_hash = *block.hash();
        if !self.orphans.contains(block.hash()) {
            self.orphans.add(
                Orphan { block, provenance, added: self.clock.now() },
                requested_missing_chunks,
            );
        }

        tracing::debug!(
            target: "chain",
            ?block_hash,
            orphans_count = %self.orphans.len(),
            evicted_count = %self.orphans.len_evicted(),
            "process block: orphan"
        );
    }
```

**File:** chain/chain/src/signature_verification.rs (L31-54)
```rust
pub fn verify_chunk_header_signature_by_hash(
    epoch_manager: &dyn EpochManagerAdapter,
    chunk_header: &ShardChunkHeader,
) -> Result<bool, Error> {
    verify_chunk_header_signature_by_hash_and_parts(
        epoch_manager,
        &chunk_header.chunk_hash(),
        chunk_header.signature(),
        chunk_header.prev_block_hash(),
        chunk_header.shard_id(),
    )
}

pub fn verify_chunk_header_signature_by_hash_and_parts(
    epoch_manager: &dyn EpochManagerAdapter,
    chunk_hash: &ChunkHash,
    signature: &Signature,
    prev_block_hash: &CryptoHash,
    shard_id: ShardId,
) -> Result<bool, Error> {
    let chunk_producer =
        epoch_manager.get_chunk_producer_info_from_prev_block(prev_block_hash, shard_id)?;
    Ok(signature.verify(chunk_hash.as_ref(), chunk_producer.public_key()))
}
```
