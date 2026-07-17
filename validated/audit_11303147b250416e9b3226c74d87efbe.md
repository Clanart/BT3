Looking at the full validation chain in `set_state_header` and the Merkle proof semantics, I need to trace each of the five checks against the proposed attack.

### Title
Missing `chunk.shard_id()` == `shard_id` Guard in `set_state_header` Allows Cross-Shard State Root Substitution — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`ChainStateSyncAdapter::set_state_header` accepts a `ShardStateSyncResponseHeader` from an untrusted peer and stores it under `StateHeaderKey(shard_id, sync_hash)` without ever asserting that the embedded chunk's `shard_id()` equals the `shard_id` parameter. Because `verify_path` is a position-blind Merkle membership check, a chunk belonging to shard A carries a valid proof of inclusion in the block's `chunk_headers_root` even when the caller passes `shard_id = B`. All five validation steps can be satisfied with public blockchain data, causing shard A's `prev_state_root` to be committed as shard B's state root, and all subsequent `apply_state_part` calls to write shard A's trie nodes and flat-state delta into shard B's `ShardUId`.

---

### Finding Description

**Entrypoint**: `ChainStateSyncAdapter::set_state_header` in `chain/chain/src/state_sync/adapter.rs`, called by the state-sync downloader after receiving a `ShardStateSyncResponseHeader` from a peer.

**The five validation steps and why none catches the shard mismatch:**

**Step 1 — `validate_chunk_proofs`** (`chain/chain/src/validate.rs` lines 22–66): checks the chunk's internal hash, tx-root, and outgoing-receipts-root. It never reads `chunk.shard_id()` and never compares it to the caller's `shard_id` parameter. [1](#0-0) 

**Step 2 — `verify_path` for `chunk_proof`** (`chain/chain/src/state_sync/adapter.rs` lines 394–403): verifies that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is a member of the Merkle tree rooted at `sync_prev_block_header.chunk_headers_root()`. The `verify_path` primitive (`core/primitives/src/merkle.rs` lines 113–118) is a pure membership check — it computes `compute_root_from_path(path, hash(item)) == root` with no position/index constraint. Shard A's chunk IS a leaf of that tree (at index A), so its proof passes even when `shard_id = B` is the parameter. There is no `verify_path_with_index` call here. [2](#0-1) [3](#0-2) 

**Step 3 — `verify_path` for `prev_chunk_proof`** (lines 416–425): identical position-blind membership check; same reasoning applies. [4](#0-3) 

**Step 4 — receipt-proof loop** (lines 447–510): iterates over `incoming_receipts_proofs`. The loop body hashes receipts with `shard_id` (the parameter), not `chunk.shard_id()`. An attacker can supply the real receipt proofs for shard B (public blockchain data) while embedding shard A's chunk. The loop passes because the proofs are cryptographically valid for shard B.

> **Correction to the question's "empty receipts" precondition**: with empty `incoming_receipts_proofs`, check 4g (`header.height() != prev_chunk_header.height_included()`) would fail because `hash_to_compare` remains `sync_hash` and `prev_chunk_header.height_included()` is always strictly less than `height(sync_hash)` in normal operation. The attack does not require empty receipts; it works with the real, publicly observable receipt proofs for shard B. [5](#0-4) 

**Step 5 — `validate_state_root_node`** (lines 512–523): validates the attacker-supplied `state_root_node` against `chunk_inner.prev_state_root()` — shard A's state root. The attacker provides a valid node for shard A's root (public data). The check passes. [6](#0-5) 

**Storage**: after all five checks pass, the header is written under `StateHeaderKey(shard_id=B, sync_hash)` with shard A's chunk embedded. [7](#0-6) 

**Downstream propagation — `set_state_part`** (lines 534–560): reads the stored header, extracts `chunk.prev_state_root()` (shard A's root), and validates incoming parts against it. Parts for shard A's trie pass; they are stored under `StatePartKey(sync_hash, shard_id=B, part_id)`. [8](#0-7) 

**Downstream propagation — `apply_state_part`** (`chain/chain/src/runtime/mod.rs` lines 1501–1529): derives `shard_uid` from the `shard_id` parameter (= B), then calls `tries.apply_all(&trie_changes, shard_uid, ...)` and `flat_state_delta.apply_to_flat_state(..., shard_uid)`. Shard A's trie nodes and flat-state delta are written into shard B's `ShardUId`. [9](#0-8) 

---

### Impact Explanation

Any node performing state sync for shard B can have its entire trie and flat-state for shard B replaced with shard A's data. After `set_state_finalize`, the node believes it has shard B's state but is actually operating on shard A's trie. All subsequent transaction execution, receipt routing, and query responses for shard B are silently wrong. The corruption is persistent (written to the DB) and not self-healing.

---

### Likelihood Explanation

State sync providers are untrusted peers — any node on the network can serve state sync responses. The attack requires only public blockchain data (shard A's chunk, its Merkle proof, and shard B's receipt proofs). No validator or privileged role is needed. The attack is applicable whenever a node enters state sync mode (epoch boundary, new node joining, node recovering from lag).

---

### Recommendation

Add an explicit shard-id guard immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
// ADD THIS:
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Additionally, replace the position-blind `verify_path` call for `chunk_proof` with `verify_path_with_index`, passing the expected shard index derived from the epoch's shard layout, to bind the proof to the correct leaf position.

---

### Proof of Concept

```
Setup: two-shard network, node N is state-syncing for shard B (shard_id=1).

Attacker (any peer) constructs ShardStateSyncResponseHeader:
  - chunk          = shard A's (shard_id=0) ShardChunk from sync_prev_block
  - chunk_proof    = shard A's Merkle path in sync_prev_block.chunk_headers_root
                     (valid membership proof; verify_path passes because it is
                      position-blind)
  - prev_chunk_header / prev_chunk_proof = shard A's prev chunk + its proof
  - incoming_receipts_proofs = real receipt proofs for shard B (public data)
  - root_proofs    = corresponding root proofs for shard B (public data)
  - state_root_node = valid node for shard A's prev_state_root (public data)

Attacker sends this header to N in response to a StateRequestHeader(shard_id=1).

N calls: set_state_header(shard_id=1, sync_hash, crafted_header)
  Step 1: validate_chunk_proofs → passes (chunk is internally valid)
  Step 2: verify_path(chunk_headers_root, shard_A_proof, ChunkHashHeight(shard_A_hash,...))
          → passes (shard A IS in the tree; verify_path is position-blind)
  Step 3: verify_path for prev_chunk → passes (same reasoning)
  Step 4: receipt loop with real shard B proofs → passes
  Step 5: validate_state_root_node(shard_A_node, shard_A_root) → passes

Result: StateHeaderKey(shard_id=1, sync_hash) stored with shard A's chunk.

N then calls set_state_part / apply_state_part for shard_id=1:
  apply_state_part derives shard_uid from shard_id=1
  writes shard A's trie nodes into shard B's ShardUId
  writes shard A's flat_state_delta into shard B's flat storage

Assert: node N's shard B state root == shard A's prev_state_root  ← corruption confirmed
```

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

**File:** chain/chain/src/state_sync/adapter.rs (L446-510)
```rust
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

**File:** core/primitives/src/merkle.rs (L113-119)
```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}
```

**File:** chain/chain/src/runtime/mod.rs (L1516-1527)
```rust
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
