Looking at the full validation logic in `set_state_header` and the call chain, I need to determine whether the shard_id parameter is ever cross-checked against the chunk embedded in the header.

### Title
Missing `chunk.shard_id() == shard_id` Guard in `set_state_header` Allows Wrong-Shard State Installation — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` accepts a `shard_id` parameter and a `ShardStateSyncResponseHeader` from a peer, validates the header through five steps, then stores it under `StateHeaderKey(shard_id, sync_hash)`. **None of the five validation steps verify that the chunk embedded in the header actually belongs to `shard_id`.** An unprivileged peer serving state sync data can craft a header containing shard S_wrong's chunk (with its valid Merkle proof) combined with shard S_correct's legitimate incoming receipt proofs, pass all validation, and cause the syncing node to store S_wrong's `prev_state_root` under `StateHeaderKey(S_correct, sync_hash)`. Subsequent `set_state_part` calls then validate and install S_wrong's trie state for S_correct.

---

### Finding Description

The full validation sequence in `set_state_header` is:

**Step 1–2** — `validate_chunk_proofs(&chunk, epoch_manager)`: [1](#0-0) 

This checks internal chunk consistency (hash, tx_root, receipts_root) but **never reads `chunk.shard_id()`**. A chunk for any valid shard passes.

**Step 3a** — Merkle path check: [2](#0-1) 

`verify_path` confirms the chunk's `(hash, height_included)` pair is a leaf in the block's `chunk_headers_root` Merkle tree. The block's Merkle tree contains one leaf per shard, ordered by shard index. A valid proof for S_wrong's chunk at position `shard_index(S_wrong)` passes this check even when `shard_id` = S_correct, because `verify_path` does not verify which position the leaf occupies — only that it is reachable via the supplied path.

**Step 4e** — Receipt hash check: [3](#0-2) 

`receipts_hash` is computed as `hash(ReceiptList(shard_id, receipts))` where `shard_id` is the **parameter** (S_correct). This correctly binds the receipt content to S_correct, but it says nothing about which shard the embedded chunk belongs to. An attacker supplies the actual on-chain incoming receipts for S_correct; the check passes.

**Step 4f** — Block-level receipt root check: [4](#0-3) 

Verifies `root` (the `prev_outgoing_receipts_root` of a sending chunk) is in the block's `prev_chunk_outgoing_receipts_root` Merkle tree. The attacker uses real on-chain roots; the check passes.

**Step 5** — State root node check: [5](#0-4) 

Validates `state_root_node` against `chunk_inner.prev_state_root()`. `chunk_inner` is S_wrong's chunk inner, so `prev_state_root()` is S_wrong's state root. The attacker supplies S_wrong's actual `state_root_node`; the check passes.

**Storage** — after all checks pass: [6](#0-5) 

The key is `StateHeaderKey(shard_id, sync_hash)` = `StateHeaderKey(S_correct, sync_hash)`, but the stored header contains S_wrong's chunk and S_wrong's `prev_state_root`.

The missing guard — never present anywhere in the function — is:
```rust
if chunk.shard_id() != shard_id {
    return Err(Error::Other("set_shard_state failed: chunk shard_id mismatch".into()));
}
```

The grep search for any `chunk.shard_id()` reference in `adapter.rs` returns zero matches, confirming the check is absent.

---

### Impact Explanation

`set_state_part` reads the stored header to obtain `state_root`: [7](#0-6) 

`state_root` is extracted from the stored chunk's `prev_state_root()`, which is S_wrong's state root. The attacker then supplies S_wrong's actual state parts, which pass `validate_state_part` against S_wrong's state root. The syncing node installs S_wrong's full trie state under S_correct's `ShardUId`, causing it to operate with the wrong state for S_correct — producing invalid blocks, failing state root checks, or being slashed if it is a validator.

---

### Likelihood Explanation

The attacker must be a peer that the syncing node contacts for state sync data (any node can serve state sync). The attacker needs:
1. S_wrong's chunk body and Merkle proof — public blockchain data.
2. S_wrong's previous chunk and Merkle proof — public blockchain data.
3. The actual incoming receipt proofs for S_correct — public blockchain data.
4. S_wrong's `state_root_node` — available to any node tracking S_wrong.
5. S_wrong's state parts — available to any node tracking S_wrong.

No validator, block producer, or privileged role is required. The attack is constructible by any full node tracking S_wrong.

---

### Recommendation

Add an explicit shard identity check immediately after extracting the chunk, before any other validation:

```rust
let chunk = shard_state_header.cloned_chunk();
// NEW: bind the chunk to the requested shard_id
if chunk.shard_id() != shard_id {
    byzantine_assert!(false);
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This should be inserted at line 376–377 of `chain/chain/src/state_sync/adapter.rs`, before `validate_chunk_proofs`. [8](#0-7) 

---

### Proof of Concept

```
Preconditions:
  - Multi-shard epoch with shards S_correct and S_wrong.
  - Syncing node requests state for S_correct at sync_hash.
  - Attacker is a state sync peer.

Attacker constructs ShardStateSyncResponseHeader:
  chunk             = S_wrong's actual ShardChunk (valid, passes validate_chunk_proofs)
  chunk_proof       = S_wrong's Merkle proof in sync_prev_block.chunk_headers_root
  prev_chunk_header = S_wrong's previous chunk header
  prev_chunk_proof  = S_wrong's previous chunk Merkle proof
  incoming_receipts_proofs = actual on-chain incoming receipts for S_correct
  root_proofs       = actual prev_outgoing_receipts_root values + Merkle paths for S_correct's receipts
  state_root_node   = S_wrong's actual StateRootNode

Syncing node calls:
  set_state_header(S_correct, sync_hash, crafted_header)

Validation outcome:
  Step 1-2: PASS  (S_wrong's chunk is internally valid)
  Step 3a:  PASS  (S_wrong's chunk_proof verifies against chunk_headers_root)
  Step 3b:  PASS  (S_wrong's prev_chunk_proof verifies)
  Step 4:   PASS  (receipts_hash uses shard_id=S_correct; attacker supplies real receipts)
  Step 5:   PASS  (state_root_node consistent with S_wrong's prev_state_root)

Stored: DBCol::StateHeaders[StateHeaderKey(S_correct, sync_hash)]
        = header with chunk.prev_state_root() = S_wrong's state root

set_state_part(S_correct, sync_hash, ...) then installs S_wrong's trie for S_correct.

Integration test assertion:
  stored_header = chain_store.get_state_header(S_correct, sync_hash)
  assert_eq!(stored_header.chunk_prev_state_root(), expected_S_correct_state_root)
  // FAILS: returns S_wrong's state root
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

**File:** chain/chain/src/state_sync/adapter.rs (L376-385)
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

**File:** chain/chain/src/state_sync/adapter.rs (L487-493)
```rust
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
                }
```

**File:** chain/chain/src/state_sync/adapter.rs (L495-502)
```rust
                if !verify_path(
                    *block_header.prev_chunk_outgoing_receipts_root(),
                    block_proof,
                    root,
                ) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
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
