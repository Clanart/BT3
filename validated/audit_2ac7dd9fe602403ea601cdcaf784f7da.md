### Title
Missing `shard_id` Binding Check in `set_state_header` Allows Wrong-Shard State Header Acceptance — (`File: chain/chain/src/state_sync/adapter.rs`)

### Summary

`ChainStateSyncAdapter::set_state_header` accepts a peer-supplied `ShardStateSyncResponseHeader` and stores it under the key `StateHeaderKey(shard_id, sync_hash)`, but **never verifies that the chunk embedded in the header actually belongs to the requested `shard_id`**. A malicious snapshot-host peer can supply a structurally valid header for shard X when shard Y was requested. The header passes all existing checks, is committed to the DB under shard Y's key, and the wrong state root is subsequently used to validate and apply state parts — corrupting the syncing node's state for shard Y.

### Finding Description

The analog to M-12 is exact: in M-12 the ERC-721 `name`/`symbol` are written into the implementation's storage slot at constructor time and are never propagated into the proxy's storage context, so every proxy has a void binding. In nearcore, the `shard_id` binding is written into the DB key (`StateHeaderKey(shard_id, sync_hash)`) at storage time, but the chunk embedded in the header — which carries the actual `shard_id` — is never verified to match the requested `shard_id`. The binding is broken at the commitment layer.

**Root cause — `set_state_header`:**

```
pub fn set_state_header(
    &self,
    shard_id: ShardId,          // ← requested shard
    sync_hash: CryptoHash,
    shard_state_header: ShardStateSyncResponseHeader,
) -> Result<(), Error> {
    ...
    let chunk = shard_state_header.cloned_chunk();
    // ❌ chunk.shard_id() is NEVER compared to shard_id
    ...
    // Stored under the REQUESTED shard_id, not the chunk's actual shard_id
    let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
    store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
``` [1](#0-0) 

The five existing checks are:
1. `validate_chunk_proofs` — validates the chunk's internal encoding proofs, not its `shard_id`.
2. `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(...))` — proves the chunk hash is *somewhere* in the block's Merkle tree of chunk headers, but **does not verify the position (shard index)** in the tree.
3. `verify_path` for `prev_chunk_header` — same position-agnostic Merkle check.
4. Receipt proof loop — hashes receipts as `ReceiptList(shard_id, receipts)` using the *requested* shard_id; this would catch a mismatch **only when `incoming_receipts_proofs` is non-empty**.
5. Height check (4g) — only executes after the receipt loop; if the loop is empty it checks `sync_hash.height() == prev_chunk_header.height_included()`, which is a weak guard. [2](#0-1) [3](#0-2) 

**Downstream propagation — `set_state_part`:**

```rust
pub fn set_state_part(...) -> Result<(), Error> {
    let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
    let chunk = shard_state_header.take_chunk();
    let state_root = *chunk.take_header().take_inner().prev_state_root(); // ← shard X's root
    if matches!(
        self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
        ...
    )
``` [4](#0-3) 

`validate_state_part_impl` does not use `shard_id` in the trie validation — it only checks the part against the provided `state_root`:

<cite repo="blackvul/nearcore--006" path="chain/chain/src/runtime/mod.rs"

### Citations

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

**File:** chain/chain/src/state_sync/adapter.rs (L486-503)
```rust
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L534-561)
```rust
    pub fn set_state_part(
        &self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
        part_id: PartId,
        part: &StatePart,
    ) -> Result<(), Error> {
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
        // Saving the part data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StatePartKey(sync_hash, shard_id, part_id.idx)).unwrap();
        let bytes = part.to_bytes();
        store_update.set(DBCol::StateParts, &key, &bytes);
        store_update.commit();
        Ok(())
    }
```
