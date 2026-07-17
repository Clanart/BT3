Now I have a complete picture of the code. Let me analyze the full validation chain in `set_state_header`.

### Title
Missing `chunk.shard_id()` == `shard_id` Guard in `set_state_header` Allows Cross-Shard State Root Substitution — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` accepts a `ShardStateSyncResponseHeader` from an untrusted peer and stores it under `StateHeaderKey(shard_id, sync_hash)` without ever asserting that `header.chunk.shard_id() == shard_id`. A malicious state-sync peer can supply shard_B's legitimately-signed chunk (with its valid Merkle proof against `sync_prev_block.chunk_headers_root`) in response to a request for shard_A, pass every existing validation check, and cause the node to persist shard_B's `prev_state_root` as the authoritative state root for shard_A. Subsequent `set_state_part` and `apply_state_part` calls then install shard_B's trie nodes under shard_A's `ShardUId`, permanently corrupting the synced node's state for shard_A.

---

### Finding Description

**Production entrypoint**: `StateSyncDownloader::ensure_shard_header` downloads a header from a peer and sends a `StateHeaderValidationRequest` to `ClientActor`, which calls `set_state_header(msg.shard_id, msg.sync_hash, msg.header)`. [1](#0-0) [2](#0-1) 

**The five validation steps in `set_state_header` and why none catches the shard mismatch**:

**Step 1 — `validate_chunk_proofs`** (`chain/chain/src/validate.rs:22-67`): Checks the chunk's internal hash, `tx_root`, and `outgoing_receipts_root`. It takes only `chunk` and `epoch_manager` — it has no `shard_id` parameter and performs no check that `chunk.shard_id()` equals any expected value. [3](#0-2) 

**Step 2 — `verify_path` for chunk inclusion** (`adapter.rs:394-403`): Verifies that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is a leaf in `sync_prev_block_header.chunk_headers_root()`. The Merkle path encodes the leaf's position in the tree, but the code never checks that this position corresponds to `shard_id`. Shard_B's chunk with its own valid Merkle proof verifies correctly against the same root, because the root covers all shards. [4](#0-3) 

**Step 3 — Receipt proof validation** (`adapter.rs:438-510`): The `receipts_hash` is computed as `CryptoHash::hash_borsh(ReceiptList(shard_id, receipts))` using the *argument* `shard_id` (shard_A), not `chunk.shard_id()`. The attacker provides shard_A's real on-chain incoming receipt proofs (public data), which satisfy this check exactly. [5](#0-4) 

**Step 4 — `state_root_node` validation** (`adapter.rs:512-523`): Validates `state_root_node` against `chunk_inner.prev_state_root()` — which is shard_B's `prev_state_root`. The attacker provides shard_B's `state_root_node`, which is trivially valid for shard_B's own root. [6](#0-5) 

**Step 5 — Storage** (`adapter.rs:525-529`): The header is stored under `StateHeaderKey(shard_id, sync_hash)` — using the caller-supplied `shard_id` (shard_A) — with shard_B's chunk and `prev_state_root` embedded inside. [7](#0-6) 

**The missing guard**: There is no check of the form `chunk.shard_id() == shard_id` anywhere in `set_state_header`.

---

### Impact Explanation

After the poisoned header is stored:

- `set_state_part` reads `StateHeaderKey(shard_id_A, sync_hash)`, extracts `state_root = chunk.prev_state_root()` (shard_B's root), and validates incoming parts against it. Parts for shard_B's trie pass; parts for shard_A's trie fail. The attacker also controls the part supply, so they serve shard_B's parts. [8](#0-7) 

- `set_state_finalize` → `apply_state_part` installs shard_B's trie nodes under shard_A's `ShardUId`, committing shard_B's `prev_state_root` as shard_A's state root. [9](#0-8) 

The synced node operates with a permanently wrong state for shard_A: account balances, contract storage, and nonces for shard_A accounts reflect shard_B's data. The node will produce or accept incorrect execution results for shard_A indefinitely.

---

### Likelihood Explanation

Any network peer can advertise itself as a state-sync snapshot host. The `StateSyncDownloadSourcePeer` selects from `snapshot_hosts` — peers that have self-advertised availability — with no privilege requirement. All data needed to craft the attack (shard_B's chunk, its Merkle proof, shard_A's receipt proofs, shard_B's state root node) is public on-chain data observable by any participant. [10](#0-9) 

---

### Recommendation

Add an explicit shard identity check immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
// NEW: reject headers whose embedded chunk belongs to a different shard
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Apply the same guard to `prev_chunk_header.shard_id()` if present. [11](#0-10) 

---

### Proof of Concept

In a two-shard test-loop environment:

1. Produce enough blocks to reach a valid `sync_hash`.
2. On the "honest" node, call `get_state_response_header(shard_id_B, sync_hash)` to obtain shard_B's legitimate header (chunk, chunk_proof, prev_chunk_header, prev_chunk_proof, state_root_node).
3. On the same honest node, call `get_state_response_header(shard_id_A, sync_hash)` to obtain shard_A's real `incoming_receipts_proofs` and `root_proofs`.
4. Construct a crafted `ShardStateSyncResponseHeaderV2` combining shard_B's chunk/proofs/state_root_node with shard_A's receipt proofs.
5. On the syncing node, call `set_state_header(shard_id_A, sync_hash, crafted_header)` — this returns `Ok(())`.
6. Assert that `get_state_header(shard_id_A, sync_hash).chunk_prev_state_root()` equals shard_B's `prev_state_root`, not shard_A's. [12](#0-11) [13](#0-12)

### Citations

**File:** chain/client/src/client_actor.rs (L2133-2146)
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
```

