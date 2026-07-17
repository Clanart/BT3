### Title
Orphan `ChunkStateWitness` pool entry silently overwritten without signature validation, evicting legitimate witness — (`File: chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs`)

---

### Summary

`OrphanStateWitnessPool::add_orphan_state_witness()` stores witnesses in an `LruCache` keyed by `ChunkProductionKey = (epoch_id, shard_id, height_created)`. `LruCache::push()` unconditionally replaces any existing entry for the same key. Because `handle_orphan_witness()` performs **no signature validation** before insertion, any network peer can send a crafted `ChunkStateWitness` carrying a matching `ChunkProductionKey` to silently evict the legitimate orphan witness. When the legitimate previous block later arrives, `take_state_witnesses_waiting_for_block()` finds no matching witness and the validator never emits a chunk endorsement.

---

### Finding Description

`OrphanStateWitnessPool` is the in-memory holding area for `ChunkStateWitness` objects whose required previous block has not yet been received. It is keyed by `ChunkProductionKey`: [1](#0-0) 

Insertion calls `LruCache::push()`, which **replaces** any existing entry for the same key and returns the evicted entry: [2](#0-1) 

The `ChunkProductionKey` is `(epoch_id, shard_id, height_created)` — it does **not** include `chunk_hash` or `prev_block_hash`. Two witnesses for the same slot on different forks share the same key.

The caller, `handle_orphan_witness()`, performs only two checks before insertion: [3](#0-2) 

1. The witness height is within `ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD` of the chain head.
2. The serialized witness size is within `max_orphan_witness_size`.

**No signature validation is performed.** The doc-comment on `add_orphan_state_witness()` states that signature validation is "expected" to have occurred, but `handle_orphan_witness()` does not enforce this: [4](#0-3) 

Signature validation only occurs later, inside `start_validating_chunk()`, which is reached via `process_chunk_state_witness()` — a path that is **not taken** for orphan witnesses: [5](#0-4) 

When the legitimate block arrives, `take_state_witnesses_waiting_for_block()` searches by `prev_block_hash`: [6](#0-5) 

If the attacker's crafted witness carries a different `prev_block_hash`, the legitimate witness slot is gone and the search returns nothing. The validator never calls `process_chunk_state_witness()` for the legitimate chunk and never emits an endorsement.

The `replacing` unit test explicitly documents and exercises this overwrite behavior: [7](#0-6) 

---

### Impact Explanation

A validator that holds an orphan witness for a chunk will silently lose it and fail to endorse that chunk when the block arrives. If an attacker targets a sufficient number of validators for the same `ChunkProductionKey`, the chunk may fail to accumulate the required endorsement threshold, causing it to be skipped. This is a targeted **liveness attack** on chunk inclusion. The attacker needs no stake and no privileged role — only the ability to send network messages to validators.

**Impact: High** — chunk endorsements are suppressed without any on-chain evidence of the attack.

---

### Likelihood Explanation

- `ChunkProductionKey = (epoch_id, shard_id, height_created)` is entirely public information derivable from the chain state.
- Any network peer can send a `ChunkStateWitness` message; there is no network-layer filtering.
- The attacker only needs to send a structurally valid (but cryptographically invalid) `ChunkStateWitness` whose height falls within the allowed orphan window and whose size is below the cap.
- Timing is straightforward: the attacker sends the crafted witness immediately after observing a new block height, before the next block propagates.
- Cost is gas-free (no on-chain transaction required).

**Likelihood: High** — low cost, no privileges, public key material.

---

### Recommendation

Validate the witness signature inside `handle_orphan_witness()` **before** calling `add_orphan_state_witness()`. The epoch manager already provides `get_chunk_producer_info()` and the witness carries a verifiable signature; the same check performed in `start_validating_chunk()` should be applied here.

As a secondary defense, `add_orphan_state_witness()` should check whether an entry already exists for the key and reject the incoming witness if one is present (analogous to the recommended fix in the external report: revert if the mapping slot is already populated).

---

### Proof of Concept

1. Attacker observes that validator V has received a `ChunkStateWitness` for `ChunkProductionKey{E, S, H}` but the previous block has not yet arrived (the witness is an orphan).
2. Attacker crafts a `ChunkStateWitness` with the same `(epoch_id=E, shard_id=S, height_created=H)` but sets `prev_block_hash` to an arbitrary hash `X ≠ legitimate_prev`. All other fields can be garbage; the signature need not be valid.
3. Attacker sends the crafted message to V. `process_chunk_state_witness_message()` routes it to `handle_orphan_witness()` because `X` is not in the chain store.
4. `handle_orphan_witness()` passes the height and size checks and calls `add_orphan_state_witness()`.
5. `LruCache::push(ChunkProductionKey{E,S,H}, crafted_entry)` evicts the legitimate witness.
6. The legitimate previous block arrives. `take_state_witnesses_waiting_for_block(legitimate_prev)` iterates the cache; the stored entry has `prev_block_hash = X`, so no match is found. The result is an empty `Vec`.
7. `process_ready_orphan_witnesses()` processes nothing. V never calls `process_chunk_state_witness()` for the legitimate chunk and never sends a `ChunkEndorsement`.
8. If enough validators are targeted, the chunk misses the endorsement threshold and is skipped.

### Citations

**File:** chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs (L14-16)
```rust
pub struct OrphanStateWitnessPool {
    witness_cache: LruCache<ChunkProductionKey, CacheEntry>,
}
```

**File:** chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs (L40-45)
```rust
    /// Add an orphaned chunk state witness to the pool. The witness will be put in a cache and it'll
    /// wait there for the block that's required to process it.
    /// It's expected that this `ChunkStateWitness` has gone through basic validation - including signature,
    /// shard_id, size, epoch_id and distance from the tip. The pool would still work without it, but without
    /// validation it'd be possible to fill the whole cache with spam.
    /// `witness_size` is only used for metrics, it's okay to pass 0 if you don't care about the metrics.
```

**File:** chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs (L46-63)
```rust
    pub fn add_orphan_state_witness(&mut self, witness: ChunkStateWitness, witness_size: usize) {
        // Insert the new ChunkStateWitness into the cache
        let cache_key = witness.chunk_production_key();
        let metrics_tracker = OrphanWitnessMetricsTracker::new(&witness, witness_size);
        let cache_entry = CacheEntry { witness, _metrics_tracker: metrics_tracker };
        if let Some((_, ejected_entry)) = self.witness_cache.push(cache_key, cache_entry) {
            // Another witness has been ejected from the cache due to capacity limit
            let header = &ejected_entry.witness.chunk_header();
            tracing::debug!(
                target: "client",
                ejected_witness_height = header.height_created(),
                ejected_witness_shard = ?header.shard_id(),
                ejected_witness_chunk = ?header.chunk_hash(),
                ejected_witness_prev_block = ?header.prev_block_hash(),
                "ejecting an orphaned chunk state witness from the cache due to capacity limit, it will not be processed"
            );
        }
    }
```

**File:** chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs (L67-86)
```rust
    pub fn take_state_witnesses_waiting_for_block(
        &mut self,
        prev_block: &CryptoHash,
    ) -> Vec<ChunkStateWitness> {
        let mut to_remove: Vec<ChunkProductionKey> = Vec::new();
        for (cache_key, cache_entry) in &self.witness_cache {
            if cache_entry.witness.chunk_header().prev_block_hash() == prev_block {
                to_remove.push(cache_key.clone());
            }
        }
        let mut result = Vec::new();
        for cache_key in to_remove {
            let ready_witness = self
                .witness_cache
                .pop(&cache_key)
                .expect("The cache contains this entry, a moment ago it was iterated over");
            result.push(ready_witness.witness);
        }
        result
    }
```

**File:** chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs (L259-291)
```rust
    /// When a new witness is inserted with the same (shard_id, height) as an existing witness, the new witness
    /// should replace the old one. The old one should be ejected from the pool.
    #[test]
    fn replacing() {
        let mut pool = OrphanStateWitnessPool::new(10);

        // The old witness is replaced when the awaited block is the same
        {
            let witness1 = make_witness(100, ShardId::new(1), block(99));
            let witness2 = make_witness(100, ShardId::new(1), block(99));
            pool.add_orphan_state_witness(witness1, 0);
            pool.add_orphan_state_witness(witness2.clone(), 0);

            let waiting_for_99 = pool.take_state_witnesses_waiting_for_block(&block(99));
            assert_contents(waiting_for_99, vec![witness2]);
        }

        // The old witness is replaced when the awaited block is different, waiting_for_block is cleaned as expected
        {
            let witness3 = make_witness(102, ShardId::new(1), block(100));
            let witness4 = make_witness(102, ShardId::new(1), block(101));
            pool.add_orphan_state_witness(witness3, 0);
            pool.add_orphan_state_witness(witness4.clone(), 0);

            let waiting_for_101 = pool.take_state_witnesses_waiting_for_block(&block(101));
            assert_contents(waiting_for_101, vec![witness4]);

            let waiting_for_100 = pool.take_state_witnesses_waiting_for_block(&block(100));
            assert_contents(waiting_for_100, vec![]);
        }

        assert_empty(&pool);
    }
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L247-283)
```rust
        // Don't save orphaned state witnesses which are far away from the current chain head.
        let chain_head = self.chain_store.head()?;
        let head_distance = witness_height.saturating_sub(chain_head.height);

        if !ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD.contains(&head_distance) {
            tracing::debug!(
                target: "chunk_validation",
                head_height = chain_head.height,
                "not saving an orphaned chunk state witness because its height isn't within the allowed height range"
            );
            return Ok(HandleOrphanWitnessOutcome::TooFarFromHead {
                witness_height,
                head_height: chain_head.height,
            });
        }

        // Don't save orphaned state witnesses which are bigger than the allowed limit.
        let witness_size_u64: u64 = witness_size as u64;
        if witness_size_u64 > self.max_orphan_witness_size {
            tracing::warn!(
                target: "chunk_validation",
                witness_height,
                ?witness_shard,
                witness_chunk = ?chunk_header.chunk_hash(),
                witness_prev_block = ?chunk_header.prev_block_hash(),
                witness_size = witness_size_u64,
                "not saving an orphaned chunk state witness because it's too big, this is unexpected"
            );
            return Ok(HandleOrphanWitnessOutcome::TooBig(witness_size_u64 as usize));
        }

        // Orphan witness is OK, save it to the pool
        tracing::debug!(target: "chunk_validation", "saving an orphaned chunk state witness to orphan pool");
        self.orphan_witness_pool
            .lock()
            .add_orphan_state_witness(witness, witness_size_u64 as usize);
        Ok(HandleOrphanWitnessOutcome::SavedToPool)
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L371-394)
```rust
    fn start_validating_chunk(
        &self,
        state_witness: ChunkStateWitness,
        signer: &Arc<ValidatorSigner>,
        save_witness_if_invalid: bool,
        processing_done_tracker: Option<ProcessingDoneTracker>,
    ) -> Result<(), Error> {
        let _span = tracing::debug_span!(
            target: "chunk_validation",
            "start_validating_chunk",
            height = %state_witness.chunk_production_key().height_created,
            shard_id = %state_witness.chunk_production_key().shard_id,
            validator = %signer.validator_id(),
            tag_block_production = true,
            tag_witness_distribution = true,
        )
        .entered();

        let prev_block_hash = *state_witness.chunk_header().prev_block_hash();
        let chunk_production_key = state_witness.chunk_production_key();
        let shard_id = state_witness.chunk_header().shard_id();
        let chunk_header = state_witness.chunk_header().clone();
        let chunk_producer_name =
            self.epoch_manager.get_chunk_producer_info(&chunk_production_key)?.take_account_id();
```
