The key question is whether `verify_path` in `set_state_header` enforces that the chunk is at the position corresponding to `shard_id`. Let me confirm the `verify_path` implementation and the full call chain.

### Title
Missing `chunk.shard_id() == shard_id` guard in `set_state_header` allows cross-shard state root substitution - (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`ChainStateSyncAdapter::set_state_header` accepts a `ShardStateSyncResponseHeader` whose embedded chunk belongs to shard S' ≠ S when called with `shard_id = S`. The Merkle-inclusion check uses `verify_path` (position-blind), not `verify_path_with_index` (position-aware), so a valid proof for S' at its own index passes the check. No explicit `chunk.shard_id() == shard_id` guard exists anywhere in the function. The header is then persisted under `StateHeaderKey(S, H)` carrying S'.`prev_state_root`, and all subsequent `set_state_part` calls validate and install S' trie data under S's `shard_uid`, corrupting the committed state root for shard S.

### Finding Description

**Entrypoint and call chain**

Any network peer can send `PeerMessage::VersionedStateResponse` carrying a crafted `ShardStateSyncResponseHeader`. The message is forwarded without peer-identity filtering:

```
PeerMessage::VersionedStateResponse → StateResponseReceived (client_actor.rs:1198-1209)
  → StateSync::apply_peer_message (mod.rs:172-179)
  → StateSyncDownloader::ensure_shard_header (downloader.rs:44-131)
  → StateHeaderValidationRequest → ClientActor handler (client_actor.rs:2133-2146)
  → ChainStateSyncAdapter::set_state_header (adapter.rs:368-531)
``` [1](#0-0) [2](#0-1) 

**The missing shard-id guard**

`set_state_header` extracts the chunk from the header but never asserts `chunk.shard_id() == shard_id`:

```rust
let chunk = shard_state_header.cloned_chunk();
// ...
if !validate_chunk_proofs(&chunk, self.epoch_manager.as_ref())? { ... }
```

`validate_chunk_proofs` only checks internal consistency (hash, tx root, receipts root). It does not inspect `chunk.shard_id()`. [3](#0-2) [4](#0-3) 

**`verify_path` is position-blind**

The Merkle-inclusion check at step 3a is:

```rust
if !verify_path(
    *sync_prev_block_header.chunk_headers_root(),
    shard_state_header.chunk_proof(),
    &ChunkHashHeight(chunk.chunk_hash().clone(), chunk.height_included()),
) { ... }
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

It only checks that the item hashes to the root via the supplied path. It does **not** check which leaf index the path corresponds to. The position-aware variant `verify_path_with_index` (which calls `verify_path_matches_index`) exists but is not used here. [5](#0-4) [6](#0-5) 

A valid Merkle proof for chunk S' at position j in the block's `chunk_headers_root` tree will pass `verify_path` regardless of whether j corresponds to shard S or shard S'. The code never checks that j == `shard_layout.get_shard_index(shard_id)`.

**Receipt proof check uses the requested `shard_id`, not `chunk.shard_id()`**

```rust
let receipts_hash = CryptoHash::hash_borsh(ReceiptList(shard_id, receipts));
```

The attacker supplies receipt proofs for shard S (the target), which are real chain data they can observe. These pass independently of the chunk's actual shard. [7](#0-6) 

**State root node check uses S'.`prev_state_root`**

```rust
let chunk_inner = chunk.take_header().take_inner();
// validate_state_root_node checks state_root_node against chunk_inner.prev_state_root()
```

The attacker provides a valid `state_root_node` for S'.`prev_state_root`. This check passes. [8](#0-7) 

**Persistence under the wrong key**

```rust
let key = borsh::to_vec(&StateHeaderKey(shard_id, sync_hash)).unwrap();
store_update.set_ser(DBCol::StateHeaders, &key, &shard_state_header);
```

The header — containing S'.`prev_state_root` — is stored under `StateHeaderKey(S, H)`. [9](#0-8) 

**`set_state_part` propagates the wrong state root**

```rust
let shard_state_header = self.get_state_header(shard_id, sync_hash)?;
let chunk = shard_state_header.take_chunk();
let state_root = *chunk.take_header().take_inner().prev_state_root(); // S'.prev_state_root
// ...
self.runtime_adapter.validate_state_part(shard_id, &state_root, part_id, part)
```

Parts are validated against S'.`prev_state_root` and stored under `StatePartKey(H, S, part_id)`. S' trie data is installed under S's `shard_uid`. [10](#0-9) 

**`set_state_finalize` applies S' chunk data to S's `shard_uid`**

```rust
let shard_uid = shard_id_to_uid(self.epoch_manager.as_ref(), shard_id, block_header.epoch_id())?;
// ...
RuntimeStorageConfig::new(chunk_header.prev_state_root(), true) // S'.prev_state_root
``` [11](#0-10) 

### Impact Explanation

A syncing node that accepts the crafted header will:
1. Store `StateHeaderKey(S, H)` → header with S'.`prev_state_root`
2. Accept and store state parts that are valid for S' but keyed to S
3. On `set_state_finalize`, apply S' chunk data to S's `shard_uid` with S'.`prev_state_root`

The result is that shard S's committed state root is replaced with S'.`prev_state_root` and its trie contains S' data. The node will subsequently produce incorrect chunk execution results for shard S, diverge from the canonical chain, and be unable to participate correctly in consensus or serve correct RPC responses for shard S accounts.

### Likelihood Explanation

The attack requires:
- A network peer connection (no special privileges)
- Knowledge of a valid `sync_hash` (publicly observable)
- A valid chunk for shard S' in the relevant block (publicly observable)
- Valid receipt proofs for shard S (publicly observable chain data)
- A valid `state_root_node` for S'.`prev_state_root` (obtainable from any honest state sync provider)

All inputs are obtainable by any observer of the NEAR network. The attack is targeted at nodes currently performing state sync (a well-known, observable condition).

### Recommendation

Add an explicit shard-id guard immediately after extracting the chunk in `set_state_header`:

```rust
let chunk = shard_state_header.cloned_chunk();
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk.shard_id() does not match requested shard_id".into(),
    ));
}
```

Additionally, replace the `verify_path` call at step 3a with `verify_path_with_index`, supplying `shard_layout.get_shard_index(shard_id)` as the expected leaf index, to cryptographically bind the chunk to its position in the block. [12](#0-11) 

### Proof of Concept

In a test with two shards (S=0, S'=1):

1. Obtain a valid `ShardStateSyncResponseHeader` for shard S'=1 at `sync_hash=H` from an honest node.
2. Call `chain.state_sync_adapter.set_state_header(ShardId::new(0), H, header_for_shard_1)`.
3. Assert the call returns `Ok(())` — it will, because no `chunk.shard_id() == shard_id` check exists.
4. Read back `chain.state_sync_adapter.get_state_header(ShardId::new(0), H)` and assert `header.chunk.shard_id() == 1` while the key is for shard 0.
5. Proceed to call `set_state_part` for shard 0 with parts valid for shard 1's state root — they will be accepted.
6. Call `set_state_finalize(ShardId::new(0), H)` and observe that shard 0's `ChunkExtra` now carries shard 1's `prev_state_root`.

### Citations

**File:** chain/network/src/peer_manager/network_state/mod.rs (L1198-1209)
```rust
            PeerMessage::VersionedStateResponse(info) => {
                self.client
                    .send_async(
                        StateResponseReceived {
                            peer_id,
                            state_response: StateResponse::State(info.into()),
                        }
                        .span_wrap(),
                    )
                    .await
                    .ok();
                None
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

**File:** chain/chain/src/state_sync/adapter.rs (L512-529)
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

**File:** core/primitives/src/merkle.rs (L112-129)
```rust
/// Verify merkle path for given item and corresponding path.
pub fn verify_path<T: BorshSerialize>(root: MerkleHash, path: &MerklePath, item: T) -> bool {
    verify_hash(root, path, CryptoHash::hash_borsh(item))
}

pub fn verify_hash(root: MerkleHash, path: &MerklePath, item_hash: MerkleHash) -> bool {
    compute_root_from_path(path, item_hash) == root
}

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
