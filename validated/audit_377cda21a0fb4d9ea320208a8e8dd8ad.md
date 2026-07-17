Based on my thorough analysis of the code, here is my assessment:

---

### Title
Missing `chunk.shard_id() == shard_id` Binding in `set_state_header` Allows Cross-Shard State Corruption — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a peer-supplied `ShardStateSyncResponseHeader` for `shard_id = B` but never asserts that the embedded chunk belongs to shard B. A malicious peer can supply shard A's chunk (with its own valid Merkle proof) together with shard B's publicly-observable incoming-receipt proofs. Every validation gate passes, and the function stores the header under `StateHeaderKey(shard_B, sync_hash)` with shard A's `prev_state_root`. Subsequent `set_state_part` and `set_state_finalize` calls install shard A's trie as shard B's committed state.

---

### Finding Description

**Entrypoint / call path**

A peer-downloaded `ShardStateSyncResponseHeader` reaches `set_state_header` via:

```
StateSyncDownloader::ensure_shard_header   (downloader.rs:44-131)
  → StateHeaderValidationRequest sent to ClientActor
  → ClientActor::handle<StateHeaderValidationRequest>  (client_actor.rs:2133-2146)
  → chain.state_sync_adapter.set_state_header(shard_id=B, sync_hash, header)
``` [1](#0-0) [2](#0-1) 

**The missing guard**

`set_state_header` extracts the chunk from the attacker-supplied header and immediately calls `validate_chunk_proofs`. That function checks only internal chunk consistency (hash, tx root, receipts root) — it never compares `chunk.shard_id()` to the `shard_id` parameter. [3](#0-2) [4](#0-3) 

**Why the Merkle-inclusion check does not close the gap**

The check at lines 394-403 verifies that the chunk is *somewhere* in the block's `chunk_headers_root`:

```rust
if !verify_path(
    *sync_prev_block_header.chunk_headers_root(),
    shard_state_header.chunk_proof(),
    &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
) { … }
```

`verify_path` is position-agnostic — it only checks that `hash(item)` combined with the path directions produces the root. For a 2-shard block `[chunk_A, chunk_B]`:

- Root = `combine_hash(hash(chunk_A), hash(chunk_B))`
- Shard A's valid path: `[{hash: hash(chunk_B), direction: Right}]`

Supplying shard A's chunk with shard A's own path satisfies `verify_path(Root, path_A, chunk_A) == true` even when `shard_id = B`. The check proves only that the chunk is *in* the block, not that it occupies shard B's slot. [5](#0-4) [6](#0-5) [7](#0-6) 

**Why the receipt-proof check does not close the gap**

The receipt loop at line 488 hashes receipts with the *parameter* `shard_id` (B), not with `chunk.shard_id()`:

```rust
let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
```

The `to_shard_id` field of `ShardProof` is explicitly ignored (`to_shard_id: _`, line 478). The attacker supplies shard B's *actual* incoming-receipt proofs — which are public blockchain data — satisfying this check while the embedded chunk still belongs to shard A. [8](#0-7) 

**Height-coverage check**

The final check at line 507 requires that the receipt-proof chain terminates at `prev_chunk_header.height_included()`. In the common case (non-resharding epoch), all shards' chunks share the same `height_included`, so shard A's `prev_chunk_header` satisfies the check when shard B's receipt proofs are supplied. [9](#0-8) 

**Storage of the corrupted binding**

After all checks pass, the header is stored under `StateHeaderKey(shard_B, sync_hash)` — but the embedded chunk carries shard A's `prev_state_root`:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
``` [10](#0-9) [11](#0-10) 

---

### Impact Explanation

`set_state_part` reads the stored header and validates parts against `chunk.prev_state_root()` — now shard A's root. The attacker supplies state parts valid for shard A's trie, which pass validation. [12](#0-11) 

`set_state_finalize` / `chain_update::set_state_finalize` then calls `apply_chunk` with `RuntimeStorageConfig::new(chunk_header.prev_state_root(), true)` and `shard_uid` derived from shard B. This installs shard A's trie nodes under shard B's `ShardUId`, giving shard B wrong account balances and contract state after sync. [13](#0-12) [14](#0-13) 

---

### Likelihood Explanation

- The attacker is an unprivileged peer — no validator or operator privileges required.
- All inputs needed (shard A's chunk, its Merkle proof, shard B's receipt proofs) are publicly observable on-chain.
- The attack works in the standard (non-resharding) case where all shards share the same `height_included`.
- The syncing node retries on failure, so the attacker has multiple attempts.

---

### Recommendation

Add an explicit shard-id binding check immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
``` [3](#0-2) 

Additionally, consider verifying that the chunk's Merkle proof corresponds to shard B's index in the block (using `verify_path_with_index` or by checking the shard layout index), and assert `shard_proof.to_shard_id == shard_id` in the receipt-proof loop. [15](#0-14) 

---

### Proof of Concept

```rust
// In a test-loop state-sync test with 2 shards:
// 1. Obtain the legitimate header for shard 0 (shard A):
let header_shard_a = chain.state_sync_adapter
    .get_state_response_header(shard_id_a, sync_hash).unwrap();

// 2. Obtain shard B's actual incoming-receipt proofs from the chain store
//    and substitute them into a crafted ShardStateSyncResponseHeaderV2:
let crafted_header = ShardStateSyncResponseHeaderV2 {
    chunk: header_shard_a.chunk(),          // shard A's chunk
    chunk_proof: header_shard_a.chunk_proof(), // shard A's valid Merkle proof
    prev_chunk_header: header_shard_a.prev_chunk_header(),
    prev_chunk_proof: header_shard_a.prev_chunk_proof(),
    incoming_receipts_proofs: shard_b_receipt_proofs, // shard B's public proofs
    root_proofs: shard_b_root_proofs,
    state_root_node: header_shard_a.state_root_node(),
};

// 3. Call set_state_header for shard B with shard A's chunk:
let result = chain.state_sync_adapter.set_state_header(
    shard_id_b, sync_hash,
    ShardStateSyncResponseHeader::V2(crafted_header),
);
assert!(result.is_ok()); // passes all validation

// 4. Verify the stored header has shard A's chunk:
let stored = chain.state_sync_adapter.get_state_header(shard_id_b, sync_hash).unwrap();
assert_eq!(stored.cloned_chunk().shard_id(), shard_id_a); // != shard_id_b
// => StateHeaderKey(shard_B) is bound to shard A's prev_state_root
```

### Citations

**File:** chain/client/src/client_actor.rs (L2133-2146)
```rust
impl Handler<SpanWrapped<StateHeaderValidationRequest>, Result<(), near_chain::Error>>
    for ClientActor
{
    fn handle(
        &mut self,
        msg: SpanWrapped<StateHeaderValidationRequest>,
    ) -> Result<(), near_chain::Error> {
        let msg = msg.span_unwrap();
        self.client.chain.state_sync_adapter.set_state_header(
            msg.shard_id,
            msg.sync_hash,
            msg.header,
        )
    }
```

**File:** chain/client/src/sync/state/downloader.rs (L65-89)
```rust
            let attempt = || {
                async {
                    let header = source
                        .download_shard_header(shard_id, sync_hash, handle.clone(), cancel.clone())
                        .await?;
                    // We cannot validate the header with just a Store. We need the Chain, so we queue it up
                    // so the chain can pick it up later, and we await until the chain gives us a response.
                    handle.set_status("Waiting for validation");
                    validation_sender
                        .send_async(
                            StateHeaderValidationRequest {
                                shard_id,
                                sync_hash,
                                header: header.clone(),
                            }
                            .span_wrap(),
                        )
                        .await
                        .map_err(|_| {
                            near_chain::Error::Other(
                                "Validation request could not be handled".to_owned(),
                            )
                        })??;
                    Ok::<ShardStateSyncResponseHeader, near_chain::Error>(header)
                }
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

**File:** chain/chain/src/state_sync/adapter.rs (L476-503)
```rust
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

**File:** core/primitives/src/block.rs (L815-822)
```rust
    pub fn compute_chunk_headers_root(&self) -> (CryptoHash, Vec<MerklePath>) {
        merklize(
            &self
                .iter()
                .map(|chunk| ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()))
                .collect_vec(),
        )
    }
```

**File:** core/primitives/src/state_sync.rs (L19-20)
```rust
#[derive(PartialEq, Eq, Clone, Debug, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct StateHeaderKey(pub ShardId, pub CryptoHash);
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

**File:** chain/chain/src/chain.rs (L2699-2730)
```rust
    pub fn set_state_finalize(
        &mut self,
        shard_id: ShardId,
        sync_hash: CryptoHash,
    ) -> Result<(), Error> {
        let shard_state_header = self.state_sync_adapter.get_state_header(shard_id, sync_hash)?;
        let mut height = shard_state_header.chunk_height_included();
        let mut chain_update = self.chain_update();
        let shard_uid = chain_update.set_state_finalize(shard_id, sync_hash, shard_state_header)?;
        chain_update.commit()?;

        // We restored the state on height `shard_state_header.chunk.header.height_included`.
        // Now we should build a chain up to height of `sync_hash` block.
        loop {
            height += 1;
            let mut chain_update = self.chain_update();
            // Result of successful execution of set_state_finalize_on_height is bool,
            // should we commit and continue or stop.
            if chain_update.set_state_finalize_on_height(height, shard_id, sync_hash)? {
                chain_update.commit()?;
            } else {
                break;
            }
        }

        let flat_storage_manager = self.runtime_adapter.get_flat_storage_manager();
        if let Some(flat_storage) = flat_storage_manager.get_flat_storage_for_shard(shard_uid) {
            let header = self.get_block_header(&sync_hash)?;
            flat_storage.update_flat_head(header.prev_hash()).unwrap();
        }

        Ok(())
```
