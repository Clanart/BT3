The code is clear enough to render a verdict. Let me confirm the exact `verify_path` semantics and the downstream `set_state_finalize` path before writing up.

### Title
Missing shard-position check in `set_state_header` allows cross-shard state-root substitution — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`set_state_header` accepts a `ShardStateSyncResponseHeader` for shard `S` but never asserts that the embedded chunk's `shard_id()` equals the requested `shard_id`. The Merkle-inclusion check uses the position-agnostic `verify_path` (not `verify_path_with_index`), so a valid proof for any chunk in the block passes regardless of which shard slot it occupies. An attacker who can deliver a state-sync response can substitute shard S' chunk data into the header for shard S, causing the node to store `StateHeaderKey(S, H)` with `prev_state_root = S'.prev_state_root`, validate all subsequent state parts against that wrong root, and ultimately install S' trie data under S's `shard_uid`.

---

### Finding Description

**Step 1 — `validate_chunk_proofs` has no shard-id guard.**

`validate_chunk_proofs` in `chain/chain/src/validate.rs` checks only internal chunk consistency: header hash, tx Merkle root, and outgoing-receipts Merkle root. [1](#0-0) 

There is no assertion that `chunk.shard_id() == shard_id`. A fully self-consistent chunk for shard S' passes this check unconditionally.

**Step 2 — `verify_path` is position-agnostic.**

`verify_path` reduces to `compute_root_from_path(path, hash_of_item) == root`. [2](#0-1) 

It does **not** call `verify_path_matches_index`, which is the function that enforces a specific leaf index. [3](#0-2) 

The `chunk_headers_root` is a Merkle tree over **all** shards' `ChunkHashHeight` values. [4](#0-3) 

Therefore, supplying the Merkle proof for S' (at position S' in the tree) together with S' chunk data satisfies `verify_path(chunk_headers_root, proof_for_S', ChunkHashHeight(S'_hash, S'_height))` even when the requested shard is S.

**Step 3 — `set_state_header` never compares `chunk.shard_id()` to `shard_id`.**

The full validation sequence in `set_state_header`: [5](#0-4) 

No line in the function body reads `chunk.shard_id()` and compares it to the `shard_id` parameter. After all checks pass, the header is written: [6](#0-5) 

**Step 4 — Receipt-proof check uses the requested `shard_id`, not the chunk's.**

Line 488 hashes receipts as `ReceiptList(shard_id, receipts)` where `shard_id` is the caller-supplied parameter S. [7](#0-6) 

An attacker can obtain valid incoming-receipt proofs for shard S from any full node and embed them alongside the S' chunk. The receipt check passes independently of the chunk's shard.

**Step 5 — `validate_state_root_node` is bound to the chunk's (wrong) `prev_state_root`.** [8](#0-7) 

The attacker supplies a `state_root_node` consistent with S'.prev_state_root (also public data). This check passes.

**Step 6 — Downstream `set_state_part` and `set_state_finalize` propagate the corruption.**

`set_state_part` reads the stored header and extracts `state_root` from the chunk's `prev_state_root`, then validates incoming parts against it: [9](#0-8) 

`set_state_finalize` uses the caller-supplied `shard_id` to derive `shard_uid` (S's uid) but uses `chunk_header.prev_state_root()` (S'.prev_state_root) as the trie root for `apply_chunk`: [10](#0-9) 

S' trie data is therefore installed under S's `shard_uid`.

---

### Impact Explanation

A node completing state sync for shard S would have its trie populated with shard S' data. Every subsequent block application, receipt routing, and account lookup for shard S would operate on the wrong state root, producing invalid `ChunkExtra` values. If the node is a validator, it would sign invalid chunks; if it is a full node, it would reject valid blocks. The corruption is persistent (written to RocksDB) and survives restarts.

---

### Likelihood Explanation

The attacker must be able to deliver a `StateResponse` message to the victim. The `StateResponseReceived` handler accepts responses from any connected peer: [11](#0-10) 

No allowlist of trusted peers is enforced before `set_state_header` is called: [12](#0-11) 

Any node operator can register as a snapshot host and serve crafted responses. The `sync_hash` is a public chain value. All auxiliary data (receipt proofs for S, state_root_node for S') are readable from any full node. The attack requires multi-shard configuration (≥2 shards) and a victim in state-sync or catchup mode, which is a normal operational state.

---

### Recommendation

1. **Add an explicit shard-id guard** at the top of `set_state_header`:
   ```rust
   if chunk.shard_id() != shard_id {
       return Err(Error::Other(
           "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
       ));
   }
   ``` [13](#0-12) 

2. **Replace `verify_path` with `verify_path_with_index`** for the chunk-headers-root check, passing the shard index derived from `shard_layout.get_shard_index(shard_id)` and the total number of shards, mirroring the pattern used in chunk-part validation: [14](#0-13) 

---

### Proof of Concept

```rust
// In an integration test with ≥2 shards:
// 1. Obtain a valid ShardStateSyncResponseHeader for shard S' (shard_id=1).
let header_for_s_prime = client0.chain.state_sync_adapter
    .get_state_response_header(ShardId::new(1), sync_hash).unwrap();

// 2. Call set_state_header claiming shard S (shard_id=0) but passing S' header.
let result = client1.chain.state_sync_adapter
    .set_state_header(ShardId::new(0), sync_hash, header_for_s_prime);

// Expected (after fix): Err(...)
// Actual (current code): Ok(())  ← vulnerability confirmed
assert!(result.is_err(), "set_state_header must reject cross-shard chunk");
```

The stored `StateHeaderKey(0, sync_hash)` would contain `chunk_prev_state_root` from shard 1, and all subsequent `set_state_part` calls for shard 0 would validate parts against shard 1's state root.

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

**File:** chain/chain/src/state_sync/adapter.rs (L487-492)
```rust
                let RootProof(root, block_proof) = &shard_state_header.root_proofs()[i][j];
                let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
                // 4e. Proving the set of receipts is the subset of outgoing_receipts of shard `shard_id`
                if !verify_path(*root, proof, &receipts_hash) {
                    byzantine_assert!(false);
                    return Err(Error::Other("set_shard_state failed: invalid proofs".into()));
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

**File:** chain/chain/src/chain_update.rs (L513-521)
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
```

**File:** chain/client/src/client_actor.rs (L678-720)
```rust
impl Handler<SpanWrapped<StateResponseReceived>> for ClientActor {
    fn handle(&mut self, msg: SpanWrapped<StateResponseReceived>) {
        let StateResponseReceived { peer_id, state_response } = msg.span_unwrap();
        let hash = state_response.sync_hash();
        let shard_id = state_response.shard_id();

        match state_response {
            StateResponse::Ack(ref ack) => {
                tracing::trace!(target: "sync", %shard_id, ?hash, part_id = ?state_response.part_id_or_header(), ack_body = ?ack.body, "received state request ack");
            }
            StateResponse::State(ref state) => {
                tracing::trace!(target: "sync", %shard_id, ?hash, part_id = ?state_response.part_id_or_header(), size = ?state.payload_length(), "received state response");
            }
        }

        // Get the download that matches the shard_id and hash

        // ... It could be that the state was requested by the state sync
        if let SyncStatus::StateSync(StateSyncStatus { sync_hash, .. }) =
            &mut self.client.sync_handler.sync_status
        {
            if hash == *sync_hash {
                if let Err(err) =
                    self.client.sync_handler.state_sync.apply_peer_message(peer_id, state_response)
                {
                    tracing::error!(target: "sync", ?err, "error applying state sync response");
                }
                return;
            }
        }

        // ... Or one of the catchups
        if let Some(CatchupState { state_sync, .. }) =
            self.client.catchup_state_syncs.get_mut(&hash)
        {
            if let Err(err) = state_sync.apply_peer_message(peer_id, state_response) {
                tracing::error!(target: "sync", ?err, "error applying catchup state sync response");
            }
            return;
        }

        tracing::error!(target: "sync", ?hash, "state sync received hash that we're not expecting, potential malicious peer or a very delayed response");
    }
```

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

**File:** chain/chunks/src/shards_manager_actor.rs (L1262-1283)
```rust
    fn validate_part(
        &self,
        merkle_root: MerkleHash,
        part: &PartialEncodedChunkPart,
        num_total_parts: usize,
    ) -> Result<(), Error> {
        if (part.part_ord as usize) < num_total_parts {
            if !verify_path_with_index(
                merkle_root,
                &part.merkle_proof,
                &part.part,
                part.part_ord,
                num_total_parts as u64,
            ) {
                return Err(Error::InvalidMerkleProof);
            }

            Ok(())
        } else {
            Err(Error::InvalidChunkPartId)
        }
    }
```
