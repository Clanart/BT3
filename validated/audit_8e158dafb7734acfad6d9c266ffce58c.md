Let me look at the full `set_state_header` validation logic and the `verify_path` function to confirm the position-agnostic nature of the Merkle proof.

Based on my thorough analysis of the code, I can now provide a definitive assessment.

**Key findings from the code:**

1. `validate_chunk_proofs` (`chain/chain/src/validate.rs:22-67`) checks internal chunk consistency (hash, tx root, receipts root) but **never checks `chunk.shard_id()` against the `shard_id` parameter**.

2. `verify_path` (`core/primitives/src/merkle.rs:113-115`) is **position-agnostic** — it proves the chunk is *somewhere* in the Merkle tree, not at a specific shard index. The position-aware `verify_path_with_index` exists but is not used here.

3. `set_state_header` (`chain/chain/src/state_sync/adapter.rs:368-532`) has **no explicit `chunk.shard_id() == shard_id` assertion** anywhere in its body.

4. `set_state_part` (`chain/chain/src/state_sync/adapter.rs:541-543`) reads the stored header and blindly uses `chunk.take_header().take_inner().prev_state_root()` — whatever state root was in the stored chunk.

---

### Title
Missing shard-index binding in `set_state_header` chunk Merkle proof allows cross-shard state root substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a peer-supplied `ShardStateSyncResponseHeader` and stores it under `StateHeaderKey(shard_id, sync_hash)` without ever asserting that the embedded `chunk.shard_id()` equals the requested `shard_id`. The Merkle proof used to validate chunk inclusion is position-agnostic (`verify_path`, not `verify_path_with_index`), so a chunk belonging to shard 1 passes the inclusion check even when `shard_id=0` is requested. A malicious peer can therefore install shard 1's `prev_state_root` under shard 0's `StateHeaderKey`, causing all subsequent `set_state_part` and `apply_state_part` calls to operate on the wrong state root.

### Finding Description

In `set_state_header`, the validation sequence is:

**Step 1-2** — `validate_chunk_proofs(&chunk, ...)` verifies the chunk's internal hash and receipt/tx roots. It does not check `chunk.shard_id()` against the `shard_id` parameter. [1](#0-0) 

**Step 3a** — `verify_path(*sync_prev_block_header.chunk_headers_root(), chunk_proof, &ChunkHashHeight(...))` proves the chunk is *a leaf* in the block's chunk Merkle tree. Because `verify_path` only calls `compute_root_from_path` and compares to the root, it is entirely position-agnostic. A valid proof for shard 1's chunk passes this check even when `shard_id=0` is requested. [2](#0-1) [3](#0-2) 

**Step 4** — Receipt proof validation hashes receipts as `ReceiptList(shard_id, receipts)` using the *parameter* `shard_id` (0), not `chunk.shard_id()`. An attacker supplies the real on-chain incoming receipts for shard 0 (public data), which pass all receipt proof checks independently of which chunk is embedded. [4](#0-3) 

**Step 5** — `validate_state_root_node` checks the `state_root_node` against `chunk_inner.prev_state_root()` — shard 1's state root — which is internally consistent. [5](#0-4) 

**Storage** — The header (containing shard 1's chunk and shard 1's `prev_state_root`) is written to `DBCol::StateHeaders` under key `StateHeaderKey(shard_id=0, sync_hash)`. [6](#0-5) 

Downstream, `set_state_part` reads this stored header and extracts `state_root` from the embedded chunk: [7](#0-6) 

This `state_root` is shard 1's state root. State parts for shard 1 are validated against shard 1's state root (passing), and shard 1's state is installed under shard 0's key.

### Impact Explanation

The exact corrupted value is: `DBCol::StateHeaders[StateHeaderKey(0, sync_hash)]` stores a `ShardStateSyncResponseHeader` whose `chunk.prev_state_root()` belongs to shard 1. All subsequent `set_state_part(shard_id=0, ...)` calls validate parts against shard 1's state root and accept shard 1's state data. `apply_state_part` then installs shard 1's full trie state under shard 0's `ShardUId`, corrupting the node's view of shard 0 for the entire epoch.

### Likelihood Explanation

The attack requires a malicious peer reachable during state sync. The attacker needs only public on-chain data (chunk headers, Merkle paths, incoming receipts for shard 0) to construct the crafted header. No validator or operator privileges are required — any peer that the syncing node contacts for state sync can trigger this.

### Recommendation

Add an explicit shard-id binding check immediately after extracting the chunk in `set_state_header`:

```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other(format!(
        "set_shard_state failed: chunk shard_id {:?} != requested shard_id {:?}",
        chunk.shard_id(), shard_id
    )));
}
```

Additionally, consider replacing the position-agnostic `verify_path` with `verify_path_with_index` using the shard's index in the block's chunk list, so the Merkle proof binds the chunk to its specific position. [8](#0-7) 

### Proof of Concept

```rust
// In a test-loop environment with 2 shards:
let header_for_shard_1 = client[0]
    .chain.state_sync_adapter
    .get_state_response_header(shard_id_1, sync_hash)
    .unwrap();

// Call set_state_header with shard_id=0 but header for shard 1
let result = client[1]
    .chain.state_sync_adapter
    .set_state_header(shard_id_0, sync_hash, header_for_shard_1.clone());

// Assert: should error, but currently succeeds
assert!(result.is_err(), "Expected error for mismatched shard_id");

// Or assert the stored header's chunk matches the requested shard
let stored = client[1].chain.state_sync_adapter
    .get_state_header(shard_id_0, sync_hash).unwrap();
assert_eq!(stored.cloned_chunk().shard_id(), shard_id_0,
    "Stored header chunk shard_id must match requested shard_id");
```

The receipt proofs in `header_for_shard_1` would need to be replaced with valid proofs for shard 0 (computable from public chain data) to fully bypass step 4, but the structural gap — no `chunk.shard_id() == shard_id` check and a position-agnostic Merkle proof — is the root cause.

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

**File:** chain/chain/src/state_sync/adapter.rs (L488-503)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L541-545)
```rust
        let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
        let chunk = shard_state_header.take_chunk();
        let state_root = *chunk.take_header().take_inner().prev_state_root();
        if matches!(
            self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part),
```

**File:** core/primitives/src/merkle.rs (L113-118)
```rust
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
```
