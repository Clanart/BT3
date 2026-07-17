Looking at the validation chain in `set_state_header` and the `verify_path` semantics carefully before rendering a verdict.

### Title
Missing shard-position check in `set_state_header` allows a malicious peer to install a wrong-shard chunk as the state-sync header for the target shard, corrupting `ChunkExtra.state_root` — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` validates that the supplied chunk is *somewhere* in the block's `chunk_headers_root` Merkle tree, but never verifies that it is at the position that corresponds to the requested `shard_id`. Because `verify_path` is not position-aware, a malicious peer can supply shard A's chunk (with its valid Merkle path at position A) while the node is syncing shard B. The header passes all checks and is stored under `StateHeaderKey(shard_B, sync_hash)`. `set_state_finalize` then applies shard A's transactions against shard B's trie, producing an incorrect `ChunkExtra.state_root` for shard B.

---

### Finding Description

**Entrypoint / attacker surface**

During state sync a node sends a `StateRequestHeader { shard_id, sync_hash }` to peers and accepts the first valid `ShardStateSyncResponseHeader` it receives. Any peer — including an unprivileged one — can respond. The response is processed by `set_state_header`.

**The missing guard**

`set_state_header` performs the following checks (in order):

1. `validate_chunk_proofs` — verifies the chunk's internal hash/tx-root/receipt-root consistency. Does **not** check `chunk.shard_id()`.
2. `verify_path(chunk_headers_root, chunk_proof, ChunkHashHeight(chunk.chunk_hash(), chunk.height_included()))` — proves the chunk is *somewhere* in the block's Merkle tree.
3. `prev_chunk` Merkle proof.
4. Receipt proof chain.
5. `state_root_node` validity.

Nowhere is `chunk.shard_id()` compared to the `shard_id` parameter. [1](#0-0) 

**Why `verify_path` does not catch this**

`verify_path` is defined as:

```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}
pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
``` [2](#0-1) 

It only checks that the supplied path leads to the root. It does **not** check which leaf index the path corresponds to. The position-aware variant `verify_path_with_index` exists but is not used here. [3](#0-2) 

The `chunk_headers_root` is a Merkle tree over `ChunkHashHeight` values for **all** shards in shard-index order:

```rust
let (chunk_headers_root, chunk_proofs) = merklize(
    &sync_prev_block.chunks().iter()
        .map(|shard_chunk| ChunkHashHeight(shard_chunk.chunk_hash().clone(), shard_chunk.height_included()))
        .collect::<Vec<ChunkHashHeight>>(),
);
``` [4](#0-3) 

A valid Merkle path for shard A's chunk at position A will compute the correct root. The code never checks that position A equals the shard-index of `shard_id` (B).

**Propagation into `set_state_finalize`**

After the header is stored under `StateHeaderKey(shard_B, sync_hash)`, `set_state_finalize` retrieves it and applies the chunk:

```rust
let shard_state_header = self.state_sync_adapter.get_state_header(shard_id, sync_hash)?;
// ...
let shard_uid = shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
// shard_uid is derived from shard_B, but chunk contains shard A's transactions
let apply_result = self.runtime_adapter.apply_chunk(
    RuntimeStorageConfig::new(chunk_header.prev_state_root(), true), // shard A's state root
    ...
    ApplyChunkShardContext { shard_uid, ... }, // shard B's UID
    ...
    transactions, // shard A's transactions
)?;
``` [5](#0-4) 

The `apply_chunk` call uses `shard_uid` from shard B but `prev_state_root`, `transactions`, and `receipts` from shard A's chunk. The resulting `apply_result.new_root` is then stored as `ChunkExtra.state_root` for shard B via `apply_chunk_postprocessing`. [6](#0-5) 

---

### Impact Explanation

The exact corrupted value is `ChunkExtra.state_root` for shard B, which will hold shard A's post-execution state root instead of the canonical value. The node's trie for shard B is populated with shard A's state data (because `apply_state_part` is called with `shard_id=B` but validated against shard A's `prev_state_root`). All subsequent block processing for shard B on this node will fail or produce incorrect results, as `validate_chunk_with_chunk_extra` will see a mismatched state root.

---

### Likelihood Explanation

The attack requires a malicious peer that the syncing node connects to during state sync. State sync peers are not authenticated beyond network-level identity; any node on the network can respond to state header requests. The attacker needs a valid chunk from shard A (publicly available on-chain) and its Merkle path (computable from the block). No validator or privileged credentials are required.

---

### Recommendation

In `set_state_header`, after the `verify_path` check, add an explicit shard-identity guard:

```rust
// After verify_path succeeds:
let expected_shard_index = prev_shard_layout.get_shard_index(shard_id)?;
let chunk_shard_id = chunk.shard_id();
if chunk_shard_id != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into()
    ));
}
```

Alternatively, replace the plain `verify_path` call with `verify_path_with_index`, passing `prev_shard_index` and the total number of shards, which would simultaneously prove both inclusion and correct position. [7](#0-6) 

---

### Proof of Concept

Build a two-shard test-loop where:

1. A node begins state sync for `shard_id = 1` with a known `sync_hash`.
2. Intercept the `set_state_header` call and supply a crafted `ShardStateSyncResponseHeaderV2` where:
   - `chunk` = the valid chunk for `shard_id = 0` at `sync_prev_block.chunks()[0]`
   - `chunk_proof` = `chunk_proofs[0]` (the Merkle path for shard 0's position)
   - `state_root_node` = valid node for shard 0's `prev_state_root`
   - `incoming_receipts_proofs` / `root_proofs` = empty or minimal valid values
3. Assert `set_state_header` returns `Ok(())` (it will, because `verify_path` passes for shard 0's path and no shard_id equality check exists).
4. Supply state parts consistent with shard 0's `prev_state_root`.
5. Call `set_state_finalize(shard_id=1, sync_hash)`.
6. Read back `ChunkExtra` for shard 1 and assert its `state_root` differs from the canonical value obtained by a correctly-synced node — confirming the corruption.

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L102-111)
```rust
        let (chunk_headers_root, chunk_proofs) = merklize(
            &sync_prev_block
                .chunks()
                .iter()
                .map(|shard_chunk| {
                    ChunkHashHeight(shard_chunk.chunk_hash().clone(), shard_chunk.height_included())
                })
                .collect::<Vec<ChunkHashHeight>>(),
        );
        assert_eq!(&chunk_headers_root, sync_prev_block.header().chunk_headers_root());
```

**File:** chain/chain/src/state_sync/adapter.rs (L392-409)
```rust
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

**File:** core/primitives/src/merkle.rs (L121-129)
```rust
pub fn verify_path_with_index<T: BorshSerialize>(
    root: MerkleHash,
    path: &MerklePath,
    item: T,
    part_idx: u64,
    num_merklized_parts: u64,
) -> bool {
    verify_path_matches_index(path, part_idx, num_merklized_parts) && verify_path(root, path, item)
}
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
