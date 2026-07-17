Now I have enough information to make a definitive assessment. Let me trace the full attack path.

### Title
Missing shard-index binding in `set_state_header` chunk_proof verification allows cross-shard chunk substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`ChainStateSyncAdapter::set_state_header` verifies that the supplied chunk is *a member* of the block's `chunk_headers_root` Merkle tree, but never verifies that it occupies the leaf index that corresponds to `shard_id`. Because `verify_path` is a pure membership check, a state-sync peer can supply shard 1's chunk together with shard 1's valid Merkle proof while claiming to serve shard 0. The header passes all validation gates and is stored under `StateHeaderKey(shard_id=0, sync_hash)`. Downstream, `set_state_finalize` applies shard 1's transactions and receipts against shard 0's `shard_uid`, producing a `ChunkExtra.state_root` for shard 0 that diverges from the canonical value.

---

### Finding Description

**Entrypoint:** `ChainStateSyncAdapter::set_state_header` in `chain/chain/src/state_sync/adapter.rs`.

**Guard 1 — `validate_chunk_proofs`** (lines 379–385):

`validate_chunk_proofs` in `chain/chain/src/validate.rs` checks only internal self-consistency of the chunk: header hash, tx Merkle root, and outgoing-receipts Merkle root. It never compares `chunk.shard_id()` against the `shard_id` parameter. [1](#0-0) 

**Guard 2 — `verify_path` for `chunk_proof`** (lines 394–403):

```rust
if !verify_path(
    *sync_prev_block_header.chunk_headers_root(),
    shard_state_header.chunk_proof(),
    &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
) {
```

`verify_path` is defined as:

```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}
pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

It only checks that `compute_root_from_path(proof, hash(item)) == root`. It does **not** check which leaf index the item occupies. [2](#0-1) 

Compare with `verify_path_with_index`, which additionally calls `verify_path_matches_index` to enforce the leaf position: [3](#0-2) 

The chunk_proof check in `set_state_header` uses `verify_path`, not `verify_path_with_index`, and passes no shard index: [4](#0-3) 

**Guard 3 — `prev_chunk_proof`** (lines 416–425): Same issue — `verify_path` without index. [5](#0-4) 

**Guard 4 — receipt proofs** (lines 488–502): The receipts hash is computed with the *parameter* `shard_id` (shard 0), not the chunk's actual shard. An attacker can supply the legitimate receipts for shard 0 (publicly available on-chain), which will verify correctly against the canonical block headers. [6](#0-5) 

**Guard 5 — `validate_state_root_node`** (lines 512–523): Validates the supplied `state_root_node` against `chunk_inner.prev_state_root()`. Since the attacker supplies shard 1's chunk, this is shard 1's state root. The attacker also supplies shard 1's `state_root_node`, which is valid for that root. Check passes. [7](#0-6) 

**Storage** (lines 526–529): The malicious header is stored under `StateHeaderKey(shard_id=0, sync_hash)`. [8](#0-7) 

**`set_state_part` cascade** (lines 534–561): `set_state_part` reads the stored header to obtain `state_root`, which is now shard 1's `prev_state_root`. It validates incoming state parts against that root. The attacker must therefore also supply shard 1's state parts (valid for shard 1's state root). Any legitimate state parts for shard 0 would fail this validation. [9](#0-8) 

**`set_state_finalize` corruption** (lines 452–568 of `chain_update.rs`): `shard_uid` is derived from the `shard_id` parameter (shard 0), but `chunk_header.prev_state_root()`, `chunk.to_transactions()`, and `incoming_receipts_proofs` all come from shard 1's chunk. `apply_chunk` runs shard 1's payload against shard 0's `shard_uid`, producing a `ChunkExtra.state_root` for shard 0 that is the post-state of shard 1's execution — diverging from the canonical value for shard 0. [10](#0-9) 

---

### Impact Explanation

The syncing node installs the wrong committed state for shard 0. Its `ChunkExtra.state_root` for shard 0 diverges from every honest node's value. Subsequent block processing on top of this state will fail or produce invalid blocks, effectively stalling or corrupting the node's participation in the shard. The committed-data binding invariant — that `StateHeaderKey(shard_id, sync_hash)` stores the chunk at `shard_index(shard_id)` in the block — is violated.

---

### Likelihood Explanation

**Preconditions required:**

1. The attacker must be a state-sync peer (any node on the network can respond to state sync requests — this is an unprivileged role).
2. The attacker must track shard 1 to obtain its chunk body, Merkle proof, state root node, and state parts.
3. Shard 0 and shard 1 must have the same `prev_chunk_header.height_included()` (the common case when both shards produce chunks at every block; the receipt-chain termination check at line 507 would otherwise fail).

These preconditions are all satisfiable by any node that tracks multiple shards, which is the normal configuration for validators and many full nodes.

---

### Recommendation

Replace the two `verify_path` calls in `set_state_header` with `verify_path_with_index`, passing the shard index derived from `shard_id`:

```rust
let shard_index = shard_layout.get_shard_index(shard_id)?;
if !verify_path_with_index(
    *sync_prev_block_header.chunk_headers_root(),
    shard_state_header.chunk_proof(),
    &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
    shard_index as u64,
    chunks_count as u64,
) { ... }
```

Additionally, add an explicit guard:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other("set_shard_state failed: chunk shard_id mismatch".into()));
}
```

Apply the same fix to the `prev_chunk_proof` verification.

---

### Proof of Concept

**Test-loop setup (2 shards):**

1. Build a 2-shard `TestEnv`. Produce blocks until a valid `sync_hash` exists.
2. From client 0, obtain the legitimate `ShardStateSyncResponseHeader` for `shard_id=1` (shard 1's chunk, shard 1's `chunk_proof`, shard 1's `prev_chunk_header`, shard 1's `prev_chunk_proof`, shard 1's `state_root_node`).
3. Obtain the legitimate `incoming_receipts_proofs` for `shard_id=0` (valid receipts for shard 0 — these verify correctly because the receipt hash is computed with the `shard_id` parameter).
4. Construct a crafted `ShardStateSyncResponseHeaderV2` combining shard 1's chunk/proofs with shard 0's receipt proofs.
5. Call `client[1].chain.state_sync_adapter.set_state_header(shard_id=0, sync_hash, crafted_header)`.
6. **Assert it returns `Ok(())`** — the membership-only `verify_path` accepts shard 1's proof for shard 0's slot.
7. Supply shard 1's state parts via `set_state_part(shard_id=0, ...)`.
8. Call `set_state_finalize(shard_id=0, sync_hash)`.
9. **Assert** `client[1].chain.get_chunk_extra(sync_prev_block.hash(), shard_0_uid).state_root()` **differs** from `client[0].chain.get_chunk_extra(sync_prev_block.hash(), shard_0_uid).state_root()`.

### Citations

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

**File:** core/primitives/src/merkle.rs (L113-119)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L394-403)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L416-425)
```rust
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L487-503)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L512-523)
```rust
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L526-529)
```rust
        let mut store_update = self.chain_store.store().store_update();
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
        store_update.commit();
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

**File:** chain/chain/src/chain_update.rs (L513-557)
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

        self.chain_store_update.save_chunk(chunk);

        // `save_chunk` and `save_incoming_receipt` are chain-only and stay
        // inline; everything else goes through the shared helper.
        let config = self.chain_store_update.chain_store().chunk_persistence_config();
        let new_chunk_result = NewChunkResult { gas_limit, shard_uid, apply_result };
        let mut store_update = self.chain_store_update.store().store_update();
        apply_chunk_postprocessing(
            &mut store_update,
            self.runtime_adapter.as_ref(),
            block.as_ref(),
            new_chunk_result,
            &config,
        )?;
```
