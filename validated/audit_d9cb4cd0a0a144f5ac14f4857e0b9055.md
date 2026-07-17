### Title
Missing Shard-ID Cross-Check in `set_state_header` Allows Cross-Shard Header Injection - (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` accepts a `ShardStateSyncResponseHeader` for a requested `shard_id` but never verifies that the embedded chunk's own `shard_id()` matches the requested parameter. An unprivileged state-sync peer can supply shard A's chunk (with a valid merkle proof) while the request is for shard B, pass all five validation steps, and cause `StateHeaderKey(B, sync_hash)` to be stored with shard A's chunk and state root. Subsequent `set_state_part` calls for shard B then validate parts against shard A's state root and write them under shard B's `ShardUId`, corrupting shard B's state during sync.

---

### Finding Description

The entrypoint is `set_state_header` in `chain/chain/src/state_sync/adapter.rs`. [1](#0-0) 

The function extracts the chunk from the attacker-supplied header and runs five checks, none of which bind the chunk's shard identity to the requested `shard_id`:

**Check 1-2 (`validate_chunk_proofs`)** — verifies the chunk's internal hash, tx root, and receipts root. It does not inspect `chunk.shard_id()`. [2](#0-1) 

**Check 3a (merkle inclusion)** — verifies the chunk hash is somewhere in `sync_prev_block_header.chunk_headers_root()`. The leaf is `ChunkHashHeight(chunk_hash, height_included)`, which contains no shard identity. Shard A's chunk with shard A's merkle path passes this check even when `shard_id = B`. [3](#0-2) 

**Check 3b (prev chunk inclusion)** — same structure; verifies shard A's prev chunk is in the preceding block. No shard_id binding. [4](#0-3) 

**Check 4 (receipt proofs)** — hashes receipts as `ReceiptList(shard_id, receipts)` using the *requested* shard_id (B), then verifies against on-chain outgoing-receipts roots. An attacker supplies shard B's actual on-chain receipt proofs here; they are valid for shard B and pass independently of which chunk is embedded. [5](#0-4) 

**Check 5 (state root node)** — validates `state_root_node` against `chunk_inner.prev_state_root()`, where `chunk_inner` comes from shard A's chunk. Shard A's `state_root_node` trivially validates against shard A's `prev_state_root`. [6](#0-5) 

After all checks pass, the header is stored under the *requested* shard B's key: [7](#0-6) 

The missing guard is simply: `if chunk.shard_id() != shard_id { return Err(...) }`.

**Receipt-proof range constraint.** Check 4g requires the receipt-proof chain to walk back exactly to `prev_chunk_header.height_included()` (shard A's prev-chunk height, H_A_prev). [8](#0-7) 

In the common NEAR configuration where all shards produce a chunk at every block height, H_A_prev == H_B_prev, so shard B's actual receipt proofs cover exactly the right range. The attack is unconditional in that configuration.

---

### Impact Explanation

Once `StateHeaderKey(B, sync_hash)` holds shard A's header, `set_state_part` for shard B reads shard A's `prev_state_root` and validates incoming parts against it: [9](#0-8) 

An attacker who also supplies shard A's state parts (which validate against shard A's state root) causes them to be stored under shard B's `StatePartKey`. When the syncing node finalises state sync for shard B it installs shard A's trie data into shard B's `ShardUId`, producing a permanently corrupted shard B state on that node.

---

### Likelihood Explanation

All data required for the attack (shard A's chunk body, merkle paths, shard B's receipt proofs, shard A's state root node) is publicly available from any full node. No validator, chunk-producer, or operator privilege is required. The attack is executable by any peer that can respond to a state-sync header request, which is an unprivileged network role.

---

### Recommendation

Add an explicit shard-id cross-check immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_state_header failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
``` [10](#0-9) 

---

### Proof of Concept

Integration test strategy (production code path, no mocks):

1. Spin up a two-shard test environment with `TestEnv`.
2. Advance to a sync point; obtain `sync_hash`.
3. Call `get_state_response_header(shard_id=0, sync_hash)` on client 0 to get shard 0's header.
4. Call `get_state_response_header(shard_id=1, sync_hash)` on client 0 to get shard 1's receipt proofs; construct a cross-shard header: shard 0's chunk + shard 0's chunk proof + shard 0's prev chunk + shard 1's receipt proofs + shard 0's state root node.
5. Call `set_state_header(shard_id=1, sync_hash, cross_shard_header)` on client 1 — assert it returns `Ok(())`.
6. Read back the stored header via `get_state_header(shard_id=1, sync_hash)` and assert `stored_header.cloned_chunk().shard_id() == 0` (not 1), confirming the invariant violation. [11](#0-10)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L368-531)
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

**File:** chain/chain/src/validate.rs (L22-66)
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
```
