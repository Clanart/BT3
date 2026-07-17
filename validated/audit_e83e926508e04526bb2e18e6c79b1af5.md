### Title
`set_state_header` Never Validates That the Embedded Chunk Belongs to the Caller-Supplied `shard_id`, Allowing a Malicious Peer to Corrupt State-Sync Finalization — (`File: chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`ChainStateSyncAdapter::set_state_header` accepts a peer-supplied `ShardStateSyncResponseHeader` and a caller-supplied `shard_id`. It validates the chunk's internal proofs and verifies the chunk is included in the block, but **never checks that `chunk.shard_id() == shard_id`**. A malicious peer can serve a header whose embedded chunk belongs to shard Y while the syncing node believes it is syncing shard X. All existing checks pass, the header is stored under `StateHeaderKey(X, sync_hash)` with shard Y's chunk and state root, and `set_state_finalize` subsequently applies shard Y's chunk to shard X's trie — permanently corrupting shard X's state.

---

### Finding Description

`set_state_header` performs the following checks:

1. `validate_chunk_proofs(&chunk, ...)` — validates the chunk's internal Reed-Solomon / merkle proofs. Does **not** inspect `chunk.shard_id()`.
2. `verify_path(sync_prev_block_header.chunk_headers_root(), chunk_proof, ChunkHashHeight(chunk_hash, height_included))` — proves the chunk is in the block's chunk-headers merkle tree. A block contains one chunk per shard; this check proves membership but **not which shard slot** the chunk occupies.
3. Receipt-proof loop (step 4e): `receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts))` — uses the caller-supplied `shard_id`. An attacker who provides receipts that are genuinely destined for shard X (valid on-chain data) will produce a hash that verifies correctly against the block's `prev_chunk_outgoing_receipts_root`, even though the chunk itself is for shard Y.
4. `validate_state_root_node(state_root_node, chunk_inner.prev_state_root())` — validates the state root node against the chunk's own `prev_state_root`. Passes for shard Y's data.

None of these checks compare `chunk.shard_id()` to the function's `shard_id` parameter.

After all checks pass, the header is committed:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
``` [1](#0-0) 

The key uses the caller-supplied `shard_id` (X), but the value contains shard Y's chunk and state root. Downstream consumers (`set_state_part`, `set_state_finalize`) retrieve the header by the same key and trust its contents:

```rust
pub fn set_state_part(&self, shard_id: ShardId, ...) -> Result<(), Error> {
    let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
    let chunk = shard_state_header.take_chunk();
    let state_root = *chunk.take_header().take_inner().prev_state_root(); // shard Y's root
    ...
    self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part)
``` [2](#0-1) 

And in `set_state_finalize`:

```rust
let shard_uid = shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
let apply_result = self.runtime_adapter.apply_chunk(
    RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L525-529)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
```

**File:** chain/chain/src/state_sync/adapter.rs (L534-560)
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
```
