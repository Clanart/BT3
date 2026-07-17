## Analysis

Let me trace the exact validation path in `set_state_header` and determine whether the shard_id parameter is ever compared against `chunk.shard_id()`.

### Step-by-step validation in `set_state_header`

**Step 1-2 (`validate_chunk_proofs`):** [1](#0-0) 

This validates the chunk's internal consistency: header hash, transaction root, and receipt root. It never compares `chunk.shard_id()` against any expected shard_id parameter.

**Step 3a (Merkle proof against `chunk_headers_root`):** [2](#0-1) 

`verify_path` checks that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is a leaf in the Merkle tree rooted at `sync_prev_block_header.chunk_headers_root()`. The `chunk_headers_root` covers **all shards' chunks**. The proof encodes a path from a leaf to the root via Left/Right directions, but `verify_path` only checks that the path leads to the root — it does **not** verify which position (shard index) the leaf occupies: [3](#0-2) 

A valid Merkle proof for shard B's chunk (at position B in the tree) passes this check even when submitted as shard A's header.

**Step 3b (prev_chunk proof):** Same `verify_path` logic — no shard_id position check. [4](#0-3) 

**Step 4 (receipt proofs):** Uses the `shard_id` **parameter** (A) for `ReceiptList(shard_id, receipts)` hashing — independent of `chunk.shard_id()`. An attacker can supply legitimate shard-A receipt proofs (obtained from an honest node) combined with shard B's chunk. [5](#0-4) 

**Step 4g height check:** Verifies receipt proofs cover from `sync_hash` back to `prev_chunk_header.height_included()`. If shard A and shard B have the same `height_included` (the common case when all shards produce chunks at every height), this passes. [6](#0-5) 

**Step 5 (`validate_state_root_node`):** Validates the `state_root_node` against `chunk_inner.prev_state_root()` — shard B's state root. No shard_id comparison. [7](#0-6) 

**Storage (no shard_id check anywhere):** [8](#0-7) 

The header is stored under `StateHeaderKey(shard_id=A, sync_hash)` with shard B's chunk embedded — including shard B's `prev_state_root`.

### Downstream corruption

`set_state_part` reads the stored header and extracts `state_root` from the embedded chunk's `prev_state_root()` (shard B's root), then validates parts against it: [9](#0-8) 

`apply_state_part` then writes shard B's trie data into `shard_uid` derived from `shard_id=A`: [10](#0-9) 

`set_state_finalize` applies shard B's chunk (transactions, receipts, `prev_state_root`) under shard A's `shard_uid`: [11](#0-10) 

### Attacker reachability

Any node can advertise state snapshot availability and be selected as a state-sync provider. The network selects snapshot hosts from `snapshot_hosts`: [12](#0-11) 

This is an unprivileged operation — no validator or operator keys are required.

---

### Title
Missing `chunk.shard_id()` vs `shard_id` parameter check in `set_state_header` allows cross-shard state-root substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary
`ChainStateSyncAdapter::set_state_header` accepts a `ShardStateSyncResponseHeader` for `shard_id=A` whose embedded `ShardChunk` belongs to shard B (B ≠ A). All five validation steps pass because none compare `chunk.shard_id()` against the `shard_id` parameter. The header is stored under `StateHeaderKey(shard_id=A, sync_hash)` with shard B's `prev_state_root`, corrupting the state-root-to-shard-id binding in `DBCol::StateHeaders`.

### Finding Description
In `set_state_header` (`chain/chain/src/state_sync/adapter.rs`, lines 368–531):

- **Step 1-2** (`validate_chunk_proofs`): validates chunk internal consistency only; no shard_id cross-check.
- **Step 3a** (`verify_path` against `chunk_headers_root`): the Merkle tree covers all shards. `verify_path` only checks that the chunk hash is reachable from the root — it does not verify the leaf's position (shard index). Shard B's chunk with shard B's Merkle proof passes this check.
- **Step 3b**: same position-agnostic `verify_path` for `prev_chunk`.
- **Step 4**: receipt proofs are hashed with `ReceiptList(shard_id, receipts)` using the **parameter** `shard_id=A`, independent of `chunk.shard_id()`. An attacker copies legitimate shard-A receipt proofs.
- **Step 4g**: height check passes when shard A and shard B have the same `prev_chunk.height_included()` (the common case).
- **Step 5**: `validate_state_root_node` validates shard B's `state_root_node` against shard B's `prev_state_root` — consistent, so it passes.

The missing guard is a single equality check: `chunk.shard_id() != shard_id`.

### Impact Explanation
The syncing node installs shard B's trie data into shard A's storage (`shard_uid` for A). After `set_state_finalize`, the node's shard A state root is shard B's `prev_state_root`. The node will diverge from the canonical chain when it attempts to apply subsequent chunks for shard A, since its local state root does not match the chain's expected state root for shard A. This causes the node to be unable to participate in consensus for shard A and may cause it to reject valid blocks or produce invalid chunks.

### Likelihood Explanation
The attack requires a malicious node to advertise state snapshot availability (unprivileged) and be selected as the state-sync provider for the victim. The crafted header requires combining legitimate data from two honest state-sync responses (shard A's receipt proofs + shard B's chunk/proof), both publicly obtainable. The attack succeeds in the common case where all shards produce chunks at every height (same `height_included`).

### Recommendation
Add an explicit shard_id check at the beginning of `set_state_header`, immediately after extracting the chunk:

```rust
let chunk = shard_state_header.cloned_chunk();
// ADD THIS CHECK:
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk.shard_id() does not match shard_id parameter".into(),
    ));
}
```

Similarly, verify `prev_chunk_header.shard_id() == shard_id` when `prev_chunk_header` is present.

### Proof of Concept
A test-loop test in `chain/chain/src/state_sync/` would:
1. Produce a chain with ≥2 shards.
2. Obtain a legitimate `ShardStateSyncResponseHeader` for shard 1 (chunk + chunk_proof + prev_chunk + prev_chunk_proof + state_root_node).
3. Obtain legitimate incoming receipt proofs for shard 0 (from a legitimate shard-0 header).
4. Construct a crafted `ShardStateSyncResponseHeaderV2` with shard 1's chunk/proofs and shard 0's receipt proofs.
5. Call `set_state_header(shard_id=0, sync_hash, crafted_header)`.
6. Assert `Ok(())` is returned.
7. Read back `StateHeaderKey(0, sync_hash)` from `DBCol::StateHeaders` and assert the embedded chunk has `chunk.shard_id() == 1` and `chunk.prev_state_root() == shard_1_prev_state_root`.

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

**File:** chain/chain/src/state_sync/adapter.rs (L505-510)
```rust
        // 4g. Checking that there are no more heights to get incoming_receipts
        let header = self.chain_store.get_block_header(&hash_to_compare)?;
        if header.height() != prev_chunk_header.map_or(0, |h| h.height_included()) {
            byzantine_assert!(false);
            return Err(Error::Other("set_shard_state failed: invalid incoming receipts".into()));
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

**File:** chain/chain/src/state_sync/adapter.rs (L525-529)
```rust
        // Saving the header data.
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

**File:** chain/chain/src/runtime/mod.rs (L1501-1528)
```rust
    fn apply_state_part(
        &self,
        shard_id: ShardId,
        state_root: &StateRoot,
        part_id: PartId,
        part: &StatePart,
        epoch_id: &EpochId,
    ) -> Result<(), Error> {
        let _timer = metrics::STATE_SYNC_APPLY_PART_DELAY
            .with_label_values(&[&shard_id.to_string()])
            .start_timer();

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
        Ok(())
```

**File:** chain/chain/src/chain_update.rs (L513-520)
```rust
        let shard_uid =
            shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
        let memtrie_pin = self
            .runtime_adapter
            .get_tries()
            .maybe_pin_memtrie_root(shard_uid, chunk_header.prev_state_root())?;
        let apply_result = self.runtime_adapter.apply_chunk(
            RuntimeStorageConfig::new(chunk_header.prev_state_root(), true),
```

**File:** chain/network/src/peer_manager/peer_manager_actor.rs (L951-958)
```rust
                let Some(peer_id) = self.state.snapshot_hosts.select_host_for_part(
                    &sync_prev_prev_hash,
                    shard_id,
                    part_id,
                ) else {
                    tracing::debug!(target: "network", %shard_id, ?sync_hash, ?part_id, "no snapshot hosts available");
                    return NetworkResponses::NoDestinationsAvailable;
                };
```
