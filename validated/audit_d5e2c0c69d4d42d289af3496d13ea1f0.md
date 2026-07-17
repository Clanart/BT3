Looking at the core validation logic in `set_state_header` and the structure of the merkle tree it validates against, I need to carefully evaluate whether the Pigeonhole argument holds.

## Analysis

**The Pigeonhole argument is structurally broken.** Here is the precise chain of reasoning.

---

### The Merkle Tree Universe Is Larger Than `chunks_included`

The code comment at lines 470–474 claims the Pigeonhole principle guarantees completeness:

> "We know there were exactly `block_header.chunks_included` chunks included… According to Pigeonhole principle, it's enough to ensure all receipt_proofs are distinct to prove that all receipts were received and no receipts were hidden."

For this to hold, the universe of valid `from_shard_id` values that can produce a passing check 4f must be exactly `chunks_included` in size. It is not.

`prev_chunk_outgoing_receipts_root` is computed over **all** chunks — new and missing alike: [1](#0-0) 

The merkle tree has `num_shards` leaves, not `chunks_included` leaves. A missing chunk's `prev_outgoing_receipts_root` is a valid leaf in this tree and produces a passing `verify_path` call in check 4f. [2](#0-1) 

### The `from_shard_id` Is Never Verified Against `chunk_mask`

Check 4d only enforces uniqueness of `from_shard_id`: [3](#0-2) 

There is no check that `from_shard_id` corresponds to a shard where `chunk_mask[from_shard_index] == true`. A missing chunk's shard ID is a perfectly valid, distinct value.

### The Concrete Attack

**Setup**: 3 shards S0, S1, S2. Block B has:
- S0: new chunk (`chunk_mask[0]=true`), sent receipts R to `shard_id`
- S1: new chunk (`chunk_mask[1]=true`), sent receipts R′ (non-zero) to `shard_id`
- S2: missing chunk (`chunk_mask[2]=false`), whose previous chunk committed to zero receipts for `shard_id`
- `chunks_included = 2`

**Attacker supplies**:
- Proof A: `from_shard_id=S0`, `receipts=R`, valid inner proof against S0's `prev_outgoing_receipts_root`, valid `block_proof` for S0's leaf in the block merkle tree
- Proof B: `from_shard_id=S2`, `receipts=[]`, valid inner proof against S2's `prev_outgoing_receipts_root` (which commits to zero receipts for `shard_id`), valid `block_proof` for S2's leaf in the block merkle tree

**Checks that pass**:
- 4c: `receipt_proofs.len() == 2 == chunks_included` ✓
- 4d: `{S0, S2}` are distinct ✓
- 4e: Both inner proofs verify against their respective roots ✓
- 4f: Both `block_proof`s verify against `prev_chunk_outgoing_receipts_root` ✓ (S2's leaf is in the `num_shards`-wide tree) [4](#0-3) 

**What is missing**: S1's receipts R′ are never included. The header is stored to `DBCol::StateHeaders`.

### Impact in `set_state_finalize`

`set_state_finalize` reads the stored header and calls `collect_receipts_from_response` on the attacker-supplied `incoming_receipts_proofs`: [5](#0-4) 

It then passes the incomplete receipt list directly to `apply_chunk`: [6](#0-5) 

The `apply_result.new_root` (state root) produced by applying the chunk without R′ diverges from the canonical state root committed in subsequent chunk headers. The syncing node's state is permanently corrupted for that shard.

---

### Title
Missing `chunk_mask` guard in `set_state_header` receipt-proof completeness check allows unprivileged peer to corrupt syncing node state — (`chain/chain/src/state_sync/adapter.rs`)

### Summary
The Pigeonhole completeness argument in `ChainStateSyncAdapter::set_state_header` is invalid. The merkle tree `prev_chunk_outgoing_receipts_root` has `num_shards` leaves (all chunks, including missing ones), not `chunks_included` leaves. Because `from_shard_id` is only checked for uniqueness and never verified against `chunk_mask`, an attacker can substitute a missing chunk's proof for a new chunk's proof, pass all four validation checks, and cause `set_state_finalize` to apply an incomplete receipt set, producing a divergent state root.

### Finding Description
In `set_state_header` (adapter.rs:463–503), the validation loop checks:
1. `receipt_proofs.len() == chunks_included` (count)
2. `from_shard_id` values are distinct (uniqueness)
3. Each `receipts_hash` verifies against `root` via `verify_path` (inner proof)
4. Each `root` verifies against `block_header.prev_chunk_outgoing_receipts_root()` via `verify_path` (block proof)

The code comment asserts that (1)+(2) together with (3)+(4) constitute a Pigeonhole proof of completeness. This is wrong. The block-level merkle tree is built over **all** `num_shards` chunks' `prev_outgoing_receipts_root` values, including missing chunks. A missing chunk's root is a valid leaf and produces a passing check 4f. Since `from_shard_id` is never cross-referenced against `chunk_mask`, an attacker can supply `chunks_included` distinct `from_shard_id` values that include missing-chunk shard IDs, omitting one or more new chunks that sent non-zero receipts to the target shard.

### Impact Explanation
The corrupted `ShardStateSyncResponseHeader` is stored to `DBCol::StateHeaders`. `set_state_finalize` reads it back, collects receipts from the attacker-controlled `incoming_receipts_proofs`, and passes them to `apply_chunk`. The resulting state root diverges from the canonical chain. The syncing node's `ChunkExtra` for that shard records the wrong state root, causing all subsequent chunk validations for that shard to fail. If the node is a validator, it may produce or endorse chunks based on the wrong state.

### Likelihood Explanation
The attack requires the attacker to act as a state sync provider (a peer from whom the victim requests state). This is an unprivileged role — any peer can serve state sync responses. The attacker needs a block where at least one shard has a missing chunk whose previous chunk committed to zero receipts for the target shard, and at least one new chunk sent non-zero receipts to the target shard. Missing chunks are routine in NEAR. The attacker must construct valid merkle proofs for the missing chunk's leaf, which is straightforward from public block data.

### Recommendation
In the validation loop, after inserting `from_shard_id` into `visited_shard_ids`, verify that `from_shard_id` corresponds to a shard with a new chunk in the block:

```rust
let from_shard_index = shard_layout.get_shard_index(*from_shard_id)?;
if !block_header.chunk_mask().get(from_shard_index).copied().unwrap_or(false) {
    return Err(Error::Other(
        "set_shard_state failed: from_shard_id is not a new chunk".into()
    ));
}
```

This closes the gap between the `chunks_included`-sized universe assumed by the Pigeonhole argument and the `num_shards`-sized universe actually admitted by check 4f.

### Proof of Concept
Construct a 3-shard test network. Produce a block where S0 and S1 have new chunks and S2 has a missing chunk. Arrange for S1 to send at least one receipt to the target shard. Construct a `ShardStateSyncResponseHeader` with:
- `incoming_receipts_proofs[block_B] = [proof_S0, proof_S2]` (omitting S1, substituting S2)
- `root_proofs[block_B] = [RootProof(S0_root, S0_block_proof), RootProof(S2_root, S2_block_proof)]`

Both `S0_block_proof` and `S2_block_proof` are valid merkle paths in the `num_shards`-wide tree. Call `set_state_header` with this header; assert it returns `Ok(())`. Then call `set_state_finalize`; assert the resulting state root differs from the canonical state root for that shard.

### Citations

**File:** core/primitives/src/block.rs (L811-813)
```rust
    pub fn compute_chunk_prev_outgoing_receipts_root(&self) -> CryptoHash {
        merklize(&self.iter().map(|chunk| *chunk.prev_outgoing_receipts_root()).collect_vec()).0
    }
```

**File:** chain/chain/src/state_sync/adapter.rs (L463-503)
```rust
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
```

**File:** chain/chain/src/chain_update.rs (L479-487)
```rust
        let mut receipt_proof_responses: Vec<ReceiptProofResponse> = vec![];
        for incoming_receipt_proof in &incoming_receipts_proofs {
            let ReceiptProofResponse(hash, _) = incoming_receipt_proof;
            let block_header = self.chain_store_update.get_block_header(hash)?;
            if block_header.height() <= chunk.height_included() {
                receipt_proof_responses.push(incoming_receipt_proof.clone());
            }
        }
        let receipts = collect_receipts_from_response(&receipt_proof_responses);
```

**File:** chain/chain/src/chain_update.rs (L519-542)
```rust
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
