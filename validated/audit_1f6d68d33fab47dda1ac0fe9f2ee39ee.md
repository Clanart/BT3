### Title
Wrong Epoch's Shard Layout Used to Index Prev-Block Chunks in `get_state_response_part`, Producing Incorrect State Root at Resharding Boundaries — (`File: chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`ChainStateSyncAdapter::get_state_response_part` resolves the `shard_index` used to read the previous block's chunk array using the **sync block's (new) epoch shard layout**, while the previous block belongs to the **old epoch**. When resharding changes the shard layout between the two epochs, the wrong chunk is selected, yielding a wrong `state_root`. The state part is then computed from that wrong trie root and served to the requesting node. The receiving node's `set_state_part` validates the part against the state root committed in the header (which was computed correctly by `compute_state_response_header` using the old epoch's layout), so validation always fails. State sync for any shard whose index differs across the resharding boundary becomes permanently broken on the serving side.

---

### Finding Description

`get_state_response_part` (lines 277–358) resolves the shard layout and shard index as follows:

```rust
let epoch_id = block.header().epoch_id();          // sync block = NEW epoch
// ...
let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;  // NEW epoch layout
// ...
let prev_block = self.chain_store.get_block(header.prev_hash())?;   // OLD epoch block
let shard_index = shard_layout.get_shard_index(shard_id)?;          // index in NEW layout
let state_root = prev_block
    .chunks()
    .get(shard_index)                                                // OLD block indexed by NEW layout
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
```

The sibling function `compute_state_response_header` (lines 63–251) performs the same lookup correctly:

```rust
let prev_epoch_id = sync_prev_block.header().epoch_id();            // OLD epoch
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?; // OLD layout
let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;          // index in OLD layout
let chunk_header = chunks.get(prev_shard_index)...;                            // correct chunk
```

The two functions are supposed to be consistent: `get_state_response_part` serves the trie data whose root is committed in the header produced by `compute_state_response_header`. Because `get_state_response_part` uses the wrong layout to derive `shard_index`, the `state_root` it extracts from `prev_block.chunks()` diverges from the root in the header whenever the shard layout changes across the epoch boundary.

**Concrete failure modes at a resharding boundary (old layout has N shards, new layout has N+1):**

| Requested `shard_id` | New-layout index | Old-block chunk at that index | Result |
|---|---|---|---|
| New child shard (e.g. `X_right`) | N (out of range for old block) | `None` | `Error::InvalidShardId` — state sync permanently fails |
| Existing shard whose index shifted (e.g. shard `3` moved from index 3 to index 4) | 4 (out of range) | `None` | Same error |
| Existing shard whose index shifted into a valid old slot | Returns wrong shard's root | Wrong trie traversed | Wrong state part served; `set_state_part` validation fails |

The wrong state part is also written to the `DBCol::StateParts` cache (lines 349–354), so subsequent requests for the same `(sync_hash, shard_id, part_id)` key return the same wrong data without recomputing.

The entry point is `StateRequestActor::handle` for `StateRequestPart` messages (lines 260–293 of `chain/client/src/state_request_actor.rs`), which is reachable from any peer over the P2P network via `PeerMessage::StateRequestPart(shard_id, sync_hash, part_id)` (line 1191 of `chain/network/src/peer_manager/network_state/mod.rs`). The `shard_id`, `sync_hash`, and `part_id` fields are fully attacker-controlled; the only gate is `validate_sync_hash`, which checks that `sync_hash` belongs to a known recent epoch — a condition that is trivially satisfied by any legitimate sync hash at the resharding boundary.

---

### Impact Explanation

**Broken reconstruction invariant:** The state part served by `get_state_response_part` is computed from a `state_root` that does not match the root committed in the `ShardStateSyncResponseHeader`. `set_state_part` (lines 534–560 of `adapter.rs`) validates every received part against the header's root via `validate_state_part`; this check always fails for the mismatched part. The syncing node retries indefinitely; the serving node keeps returning the same wrong (or error) response. State sync for the affected shard is permanently broken.

**Scope:** Every node that needs to state-sync after a resharding epoch boundary is affected. This includes:
- New nodes joining the network after resharding.
- Existing nodes that fell behind and need to catch up past the resharding boundary.

Without successful state sync, these nodes cannot participate in consensus or serve data for the new epoch's shards.

**Severity: High** — deterministic, permanent state-sync failure for all shards whose layout index changes across a resharding boundary; no workaround short of a code fix.

---

### Likelihood Explanation

The condition is: resharding has occurred (shard layout version changed between the previous epoch and the sync block's epoch). This is a planned, protocol-level event. Once it occurs, every `StateRequestPart` for an affected shard at that boundary triggers the bug. No special attacker capability is required; ordinary state-sync traffic from any peer is sufficient.

---

### Recommendation

In `get_state_response_part`, derive `shard_index` from the **previous block's epoch layout**, mirroring `compute_state_response_header`:

```rust
let prev_block = self.chain_store.get_block(header.prev_hash())?;
let prev_epoch_id = prev_block.header().epoch_id();                          // OLD epoch
let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?; // OLD layout
// Validate shard_id against the new epoch (already done above), but index with old layout:
let shard_index = prev_shard_layout.get_shard_index(shard_id)
    .map_err(|_| Error::InvalidShardId(shard_id))?;
let state_root = prev_block
    .chunks()
    .get(shard_index)
    .ok_or(Error::InvalidShardId(shard_id))?
    .prev_state_root();
```

Add a test that exercises `get_state_response_part` at a resharding epoch boundary and asserts that the returned state part passes `validate_state_part` against the root in the corresponding header.

---

### Proof of Concept

1. Configure a two-epoch test with resharding: epoch 0 uses `ShardLayout::V1` (1 shard), epoch 1 uses `ShardLayout::V2` (4 shards). The sync block is the first block of epoch 1.

2. Call `compute_state_response_header(shard_id=1, sync_hash)` → succeeds, returns a header whose `chunk_prev_state_root()` is the root of shard 1's initial state.

3. Call `get_state_response_part(shard_id=1, part_id=0, sync_hash)`:
   - `epoch_id` = epoch 1's ID.
   - `shard_layout` = V2 layout (4 shards).
   - `shard_index = shard_layout.get_shard_index(1)` = 1.
   - `prev_block.chunks().get(1)` → `None` (epoch 0 block has only 1 chunk, index 0).
   - Returns `Error::InvalidShardId(1)`.

4. The syncing node receives an empty response, retries, and loops forever. State sync for shard 1 never completes.

**Key code locations:**

- Wrong layout usage: [1](#0-0) 
- Correct pattern in sibling function: [2](#0-1) 
- Wrong state part cached: [3](#0-2) 
- P2P entry point (attacker-controlled `shard_id`): [4](#0-3) 
- `set_state_part` validation that rejects the wrong part: [5](#0-4)

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L93-96)
```rust
        let shard_layout = self.epoch_manager.get_shard_layout(sync_block_epoch_id)?;
        let prev_epoch_id = sync_prev_block.header().epoch_id();
        let prev_shard_layout = self.epoch_manager.get_shard_layout(&prev_epoch_id)?;
        let prev_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
```

**File:** chain/chain/src/state_sync/adapter.rs (L305-316)
```rust
        let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
        let shard_ids = self.epoch_manager.shard_ids(epoch_id)?;
        if !shard_ids.contains(&shard_id) {
            return Err(shard_id_out_of_bounds(shard_id));
        }
        let prev_block = self.chain_store.get_block(header.prev_hash())?;
        let shard_index = shard_layout.get_shard_index(shard_id)?;
        let state_root = prev_block
            .chunks()
            .get(shard_index)
            .ok_or(Error::InvalidShardId(shard_id))?
            .prev_state_root();
```

**File:** chain/chain/src/state_sync/adapter.rs (L349-355)
```rust
        let header_key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        if self.chain_store.store_ref().exists(DBCol::StateHeaders, &header_key) {
            let mut store_update = self.chain_store.store().store_update();
            let bytes = state_part.to_bytes();
            store_update.set(DBCol::StateParts, &key, &bytes);
            store_update.commit();
        }
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

**File:** chain/network/src/peer_manager/network_state/mod.rs (L1191-1196)
```rust
            PeerMessage::StateRequestPart(shard_id, sync_hash, part_id) => {
                let response = self
                    .state_request_adapter
                    .send_async(StateRequestPart { shard_id, sync_hash, part_id })
                    .await;
                response.ok().flatten().map(|r| PeerMessage::VersionedStateResponse(*r.0))
```