**File:** chain/client/src/sync/state/downloader.rs (L65-89)
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
```

**File:** chain/chain/src/validate.rs (L22-67)
```rust
pub fn validate_chunk_proofs(
    chunk: &ShardChunk,
    epoch_manager: &dyn EpochManagerAdapter,
) -> Result<bool, Error> {
    let correct_chunk_hash = chunk.compute_header_hash();

    // 1. Checking chunk.header.hash
    let header_hash = chunk.header_hash();
    if header_hash != &correct_chunk_hash {
        byzantine_assert!(false);
        return Ok(false);
    }

    // 2. Checking that chunk body is valid
    // 2a. Checking chunk hash
    if chunk.chunk_hash() != &correct_chunk_hash {
        byzantine_assert!(false);
        return Ok(false);
    }
    let height_created = chunk.height_created();
    let outgoing_receipts_root = chunk.prev_outgoing_receipts_root();
    let (transactions, receipts) = (chunk.to_transactions(), chunk.prev_outgoing_receipts());

    // 2b. Checking that chunk transactions are valid
    let (tx_root, _) = merklize(transactions);
    if &tx_root != chunk.tx_root() {
        byzantine_assert!(false);
        return Ok(false);
    }
    // 2c. Checking that chunk receipts are valid
    if height_created == 0 {
        return Ok(receipts.is_empty() && outgoing_receipts_root == &CryptoHash::default());
    } else {
        let shard_layout = {
            let prev_block_hash = chunk.prev_block_hash();
            epoch_manager.get_shard_layout_from_prev_block(&prev_block_hash)?
        };
        let outgoing_receipts_hashes = Chain::build_receipts_hashes(receipts, &shard_layout)?;
        let (receipts_root, _) = merklize(&outgoing_receipts_hashes);
        if &receipts_root != outgoing_receipts_root {
            byzantine_assert!(false);
            return Ok(false);
        }
    }
    Ok(true)
}
```

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

**File:** chain/chain/src/chain.rs (L2699-2731)
```rust
    pub fn set_state_finalize(
        &mut self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
    ) -> Result<(), Error> {
        let shard_state_header = self.state_sync_adapter.get_state_header(shard_id, sync_hash)?;
        let mut height = shard_state_header.chunk_height_included();
        let mut chain_update = self.chain_update();
        let shard_uid = chain_update.set_state_finalize(shard_id, sync_hash, shard_state_header)?;
        chain_update.commit()?;

        // We restored the state on height `shard_state_header.chunk.header.height_included`.
        // Now we should build a chain up to height of `sync_hash` block.
        loop {
            height += 1;
            let mut chain_update = self.chain_update();
            // Result of successful execution of set_state_finalize_on_height is bool,
            // should we commit and continue or stop.
            if chain_update.set_state_finalize_on_height(height, shard_id, sync_hash)? {
                chain_update.commit()?;
            } else {
                break;
            }
        }

        let flat_storage_manager = self.runtime_adapter.get_flat_storage_manager();
        if let Some(flat_storage) = flat_storage_manager.get_flat_storage_for_shard(shard_uid) {
            let header = self.get_block_header(&sync_hash)?;
            flat_storage.update_flat_head(header.prev_hash()).unwrap();
        }

        Ok(())
    }
