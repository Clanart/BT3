Let me look at the `verify_path` function and how the chunk shard_id is checked in the main chunk validation to complete the analysis.

Based on my thorough analysis of the code, here is my determination:

---

### Title
Missing `prev_chunk_header.shard_id()` == `shard_id` Guard in `set_state_header` Allows Receipt-Chain Truncation via Cross-Shard Header Substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`ChainStateSyncAdapter::set_state_header` validates that `prev_chunk_header` is included in the previous block's merkle tree, but never asserts that `prev_chunk_header.shard_id() == shard_id`. Because `verify_path` is position-agnostic (it only checks that the hash reconstructs the root, not which leaf index it occupies), an attacker-controlled peer can substitute the real shard-X `prev_chunk_header` with the real shard-Y `prev_chunk_header` plus its genuine merkle proof, and the check passes. The substituted header's `height_included` then becomes the sole bound for the incoming-receipt proof chain (check 4g, line 507), allowing the attacker to truncate or extend that chain relative to the correct bound, causing `set_state_finalize` to apply the wrong set of incoming receipts and install a corrupted state root.

### Finding Description

**Production entrypoint**: Any syncing node calls `StateSyncDownloader::ensure_shard_header` → `StateHeaderValidationRequest` → `ClientActor` handler → `ChainStateSyncAdapter::set_state_header`. [1](#0-0) [2](#0-1) 

The header is downloaded from an arbitrary peer via `StateSyncDownloadSourcePeer` — no authentication or trust check on the provider. [3](#0-2) 

**The missing guard**: In `set_state_header`, the `prev_chunk_header` validation block (lines 412–426) only calls `verify_path` against `prev_block_header.chunk_headers_root()`: [4](#0-3) 

`verify_path` is purely hash-based — it checks `compute_root_from_path(path, hash) == root` with no position/index constraint: [5](#0-4) 

A block's `chunk_headers_root` is a merkle tree over **all** shard chunk headers. A valid proof for shard-Y's chunk header at position `j` will pass `verify_path` against the same root, even when the caller expects shard-X's header at position `i`. There is no `prev_chunk_header.shard_id() == shard_id` assertion anywhere in the function.

**Receipt-chain bound corruption**: After the merkle check, `prev_chunk_header.height_included()` is used as the exclusive lower bound for the incoming-receipt proof chain: [6](#0-5) 

If shard-Y's `height_included` (`H_Y`) is greater than shard-X's correct `height_included` (`H_X`), the attacker provides a receipt-proof chain that covers only blocks `[sync_hash … H_Y]`, omitting blocks `[H_X … H_Y − 1]`. Check 4g passes because `header.height() == H_Y == prev_chunk_header.height_included()`. The header is then persisted: [7](#0-6) 

**Finalization with wrong receipts**: `set_state_finalize` reads the stored header and applies its `incoming_receipts_proofs` without re-validating the receipt chain bound: [8](#0-7) 

The receipts from the omitted blocks are never applied, producing a state root that diverges from the canonical chain.

### Impact Explanation

The syncing node installs a state root for shard X that is missing incoming receipts from blocks `[H_X … H_Y − 1]`. Every subsequent block application for that shard will produce a mismatched state root, permanently breaking the node's ability to validate or produce chunks for shard X. The wrong committed state is durably written to `DBCol::StateHeaders` and then applied via `apply_chunk`.

### Likelihood Explanation

Any peer reachable by the syncing node can serve a crafted `ShardStateSyncResponseHeader`. All required inputs (real chunk headers, real merkle proofs, real receipt proofs for the non-omitted blocks) are publicly available on-chain. The attack is constructible without any validator or privileged key. It is most effective when shards have different `height_included` values (common when chunks are occasionally missing), which is a normal network condition.

### Recommendation

Add an explicit shard-id equality check immediately after extracting `prev_chunk_header`, before the merkle proof verification:

```rust
if let Some(ref phdr) = prev_chunk_header {
    if phdr.shard_id() != shard_id {
        return Err(Error::Other(
            "set_shard_state failed: prev_chunk_header shard_id mismatch".into(),
        ));
    }
}
```

Similarly, add `chunk.shard_id() == shard_id` after `validate_chunk_proofs`, since `verify_path` for the main chunk also lacks a position check. [9](#0-8) 

### Proof of Concept

A cargo integration test can:
1. Build a valid `ShardStateSyncResponseHeader` for shard 0 using the existing test helpers (as in `integration-tests/src/tests/client/process_blocks.rs`). [10](#0-9) 
2. Replace `prev_chunk_header` with the real chunk header for shard 1 (different `height_included`) and replace `prev_chunk_proof` with shard 1's real merkle proof from the same block.
3. Truncate `incoming_receipts_proofs` to cover only blocks down to shard 1's `height_included`.
4. Call `set_state_header(shard_id=0, …, crafted_header)` and assert it returns `Ok(())` — demonstrating the missing guard.
5. Call `set_state_finalize` and assert the resulting state root differs from the canonical one.

### Citations

**File:** chain/client/src/client_actor.rs (L2133-2147)
```rust
impl Handler<SpanWrapped<StateHeaderValidationRequest>, Result<(), near_chain::Error>>
    for ClientActor
{
    fn handle(
        &mut self,
        msg: SpanWrapped<StateHeaderValidationRequest>,
    ) -> Result<(), near_chain::Error> {
        let msg = msg.span_unwrap();
        self.client.chain.state_sync_adapter.set_state_header(
            msg.shard_id,
            msg.sync_hash,
            msg.header,
        )
    }
}
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

**File:** chain/client/src/sync/state/network.rs (L31-46)
```rust
pub(super) struct StateSyncDownloadSourcePeer {
    pub clock: Clock,
    pub store: Store,
    pub request_sender: AsyncSender<PeerManagerMessageRequest, PeerManagerMessageResponse>,
    pub request_timeout: Duration,
    pub state: Arc<Mutex<StateSyncDownloadSourcePeerSharedState>>,
}

#[derive(Default)]
pub(super) struct StateSyncDownloadSourcePeerSharedState {
    /// Tracks pending requests we have sent to peers. The requests are indexed by
    /// (shard ID, sync hash, part ID or header), and the value is the peer ID we
    /// expect the response from, as well as a channel sender to complete the future
    /// waiting for the response.
    pending_requests: HashMap<PendingPeerRequestKey, PendingPeerRequestValue>,
}
```

**File:** chain/chain/src/state_sync/adapter.rs (L379-403)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L412-426)
```rust
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L505-510)
```rust
        // 4g. Checking that there are no more heights to get incoming_receipts
        let header = self.chain_store.get_block_header(&hash_to_compare)?;
        if header.height() != prev_chunk_header.map_or(0, |h| h.height_included()) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid incoming receipts".into()));
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

**File:** core/primitives/src/merkle.rs (L113-119)
```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

**File:** chain/chain/src/chain_update.rs (L479-487)
```rust
        let mut receipt_proof_responses: Vec<ReceiptProofResponse> = vec![];
        for incoming_receipt_proof in &incoming_receipts_proofs {
            let ReceiptProofResponse(hash, _) = incoming_receipt_proof;
            let block_header = self.chain_store_update.get_block_header(hash)?;
            if block_header.height() <= chunk.height_included() {
                receipt_proof_responses.push(incoming_receipt_proof.clone());
            }
        }
        let receipts = collect_receipts_from_response(&receipt_proof_responses);
```

**File:** integration-tests/src/tests/client/process_blocks.rs (L1975-1995)
```rust
    let state_sync_header = env.clients[0]
        .chain
        .state_sync_adapter
        .get_state_response_header(shard_id, sync_hash)
        .unwrap();
    let num_parts = state_sync_header.num_state_parts();
    let state_sync_parts = (0..num_parts)
        .map(|i| {
            env.clients[0]
                .chain
                .state_sync_adapter
                .get_state_response_part(shard_id, i, sync_hash)
                .unwrap()
        })
        .collect::<Vec<_>>();

    env.clients[1]
        .chain
        .state_sync_adapter
        .set_state_header(shard_id, sync_hash, state_sync_header.clone())
        .unwrap();
```
