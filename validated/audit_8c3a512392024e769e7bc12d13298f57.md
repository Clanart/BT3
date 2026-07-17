Looking at the full call chain and validation logic in `set_state_header`, I need to trace every guard carefully before rendering a verdict.

### Title
Missing `chunk.shard_id()` == `shard_id` Guard in `set_state_header` Allows Cross-Shard State Root Corruption — (`chain/chain/src/state_sync/adapter.rs`)

---

### Summary

`ChainStateSyncAdapter::set_state_header` accepts a `ShardStateSyncResponseHeader` whose embedded `ShardChunk` may belong to a different shard than the `shard_id` argument. Because the only Merkle-inclusion check is against `chunk_headers_root` — which covers **all** shards — a chunk for shard 1 carries a valid proof even when `shard_id=0` is requested. The header is then persisted under `StateHeaderKey(0, sync_hash)` with shard 1's `chunk_prev_state_root`, and subsequent `set_state_part` calls validate and install shard 1's trie data into shard 0's storage.

---

### Finding Description

**Missing guard (confirmed by grep — zero occurrences of `chunk.shard_id()` in `adapter.rs`):**

`set_state_header` performs five checks before writing to the DB:

1. `validate_chunk_proofs(&chunk, ...)` — internal chunk signature/proofs [1](#0-0) 
2. `verify_path(*sync_prev_block_header.chunk_headers_root(), chunk_proof, &ChunkHashHeight(...))` — proves the chunk hash is in the block's Merkle tree, but this tree covers **every** shard [2](#0-1) 
3. `prev_chunk` inclusion check — same tree, same issue [3](#0-2) 
4. Receipt-proof loop — uses the outer `shard_id` parameter, not `chunk.shard_id()` [4](#0-3) 
5. `validate_state_root_node(state_root_node, chunk_inner.prev_state_root())` — validates the node against the chunk's own root, regardless of which shard that chunk belongs to [5](#0-4) 

**No check of the form `chunk.shard_id() == shard_id` exists anywhere in the function.**

The header is then stored under the caller-supplied key:

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
``` [6](#0-5) 

**Downstream propagation — `set_state_part` trusts the stored header:**

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root();
// state_root is now shard 1's root; parts for shard 1 pass validation
self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part)
``` [7](#0-6) 

---

### Impact Explanation

An attacker who is selected as the state sync provider for shard 0 can:

1. Supply a `ShardStateSyncResponseHeader` with outer `shard_id=0` but an inner `ShardChunk` for shard 1 (valid Merkle proof against `chunk_headers_root`).
2. Supply real receipt proofs for shard 0 (public chain data) — these satisfy check 4 because the loop hashes `ReceiptList(shard_id=0, receipts)`, independent of the chunk's shard. [8](#0-7) 
3. Supply the real `StateRootNode` for shard 1's trie root — satisfies check 5.
4. `StateHeaderKey(0, sync_hash)` is written with shard 1's `prev_state_root`.
5. Attacker then provides state parts valid for shard 1's state root; `validate_state_part` passes; `apply_state_part` installs shard 1's trie data into shard 0's storage.

The concrete corrupted value: `chunk_prev_state_root` stored under `StateHeaderKey(0, sync_hash)` is shard 1's state root, and shard 1's trie nodes are written into shard 0's `ShardUId` namespace.

---

### Likelihood Explanation

**Attacker privilege required: none beyond being a connected peer.**

Any peer can advertise itself as a snapshot host by sending a `SyncSnapshotHosts` message signed with its own node key — no validator or operator role is needed. [9](#0-8) 

The victim selects a provider randomly from `hosts_for_shard`: [10](#0-9) 

The `pending_requests` guard in `receive_peer_message` requires the response to come from the selected peer and carry the correct outer `shard_id` — both conditions are satisfied by the attacker (they are the selected peer, and they set `shard_id=0` in `StateResponseInfoV2`). [11](#0-10) 

The `StateHeaderValidationRequest` path from `ClientActor` passes `msg.shard_id` and `msg.header` directly to `set_state_header` with no additional shard cross-check. [12](#0-11) 

---

### Recommendation

Add an explicit shard identity check at the top of `set_state_header`, immediately after extracting the chunk:

```rust
let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

This should be placed before any other validation at line 376 in `chain/chain/src/state_sync/adapter.rs`. [13](#0-12) 

---

### Proof of Concept

```rust
// In a cargo integration test (using TestEnv or similar):
// 1. Obtain a real shard-1 ShardStateSyncResponseHeader for sync_hash.
// 2. Call set_state_header with shard_id=0 but the shard-1 header.
let shard1_header = env.clients[0]
    .chain.state_sync_adapter
    .get_state_response_header(ShardId::new(1), sync_hash)
    .unwrap();

// This should fail but currently succeeds:
env.clients[1]
    .chain.state_sync_adapter
    .set_state_header(ShardId::new(0), sync_hash, shard1_header)
    .expect("set_state_header must reject mismatched shard_id");

// Assert the stored header has the wrong prev_state_root:
let stored = env.clients[1]
    .chain.state_sync_adapter
    .get_state_header(ShardId::new(0), sync_hash)
    .unwrap();
// stored.chunk_prev_state_root() == shard 1's state root  ← corruption confirmed
assert_ne!(
    stored.chunk_prev_state_root(),
    correct_shard0_state_root,
    "StateHeaderKey(0, sync_hash) contains shard 1's state root"
);
```

### Citations

**File:** chain/chain/src/state_sync/adapter.rs (L376-377)
```rust
        let chunk = shard_state_header.cloned_chunk();
        let prev_chunk_header = shard_state_header.cloned_prev_chunk_header();
```

**File:** chain/chain/src/state_sync/adapter.rs (L379-385)
```rust
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

**File:** chain/chain/src/state_sync/adapter.rs (L412-436)
```rust
        match (&prev_chunk_header, shard_state_header.prev_chunk_proof()) {
            (Some(prev_chunk_header), Some(prev_chunk_proof)) => {
                let prev_block_header =
                    self.chain_store.get_block_header(block_header.prev_hash())?;
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
            }
            (None, None) => {
                if chunk.height_included() != 0 {
                    return Err(Error::Other(
                    "set_shard_state failed: received empty state response for a chunk that is not at height 0".into()
                ));
                }
            }
            _ =>
                return Err(Error::Other("set_shard_state failed: `prev_chunk_header` and `prev_chunk_proof` must either both be present or both absent".into()))
        };
```

**File:** chain/chain/src/state_sync/adapter.rs (L488-492)
```rust
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

**File:** chain/network/src/snapshot_hosts/mod.rs (L183-190)
```rust
    fn add_to_shard_hosts(&mut self, info: &SnapshotHostInfo) {
        if self.current_state_sync_hash.as_ref() != Some(&info.sync_hash) {
            return;
        }
        for shard_id in &info.shards {
            self.hosts_for_shard.entry(*shard_id).or_default().insert(info.peer_id.clone());
        }
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L256-263)
```rust
    pub fn select_host_for_header(
        &mut self,
        sync_hash: &CryptoHash,
        shard_id: ShardId,
    ) -> Option<PeerId> {
        self.update_current_state_sync_hash(sync_hash);
        self.hosts_for_shard.get(&shard_id)?.iter().choose(&mut thread_rng()).cloned()
    }
```

**File:** chain/client/src/sync/state/network.rs (L90-101)
```rust
        let key = PendingPeerRequestKey { shard_id, sync_hash: msg.sync_hash(), part_id_or_header };

        let Some(request) = self.pending_requests.get_mut(&key) else {
            tracing::debug!(target: "sync", ?key, %peer_id, "unexpected state response, request may have timed out");
            return Ok(());
        };

        if request.peer_id != peer_id {
            return Err(near_chain::Error::Other(
                "Unexpected state response (wrong sender)".to_owned(),
            ));
        }
```

**File:** chain/client/src/client_actor.rs (L2141-2146)
```rust
        self.client.chain.state_sync_adapter.set_state_header(
            msg.shard_id,
            msg.sync_hash,
            msg.header,
        )
    }
```