```

**File:** chain/client/src/sync/state/network.rs (L271-303)
```rust
impl StateSyncDownloadSource for StateSyncDownloadSourcePeer {
    fn download_shard_header(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        handle: Arc<TaskHandle>,
        cancel: CancellationToken,
    ) -> BoxFuture<'static, Result<ShardStateSyncResponseHeader, near_chain::Error>> {
        let key = PendingPeerRequestKey {
            shard_id,
            sync_hash,
            part_id_or_header: PartIdOrHeader::Header,
        };
        let fut = Self::try_download(
            self.clock.clone(),
            self.request_sender.clone(),
            key,
            self.store.clone(),
            self.state.clone(),
            cancel,
            self.request_timeout,
            handle,
        );
        fut.map(|response| {
            response.and_then(|response| {
                response
                    .take_header()
                    .ok_or_else(|| near_chain::Error::Other("Expected header".to_owned()))
            })
        })
        .instrument(tracing::debug_span!("StateSyncDownloadSourcePeer::download_shard_header"))
        .boxed()
    }
```

**File:** core/primitives/src/state_sync.rs (L92-122)
```rust
pub struct ShardStateSyncResponseHeaderV2 {
    /// The chunk whose header in included as B_prev.chunks[shard_id]
    /// This chunk will be applied after downloading state
    pub chunk: ShardChunk,
    /// A merkle path for (Self::chunk.hash, Self::chunk.height_included), verifiable
    /// against B_prev.chunk_headers_root
    pub chunk_proof: MerklePath,
    /// This is None if sync_hash is the genesis hash. Otherwise, it's B_prev_chunk.chunks[shard_id]
    pub prev_chunk_header: Option<ShardChunkHeader>,
    /// A merkle path for (Self::prev_chunk_header.hash, Self::prev_chunk_header.height_included), verifiable
    /// against B_prev_chunk.chunk_headers_root
    pub prev_chunk_proof: Option<MerklePath>,
    /// This field contains the incoming receipts for shard_id for B_sync and B_prev_chunk.
    /// So, this field has at most two elements.
    /// These receipts are used to apply `chunk` after downloading state
    pub incoming_receipts_proofs: Vec<ReceiptProofResponse>,
    /// This field contains the info necessary to verify that the receipt proofs in Self::incoming_receipts_proofs
    /// are actually the ones referenced on chain
    ///
    /// The length of this field is the same as the length of Self::incoming_receipts_proofs, and elements
    /// of the two at a given index are taken together for verification. For a given index i,
    /// root_proofs[i] is a vector of the same length as incoming_receipts_proofs[i].1 , which itself is a
    /// vector of receipt proofs for all "from_shard_ids" that sent receipts to shard_id. root_proofs[i][j]
    /// contains a merkle root equal to the prev_outgoing_receipts_root field of the corresponding chunk
    /// included in the block with hash incoming_receipts_proofs[i].0, and a merkle path to verify it against
    /// that block's prev_chunk_outgoing_receipts_root field.
    pub root_proofs: Vec<Vec<RootProof>>,
    /// The state root with hash equal to B_prev.chunks[shard_id].prev_state_root.
    /// That is, the state root node of the trie before applying the chunks in B_prev
    pub state_root_node: StateRootNode,
}
```
