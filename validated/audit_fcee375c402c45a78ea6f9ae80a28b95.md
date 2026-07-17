Now I have all the information needed to make a definitive assessment. Let me analyze the exact validation logic in `set_state_header`.

### Title
Missing `chunk.shard_id() == shard_id` Guard in `set_state_header` Allows Cross-Shard Header Poisoning - (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header()` accepts and stores a `ShardStateSyncResponseHeader` supplied by an unprivileged peer without ever asserting that the embedded chunk's shard ID matches the `shard_id` argument. The `verify_path` check proves only that the chunk hash is *somewhere* in the block's chunk-headers Merkle tree — not that it occupies the slot for the requested shard. A malicious peer can therefore store a header for shard Y that carries shard X's `prev_state_root`, causing all subsequent `set_state_part()` calls for shard Y to validate parts against the wrong state root.

### Finding Description

`set_state_header` performs five validation steps before writing to `DBCol::StateHeaders`:

**Step 1 — `validate_chunk_proofs`** [1](#0-0) 
Checks the chunk's internal hash, `tx_root`, and `outgoing_receipts_root` consistency. It never inspects `chunk.shard_id()`.

**Step 2 — `verify_path` against `chunk_headers_root`** [2](#0-1) 
Proves that `ChunkHashHeight(chunk.chunk_hash(), chunk.height_included())` is a leaf in the block's chunk-headers Merkle tree. The tree is built from **all** shards' chunks in shard-index order. `verify_path` confirms the leaf is *somewhere* in the tree at the position encoded by the supplied path — it does not confirm the position corresponds to `shard_id`. The `shard_id` parameter is never referenced in this call.

**Step 3 — receipt proof validation** [3](#0-2) 
Uses `shard_id` (Y) to compute `receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts))` and verifies it against the block's `prev_chunk_outgoing_receipts_root`. An attacker can satisfy this by supplying the correct public receipts for shard Y.

**Step 4 — `validate_state_root_node`** [4](#0-3) 
Validates `state_root_node` against `chunk.prev_state_root()`. If the chunk is shard X's chunk, the attacker supplies shard X's `state_root_node`, which matches shard X's `prev_state_root`. This passes.

**Storage** [5](#0-4) 
The header is written under `StateHeaderKey(shard_id, sync_hash)` — using the caller-supplied `shard_id` (Y), not the chunk's actual shard ID.

The `StateHeaderKey` struct itself: [6](#0-5) 

**Downstream effect in `set_state_part`** [7](#0-6) 
`set_state_part` reads the stored header for `(shard_id=Y, sync_hash)`, extracts `state_root` from the chunk (which is shard X's `prev_state_root`), and calls `validate_state_part(shard_id=Y, state_root=X_root, ...)`. Every legitimate part for shard Y fails this validation, and any part for shard X that happens to pass is stored under shard Y's `StatePartKey`.

### Impact Explanation

An unprivileged peer responding to a state-sync header request can cause the syncing node to:

1. Store a `StateHeaderKey(Y, sync_hash)` entry whose embedded chunk carries shard X's `prev_state_root`.
2. Reject all legitimate state parts for shard Y (they don't match shard X's state root).
3. Potentially accept and store state parts for shard X under shard Y's `StatePartKey`, assembling the wrong trie.

The primary confirmed impact is **state-sync DoS** for the targeted shard: the node cannot complete state sync for shard Y while the poisoned header is cached. If the attacker controls multiple peers, the node may be unable to obtain a clean header and stall indefinitely.

A secondary concern is that if the node does assemble shard X's trie under shard Y's key, the mismatch with the canonical `prev_state_root` in the block header would be detected at state-application time — preventing silent state corruption, but still causing the node to fail catchup.

### Likelihood Explanation

Any peer reachable by the syncing node can respond to state-sync header requests. No validator or operator privilege is required. The attacker only needs:
- Knowledge of the block's chunk headers (public).
- The correct receipts for shard Y (public).
- A valid chunk body for any shard X in the same block (obtainable from the network).

### Recommendation

Add an explicit shard-ID guard immediately after `validate_chunk_proofs` in `set_state_header`:

```rust
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Apply the same guard to `prev_chunk_header` if present. This closes the gap without touching any other validation logic.

### Proof of Concept

A unit test strategy (as suggested in the question):

1. Build a two-shard chain with `sync_hash` pointing to a valid sync block.
2. Call `compute_state_response_header(shard_id=0, sync_hash)` to obtain a legitimate header for shard 0, including its valid `chunk_proof`.
3. Construct a `ShardStateSyncResponseHeader` that keeps shard 0's `chunk`, `chunk_proof`, and `state_root_node`, but supplies the correct incoming receipts for shard 1 (with matching `root_proofs` derived from the block).
4. Call `set_state_header(shard_id=1, sync_hash, poisoned_header)`.
5. Assert the call returns `Err(...)` — **currently it returns `Ok(())`**, confirming the missing guard.
6. Verify that `DBCol::StateHeaders` now contains an entry under `StateHeaderKey(1, sync_hash)` whose embedded chunk has `shard_id() == 0`.

The exact corrupted value is `chunk.prev_state_root()` (shard 0's state root) stored under `StateHeaderKey(shard_id=1, sync_hash)`, causing `set_state_part` for shard 1 to validate against the wrong root.

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

**File:** chain/chain/src/state_sync/adapter.rs (L488-502)
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
```

**File:** chain/chain/src/state_sync/adapter.rs (L513-523)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L527-528)
```rust
        let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
        store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
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

**File:** core/primitives/src/state_sync.rs (L20-20)
```rust
pub struct StateHeaderKey(pub ShardId, pub CryptoHash);
```
