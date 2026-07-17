### Title
Missing shard-id binding in `set_state_header` lets a malicious Tier-3 peer install a cross-shard state header, causing `set_state_finalize` to corrupt `ChunkExtra.state_root` for the wrong shard — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` never asserts `chunk.shard_id() == shard_id`. The only structural guard — a `verify_path` Merkle-inclusion check against `chunk_headers_root` — is position-agnostic: it proves the chunk exists somewhere in the tree, not that it occupies the slot for the requested shard. A malicious Tier-3 peer can therefore serve shard 0's `ShardChunk` as the state-sync header for shard 1. After the header is stored under `StateHeaderKey(1, sync_hash)`, `set_state_finalize` reads it back, derives `shard_uid` from the `shard_id=1` parameter (not from the chunk), and calls `apply_chunk_postprocessing`, which writes `ChunkExtra` keyed by `(block_hash, shard_uid=1)` but seeded with shard 0's `prev_state_root`. Every subsequent block application for shard 1 then starts from shard 0's state.

---

### Finding Description

**Step 1 — `set_state_header` accepts a cross-shard header** [1](#0-0) 

`validate_chunk_proofs` checks internal chunk consistency (hash, tx root, receipts root) but never compares `chunk.shard_id()` to the `shard_id` parameter. [2](#0-1) 

The Merkle-inclusion check at line 394 calls `verify_path`, which is purely position-agnostic: [3](#0-2) 

It proves `ChunkHashHeight(shard0_chunk_hash, height)` is *somewhere* in `chunk_headers_root`, not that it is at the index corresponding to shard 1. Shard 0's chunk is legitimately in that tree (at index 0), so the proof verifies.

The receipt-proof loop (steps 4e–4f) hashes receipts as `ReceiptList(shard_id=1, receipts)` and checks the root against `block_header.prev_chunk_outgoing_receipts_root()`. An attacker with access to the canonical chain (public data) can supply the actual incoming receipts for shard 1 and their valid Merkle paths, satisfying this check. [4](#0-3) 

The header is then stored under `StateHeaderKey(shard_id=1, sync_hash)`: [5](#0-4) 

**Step 2 — `set_state_finalize` propagates the corruption**

`Chain::set_state_finalize` retrieves the stored header by `(shard_id=1, sync_hash)` — getting shard 0's chunk — and passes it to `chain_update.set_state_finalize`: [6](#0-5) 

`chain_update.set_state_finalize` derives `shard_uid` from the *parameter* `shard_id=1`, not from `chunk.shard_id()`: [7](#0-6) 

It then applies the chunk using shard 0's `prev_state_root` under `shard_uid=1`: [8](#0-7) 

`apply_chunk_postprocessing` writes `ChunkExtra` keyed by `(block_hash, shard_uid=1)` with the result of applying shard 0's state: [9](#0-8) 

There is no guard anywhere in this path that checks `chunk.shard_id() == shard_id`.

---

### Impact Explanation

`ChunkExtra` at `(block_hash, shard_uid=1)` now holds shard 0's post-application `state_root`. Every subsequent call to `set_state_finalize_on_height` for shard 1 reads this corrupted `ChunkExtra` as its starting state: [10](#0-9) 

All block applications for shard 1 from that point forward execute against shard 0's trie, silently corrupting shard 1's state. The commitment invariant — `ChunkExtra(block_hash, shard_uid=1).state_root` must equal shard 1's post-application root — is permanently violated for the syncing node.

---

### Likelihood Explanation

The attacker must operate as a Tier-3 state-sync peer reachable by the syncing node. This does not require validator, block-producer, or any other privileged role — any node can advertise Tier-3 capability. The canonical chain data needed to construct valid receipt proofs is publicly available. The attacker must also serve matching state parts (validated only against the header's `prev_state_root`, which is shard 0's root), which are also publicly obtainable. The attack is therefore reachable by an unprivileged network participant.

---

### Recommendation

Add an explicit shard-id binding check at the top of `set_state_header`, immediately after extracting the chunk:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {} does not match requested shard_id {}",
        chunk.shard_id(), shard_id
    )));
}
``` [11](#0-10) 

Similarly, add the same guard in `chain_update.set_state_finalize` after extracting the chunk from the header: [12](#0-11) 

---

### Proof of Concept

A test-loop test would:
1. Produce a multi-shard chain with two shards (0 and 1).
2. Compute the legitimate `ShardStateSyncResponseHeader` for shard 0 at `sync_hash`.
3. Construct a `ShardStateSyncResponseHeader` that wraps shard 0's chunk but uses the Merkle proof for shard 0's position in the block's `chunk_headers_root`, and supplies the actual incoming receipts for shard 1 as the receipt proofs.
4. Call `chain.state_sync_adapter.set_state_header(shard_id=1, sync_hash, crafted_header)` — assert it returns `Ok(())`.
5. Apply shard 0's state parts under shard 1 via `apply_state_part(shard_id=1, shard0_state_root, ...)`.
6. Call `chain.set_state_finalize(shard_id=1, sync_hash)`.
7. Assert `chain.get_chunk_extra(block_hash, shard_uid=1).state_root == shard0_state_root` (not shard 1's root).

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L376-403)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L525-529)
```rust
        // Saving the header data.
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
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

**File:** core/primitives/src/merkle.rs (L112-119)
```rust
/// Verify merkle path for given item and corresponding path.
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

**File:** chain/chain/src/chain.rs (L2704-2707)
```rust
        let shard_state_header = self.state_sync_adapter.get_state_header(shard_id, sync_hash)?;
        let mut height = shard_state_header.chunk_height_included();
        let mut chain_update = self.chain_update();
        let shard_uid = chain_update.set_state_finalize(shard_id, sync_hash, shard_state_header)?;
```

**File:** chain/chain/src/chain_update.rs (L460-468)
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
```

**File:** chain/chain/src/chain_update.rs (L513-514)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
```

**File:** chain/chain/src/chain_update.rs (L519-542)
```rust
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

**File:** chain/chain/src/chain_update.rs (L549-558)
```rust
        let new_chunk_result = NewChunkResult { gas_limit, shard_uid, apply_result };
        let mut store_update = self.chain_store_update.store().store_update();
        apply_chunk_postprocessing(
            &mut store_update,
            self.runtime_adapter.as_ref(),
            block.as_ref(),
            new_chunk_result,
            &config,
        )?;
        self.chain_store_update.merge(store_update);
```

**File:** chain/chain/src/chain_update.rs (L603-612)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let chunk_extra = self.chain_store_update.get_chunk_extra(prev_hash, &shard_uid)?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, *chunk_extra.state_root())?;

        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(*chunk_extra.state_root(), true),
```
