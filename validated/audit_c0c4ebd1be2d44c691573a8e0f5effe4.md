## Analysis

I have read the full `set_state_header` implementation and all related code. Here is the complete finding.

---

### Title
Missing `chunk.shard_id() == shard_id` Guard in `set_state_header` Allows Cross-Shard State Root Substitution — (`chain/chain/src/state_sync/adapter.rs`)

### Summary

`set_state_header` accepts a peer-supplied `ShardStateSyncResponseHeader` and stores it under `StateHeaderKey(shard_id, sync_hash)` using the **caller-supplied** `shard_id`. It never asserts that the embedded chunk's own shard identity matches that argument. A malicious state-sync provider can supply a header whose `chunk` belongs to shard 1 while the call uses `shard_id=0`, pass every validation gate, and persist the wrong chunk under shard 0's key. Every subsequent `set_state_part` call for shard 0 then validates parts against shard 1's `prev_state_root`, corrupting the syncing node's state.

---

### Finding Description

**Entrypoint and data flow**

The syncing node's `StateSyncDownloader::ensure_shard_header` downloads a `ShardStateSyncResponseHeader` from a peer and sends it to `ClientActor` as a `StateHeaderValidationRequest { shard_id, sync_hash, header }`. [1](#0-0) 

`ClientActor` forwards it directly to `ChainStateSyncAdapter::set_state_header(msg.shard_id, msg.sync_hash, msg.header)`. [2](#0-1) 

**The five validation steps and what each one does NOT check**

```
set_state_header(shard_id=0, sync_hash, header_with_shard1_chunk)
```

1. **`validate_chunk_proofs(&chunk, epoch_manager)`** — verifies the chunk's internal hash, tx root, and receipts root. It never reads `chunk.shard_id()`. [3](#0-2) 

2. **`verify_path(chunk_headers_root, chunk_proof, &ChunkHashHeight(chunk.chunk_hash(), ...))`** — verifies that the chunk hash appears somewhere in the block's merkle tree. The proof path encodes the leaf's position (shard 1's index), but the code never checks that this position equals `shard_id` (0). A proof for shard 1's slot passes. [4](#0-3) 

3. **`verify_path` for `prev_chunk`** — same structural issue; position is not compared to `shard_id`. [5](#0-4) 

4. **Receipt proof loop** — computes `receipts_hash = hash(ReceiptList(shard_id, receipts))` using the **caller-supplied** `shard_id=0`. The attacker populates `receipts` with the real shard 0 incoming receipts (public on-chain data) and supplies the corresponding chain-anchored merkle proofs. All checks pass. [6](#0-5) 

5. **`validate_state_root_node(state_root_node, chunk_inner.prev_state_root())`** — checks internal consistency of the state root node against the chunk's `prev_state_root`. Uses shard 1's `prev_state_root`; no shard identity check. [7](#0-6) 

**Storage with wrong key**

After all five checks pass, the header is stored under `StateHeaderKey(shard_id=0, sync_hash)` — but the embedded chunk belongs to shard 1. [8](#0-7) 

**Downstream corruption in `set_state_part`**

`set_state_part` reads the stored header for `(shard_id=0, sync_hash)`, extracts `state_root = chunk.take_header().take_inner().prev_state_root()` — which is shard 1's `prev_state_root` — and validates every incoming part against it. [9](#0-8) 

The attacker then supplies shard 1's state parts, which validate correctly against shard 1's state root and are stored under `StatePartKey(sync_hash, shard_id=0, part_id)`. The syncing node installs shard 1's trie as shard 0's state.

---

### Impact Explanation

A syncing node that accepts this crafted header will:
- Store shard 1's trie data as shard 0's committed state.
- Apply shard 1's chunk (transactions, receipts) as if it were shard 0's chunk during `set_state_finalize`.
- Diverge from the canonical chain for all shard 0 accounts, producing wrong state roots and failing block validation — or, if the attacker controls the only available snapshot, silently operating on corrupted state.

---

### Likelihood Explanation

Any node can advertise a state snapshot (no validator privilege required). The attacker only needs:
1. A valid block at `sync_hash` (public).
2. Shard 1's chunk and its merkle proof (public on-chain data).
3. Shard 0's incoming receipts and their merkle proofs (public on-chain data).

No cryptographic secret is required. The attack is fully constructible from public chain data by any peer that has synced the chain.

---

### Recommendation

Add an explicit shard identity guard at the top of `set_state_header`, immediately after extracting the chunk:

```rust
let chunk = shard_state_header.cloned_chunk();
// NEW: reject headers whose embedded chunk belongs to a different shard
if chunk.shard_id() != shard_id {
    return Err(Error::Other(
        "set_shard_state failed: chunk shard_id does not match requested shard_id".into(),
    ));
}
```

Apply the same guard to `prev_chunk_header` if present.

---

### Proof of Concept

```
1. Obtain sync_hash for an epoch boundary block.
2. From the chain, extract:
   - shard_1_chunk and its merkle proof at index 1 in sync_prev_block.chunk_headers_root
   - shard_1_prev_chunk_header and its merkle proof
   - shard_0 incoming receipts and their root_proofs (chain-anchored)
   - state_root_node consistent with shard_1_chunk.prev_state_root()
3. Construct ShardStateSyncResponseHeaderV2 {
       chunk: shard_1_chunk,
       chunk_proof: <proof at index 1>,
       prev_chunk_header: shard_1_prev_chunk_header,
       prev_chunk_proof: <proof at index 1 in prev block>,
       incoming_receipts_proofs: <shard_0 receipts with valid chain proofs>,
       root_proofs: <matching root_proofs for shard_0>,
       state_root_node: <valid node for shard_1's prev_state_root>,
   }
4. Call set_state_header(shard_id=0, sync_hash, crafted_header).
   → All five checks pass; StateHeaderKey(0, sync_hash) is written with shard_1's chunk.
5. Assert store.get_ser(DBCol::StateHeaders, StateHeaderKey(0, sync_hash))
   returns a header whose chunk.shard_id() == 1.
6. Supply shard_1's state parts via set_state_part(shard_id=0, ...).
   → Parts validate against shard_1's prev_state_root and are accepted.
7. Observe that the syncing node's shard 0 trie now contains shard 1's state.
```

### Citations

**File:** chain/client/src/sync/state/downloader.rs (L65-88)
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
