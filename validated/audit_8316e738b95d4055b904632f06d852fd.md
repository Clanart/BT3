### Title
`set_state_header` Accepts Cross-Shard State Header Without Verifying Chunk's `shard_id` Matches the Requested Shard — (File: chain/chain/src/state_sync/adapter.rs)

---

### Summary

`ChainStateSyncAdapter::set_state_header(shard_id, sync_hash, header)` validates that the chunk embedded in the header is included in the block via a merkle proof, but **never checks that `chunk.shard_id() == shard_id`**. Because `verify_path` is not position-aware (it only reconstructs the root from the path, without verifying which leaf index the path corresponds to), a malicious peer can supply a header for shard B that embeds shard A's chunk with a valid merkle proof for shard A's position. The header is then stored under `StateHeaderKey(B, sync_hash)` carrying shard A's `prev_state_root`, causing subsequent state parts for shard A to be validated and applied to shard B's trie, silently corrupting the syncing node's state.

---

### Finding Description

**Step 1 — `validate_chunk_proofs` does not check `shard_id`.** [1](#0-0) 

`validate_chunk_proofs` only verifies the chunk's internal hash, transaction root, and receipts root. It never inspects `chunk.shard_id()`.

**Step 2 — `verify_path` is not position-aware.** [2](#0-1) 

`verify_path` calls `compute_root_from_path`, which simply folds the path left/right to reconstruct the root. It does **not** verify which leaf index the path encodes. The position-aware variant `verify_path_with_index` exists but is not used here. [3](#0-2) 

**Step 3 — `set_state_header` uses `verify_path` and has no `shard_id` cross-check.** [4](#0-3) 

The function verifies that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is somewhere in `chunk_headers_root`, but never asserts `chunk.shard_id() == shard_id`. The header is then persisted under `StateHeaderKey(shard_id, sync_hash)`: [5](#0-4) 

**Step 4 — The corrupted `state_root` propagates into part validation and application.**

`run_state_sync_for_shard` reads `state_root` directly from the stored header: [6](#0-5) 

Parts are then validated against that `state_root`: [7](#0-6) 

And applied to shard B's trie using the same wrong root: [8](#0-7) 

`apply_state_part` in the runtime writes trie nodes under `shard_uid` derived from the `shard_id` parameter (B), but the trie content comes from shard A's state: [9](#0-8) 

---

### Impact Explanation

A single malicious peer reachable during state sync can silently replace shard B's entire trie with shard A's trie. The syncing node will then operate on incorrect state: a validator node will produce or endorse blocks with wrong state roots, risking slashing; a non-validator node will diverge from the canonical chain and require a full re-sync. Because the corruption is written atomically to RocksDB and marked as applied, it persists across restarts.

**Severity: High**

---

### Likelihood Explanation

State sync is performed by any node that falls behind by more than two epochs, and by validators catching up to a new shard assignment. The attacker only needs to be one of the peers the syncing node contacts for the header — a single malicious node in the peer set suffices. No privileged role (validator, chunk producer) is required. The attacker must also serve state parts consistent with the wrong `state_root`, which is straightforward since those parts are simply shard A's legitimate parts.

**Likelihood: Medium**

---

### Recommendation

Inside `set_state_header`, after extracting the chunk, add an explicit shard identity check before any further validation:

```rust
// Verify the chunk in the header actually belongs to the requested shard.
if chunk.shard_id() != shard_id {
    return Err(Error::Other(format!(
        "set_shard_state failed: header chunk shard_id {:?} != requested shard_id {:?}",
        chunk.shard_id(), shard_id
    )));
}
```

Additionally, consider replacing `verify_path` with `verify_path_with_index` (passing the shard index as the expected position) so that the merkle proof is also bound to the correct leaf position in `chunk_headers_root`.

---

### Proof of Concept

1. Syncing node N begins state sync for shard B at `sync_hash`.
2. Attacker peer P intercepts the `StateRequestHeader { shard_id: B, sync_hash }` message.
3. P responds with a `ShardStateSyncResponseHeader` where:
   - `chunk` = a valid `ShardChunk` for shard A (obtained from the canonical chain)
   - `chunk_proof` = the legitimate merkle path for shard A's chunk at its own index in `sync_prev_block.chunk_headers_root`
   - `state_root_node` = shard A's state root node
4. N calls `set_state_header(B, sync_hash, header)`:
   - `validate_chunk_proofs` passes — shard A's chunk is internally consistent.
   - `verify_path(chunk_headers_root, chunk_proof_A, ChunkHashHeight(chunk_A.hash, chunk_A.height))` passes — shard A's chunk is legitimately in the tree.
   - No `shard_id` check exists; the header is stored under `StateHeaderKey(B, sync_hash)` with shard A's `prev_state_root`.
5. `run_state_sync_for_shard` reads `state_root = header.chunk_prev_state_root()` → shard A's root.
6. P serves state parts for shard A; each part passes `validate_state_part(B, state_root_A, ...)` because validation is purely against the root hash.
7. `apply_state_part(shard_id=B, state_root=state_root_A, ...)` writes shard A's trie nodes into shard B's `ShardUId` column in RocksDB.
8. Shard B's state is now shard A's state; N diverges from the canonical chain on the next block it processes or produces.

### Citations

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

**File:** chain/client/src/sync/state/shard.rs (L75-77)
```rust
    let header = downloader.ensure_shard_header(shard_id, sync_hash, cancel.clone()).await?;
    let state_root = header.chunk_prev_state_root();
    let num_parts = header.num_state_parts();
```

**File:** chain/client/src/sync/state/shard.rs (L334-340)
```rust
    runtime.apply_state_part(
        shard_id,
        &state_root,
        PartId { idx: part_id, total: num_parts },
        &state_part,
        &epoch_id,
    )?;
```

**File:** chain/client/src/sync/state/downloader.rs (L174-190)
```rust
                if matches!(
                    runtime_adapter.validate_state_part(
                        shard_id,
                        &state_root,
                        PartId { idx: part_id, total: num_state_parts },
                        &part,
                    ),
                    StatePartValidationResult::Valid
                ) {
                    let mut store_update = store.store_update();
                    let key = borsh::to_vec(&StatePartKey(sync_hash, shard_id, part_id)).unwrap();
                    let bytes = part.to_bytes();
                    store_update.set(DBCol::StateParts, &key, &bytes);
                    store_update.commit();
                } else {
                    return Err(near_chain::Error::Other("Part data failed validation".to_owned()));
                }
```

**File:** chain/chain/src/runtime/mod.rs (L1513-1527)
```rust
        let part = part
            .to_partial_state()
            .expect("Part was already validated earlier, so could never fail here");
        let ApplyStatePartResult { trie_changes, flat_state_delta, contract_codes } =
            Trie::apply_state_part(state_root, part_id, part);
        let tries = self.get_tries();
        let shard_uid = self.get_shard_uid_from_epoch_id(shard_id, epoch_id)?;
        let mut store_update = tries.store_update();
        tries.apply_all(&trie_changes, shard_uid, &mut store_update);
        tracing::debug!(target: "chain", %shard_id, values_count = %flat_state_delta.len(), "inserting values to flat storage");
        // TODO: `apply_to_flat_state` inserts values with random writes, which can be time consuming.
        //       Optimize taking into account that flat state values always correspond to a consecutive range of keys.
        flat_state_delta.apply_to_flat_state(&mut store_update.flat_store_update(), shard_uid);
        self.precompile_contracts(epoch_id, contract_codes)?;
        store_update.commit();
```
