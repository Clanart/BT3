Let me analyze the external bug pattern and search for analogs in nearcore. The core issue is: a cached/buffered amount is served without updating the underlying source's "already collected" marker, causing double-collection when a later operation re-collects from the source.

After thorough investigation of the nearcore codebase, I found a valid structural analog.

---

### Title
Outgoing-receipt double-reassignment via `MainStateTransitionCache` during resharding — (`chain/chain/src/stateless_validation/chunk_validation.rs`)

### Summary

`validate_chunk_state_witness_impl` stores **already-resharding-reassigned** `outgoing_receipts` into `MainStateTransitionCache`. On any subsequent cache hit for the same `(witness_chunk_shard_uid, block_hash)` pair, `reassign_outgoing_receipts_for_resharding` is applied a second time to the already-mutated receipts, producing a wrong `outgoing_receipts_root`. This is the exact nearcore analog of the Sushi double-yield bug: a cached value is served without marking it as "already transformed," so the transformation is applied twice.

### Finding Description

In `validate_chunk_state_witness_impl`, the main-transition result is resolved from either a fresh `apply_new_chunk` call or the `MainStateTransitionCache`: [1](#0-0) 

After resolution, when `chunk_shard_layout != witness_shard_layout` (i.e., the witness spans a resharding epoch boundary), `reassign_outgoing_receipts_for_resharding` **mutates** `outgoing_receipts` in place: [2](#0-1) 

Immediately after, the **post-mutation** receipts are written back into the cache: [3](#0-2) 

The cache type is a shared `Arc<Mutex<...>>` used across all witness validations in the actor: [4](#0-3) 

**Sequence of events that triggers the bug:**

1. Witness W for `(witness_chunk_shard_uid, block_hash)` arrives during a resharding epoch boundary where `chunk_shard_layout ≠ witness_shard_layout`.
2. **Cache miss** → `apply_new_chunk` produces original `outgoing_receipts`.
3. `reassign_outgoing_receipts_for_resharding` mutates them (first application).
4. Cache stores the **already-mutated** receipts under `(witness_chunk_shard_uid, block_hash)`.
5. Witness W arrives again (network retransmission, or any peer replaying the signed witness).
6. **Cache hit** → returns the already-mutated receipts.
7. `reassign_outgoing_receipts_for_resharding` is applied **again** (second application) to already-reassigned receipts.
8. `outgoing_receipts_hashes` is computed from the doubly-mutated receipts.
9. The computed `outgoing_receipts_root` does not match the committed root in the chunk header → validation fails or produces a wrong endorsement.

The cache is populated **after** the resharding mutation, but the mutation guard (`if chunk_shard_layout != witness_shard_layout`) is re-evaluated on every call regardless of whether the result came from cache. There is no flag in `ChunkStateWitnessValidationResult` to indicate that the stored receipts are already post-reassignment. [5](#0-4) 

### Impact Explanation

During resharding, chunk validators compute a wrong `outgoing_receipts_root` for any witness that is validated more than once. This causes them to reject valid witnesses with `InvalidChunkStateWitness`, preventing chunk endorsement collection. Without sufficient endorsements, block production stalls at the resharding epoch boundary. The corrupted value is the `outgoing_receipts_hashes` vector at line 645, derived from doubly-reassigned receipts whose shard routing has been applied twice.

### Likelihood Explanation

Resharding epoch boundaries are infrequent but scheduled protocol events. Network retransmissions of the same witness are routine (witnesses are large and travel over unreliable P2P links). Any network participant can replay a captured, validly-signed witness to a validator, triggering the second validation. The `MainStateTransitionCache` holds up to 20 entries per shard, so the poisoned entry persists across multiple retransmissions within the same epoch boundary window. [6](#0-5) 

### Recommendation

Store **pre-reassignment** `outgoing_receipts` in the cache, and always apply `reassign_outgoing_receipts_for_resharding` after retrieval (whether from cache or from fresh application). Alternatively, add a boolean field `receipts_already_reassigned` to `ChunkStateWitnessValidationResult` and skip the reassignment on cache hits. The simplest fix is to move the `cache.put(...)` call to before the resharding reassignment block, so the cache always holds the canonical post-apply, pre-reassignment receipts.

### Proof of Concept

```
Epoch boundary: old_layout (1 shard) → new_layout (2 shards)
witness_chunk_shard_uid = child_shard_0
block_hash = B (last block of old epoch)
chunk_shard_layout = old_layout  ≠  witness_shard_layout = new_layout

Pass 1 (cache miss):
  outgoing_receipts = apply_new_chunk(...)   // receipts routed for old 1-shard layout
  reassign_outgoing_receipts_for_resharding(&mut outgoing_receipts, ...)
  // receipts now routed for child_shard_0 in new 2-shard layout
  cache.put((child_shard_0, B), { outgoing_receipts: <reassigned> })

Pass 2 (cache hit, same witness retransmitted):
  outgoing_receipts = cache.get((child_shard_0, B))  // already reassigned
  reassign_outgoing_receipts_for_resharding(&mut outgoing_receipts, ...)
  // receipts now doubly-reassigned → wrong routing
  outgoing_receipts_hashes = build_receipts_hashes(outgoing_receipts)
  // hash ≠ committed outgoing_receipts_root in chunk header
  → InvalidChunkStateWitness error, endorsement not produced
```

### Citations

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L87-91)
```rust
#[derive(Clone)]
pub struct ChunkStateWitnessValidationResult {
    pub chunk_extra: ChunkExtra,
    pub outgoing_receipts: Vec<Receipt>,
}
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L93-99)
```rust
// TODO: key should be a pair (chunk_shard_uid, witness_shard_uid) for shard merging
pub type MainStateTransitionCache =
    Arc<Mutex<HashMap<ShardUId, LruCache<CryptoHash, ChunkStateWitnessValidationResult>>>>;

/// The number of state witness validation results to cache per shard.
/// This number needs to be small because result contains outgoing receipts, which can be large.
const NUM_WITNESS_RESULT_CACHE_ENTRIES: usize = 20;
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L594-621)
```rust
    let cache_result = {
        let mut shard_cache = main_state_transition_cache.lock();
        shard_cache
            .get_mut(&witness_chunk_shard_uid)
            .and_then(|cache| cache.get(&block_hash).cloned())
    };
    let (mut chunk_extra, mut outgoing_receipts) =
        match (pre_validation_output.main_transition_params, cache_result) {
            (MainTransition::Genesis { chunk_extra, .. }, _) => (chunk_extra, vec![]),
            (MainTransition::NewChunk { new_chunk_data, .. }, None) => {
                let chunk_gas_limit = new_chunk_data.gas_limit;
                let NewChunkResult { apply_result: mut main_apply_result, .. } = apply_new_chunk(
                    ApplyChunkReason::ValidateChunkStateWitness,
                    &span,
                    new_chunk_data,
                    ShardContext { shard_uid, should_apply_chunk: true },
                    runtime_adapter,
                    // Recorded-storage replay; no memtrie path.
                    MaybePinnedMemtrieRoot::no_memtries(),
                    None,
                )?;
                let outgoing_receipts = std::mem::take(&mut main_apply_result.outgoing_receipts);
                let chunk_extra = main_apply_result.to_chunk_extra(chunk_gas_limit);

                (chunk_extra, outgoing_receipts)
            }
            (_, Some(result)) => (result.chunk_extra, result.outgoing_receipts),
        };
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L633-646)
```rust
    // Compute receipt hashes here to avoid copying receipts
    let outgoing_receipts_hashes = {
        let chunk_epoch_id = epoch_manager.get_epoch_id(&block_hash)?;
        let chunk_shard_layout = epoch_manager.get_shard_layout(&chunk_epoch_id)?;
        if chunk_shard_layout != witness_shard_layout {
            ChainStore::reassign_outgoing_receipts_for_resharding(
                &mut outgoing_receipts,
                &witness_shard_layout,
                witness_chunk_shard_id,
                shard_id,
            )?;
        }
        Chain::build_receipts_hashes(&outgoing_receipts, &witness_shard_layout)?
    };
```

**File:** chain/chain/src/stateless_validation/chunk_validation.rs (L647-660)
```rust
    // Save main state transition result to cache.
    {
        let mut shard_cache = main_state_transition_cache.lock();
        let cache = shard_cache.entry(witness_chunk_shard_uid).or_insert_with(|| {
            LruCache::new(NonZeroUsize::new(NUM_WITNESS_RESULT_CACHE_ENTRIES).unwrap())
        });
        cache.put(
            block_hash,
            ChunkStateWitnessValidationResult {
                chunk_extra: chunk_extra.clone(),
                outgoing_receipts: outgoing_receipts.clone(),
            },
        );
    }
```
