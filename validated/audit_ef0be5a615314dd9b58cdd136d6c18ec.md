### Title
Unauthenticated Witness Eviction via `ChunkProductionKey` Collision in `OrphanStateWitnessPool` Breaks Endorsement Commitment — (`File: chain/client/src/stateless_validation/chunk_validator/orphan_witness_pool.rs`)

### Summary

`OrphanStateWitnessPool` uses `ChunkProductionKey` (`shard_id`, `epoch_id`, `height_created`) as its LRU cache key, omitting `prev_block_hash` and `chunk_hash`. Any network peer can send a `ChunkStateWitnessMessage` with a forged witness for the same `(shard_id, epoch_id, height_created)` but pointing to a non-existent `prev_block_hash`. Because `handle_orphan_witness` performs no signature check before inserting into the pool, the forged witness silently evicts the legitimate orphan witness. When the real parent block later arrives, `take_state_witnesses_waiting_for_block` finds no witness for it, and the validator never endorses the chunk.

### Finding Description

`OrphanStateWitnessPool` stores orphaned `ChunkStateWitness` objects in an `LruCache<ChunkProductionKey, CacheEntry>`. [1](#0-0) 

The cache key is `ChunkProductionKey`: [2](#0-1) 

`prev_block_hash` and `chunk_hash` are **not** part of the key. `add_orphan_state_witness` calls `LruCache::push`, which replaces any existing entry with the same key: [3](#0-2) 

The replacement behavior is explicitly tested and considered intentional: [4](#0-3) 

The code path that adds orphan witnesses — `handle_orphan_witness` — performs only a height-distance check and a size check. **No signature verification occurs**: [5](#0-4) 

Signature verification only happens in `start_validating_chunk` → `pre_validate_chunk_state_witness`, which is called only when the previous block is already available — i.e., never for orphan witnesses: [6](#0-5) 

The comment in `add_orphan_state_witness` acknowledges that validation is *expected* but not enforced: [7](#0-6) 

When the real parent block arrives, `process_ready_orphan_witnesses` calls `take_state_witnesses_waiting_for_block` keyed on the block hash. If the legitimate witness was replaced by a forged one pointing to a different `prev_block_hash`, the legitimate witness is gone and the validator never endorses the chunk: [8](#0-7) 

### Impact Explanation

A validator that is targeted loses its endorsement for the affected chunk. If an attacker targets enough validators simultaneously (continuously re-sending forged witnesses for the same `ChunkProductionKey` before the real parent block arrives), the chunk may fail to accumulate a supermajority of endorsements, causing it to be skipped. The attack is repeatable at negligible cost: the attacker only needs to send a network message with a plausible-looking `ChunkStateWitnessMessage` whose `prev_block_hash` is an unknown hash. The `ALLOWED_ORPHAN_WITNESS_DISTANCE_FROM_HEAD` window of 2–5 blocks is narrow but predictable, and the attacker can time the forged message to arrive just after the legitimate witness is stored.

**Impact: High** — targeted liveness failure; chunk endorsement commitment is broken without any cryptographic cost to the attacker.

### Likelihood Explanation

Any peer with network connectivity to a validator node can send a `ChunkStateWitnessMessage`. The `(shard_id, epoch_id, height_created)` of upcoming chunks is deterministic and publicly derivable from epoch data. No validator key or stake is required. The attack requires only timing knowledge and a single network message per eviction.

**Likelihood: High** — unprivileged, low-cost, repeatable.

### Recommendation

1. **Verify the witness signature before inserting into the orphan pool.** The `chunk_header` inside `ChunkStateWitness` is signed by the chunk producer; verify it against the expected producer for `(shard_id, epoch_id, height_created)` before calling `add_orphan_state_witness`. This is the check the comment already says is "expected."

2. **Include `prev_block_hash` (or `chunk_hash`) in the orphan pool cache key.** This prevents a witness for a different fork from evicting a legitimate witness for the same slot. The key should be `(shard_id, epoch_id, height_created, prev_block_hash)` or simply `chunk_hash`.

3. **Reject replacement of an existing orphan witness** unless the new witness has a valid signature and the existing one does not.

### Proof of Concept

```
1. Legitimate chunk producer P produces chunk C at (shard_id=S, epoch_id=E, height=H)
   with prev_block_hash=B_real. B_real is not yet available at validator V.
   P sends ChunkStateWitnessMessage(witness_real) to V.

2. V calls handle_orphan_witness(witness_real):
   - head_distance check passes (H is 2-5 ahead of head)
   - size check passes
   - witness_real is inserted into orphan_pool with key (S, E, H)

3. Attacker A sends ChunkStateWitnessMessage(witness_fake) to V where:
   - witness_fake.chunk_header.shard_id = S
   - witness_fake.chunk_header.height_created = H
   - witness_fake.epoch_id = E
   - witness_fake.chunk_header.prev_block_hash = B_fake (unknown hash, never arrives)
   - witness_fake content is arbitrary (no signature check)

4. V calls handle_orphan_witness(witness_fake):
   - head_distance check passes
   - size check passes
   - LruCache::push((S,E,H), witness_fake) evicts witness_real silently

5. B_real arrives. V calls take_state_witnesses_waiting_for_block(B_real).
   Iterates orphan_pool: only witness_fake is present, its prev_block_hash=B_fake ≠ B_real.
   Result: empty. V never endorses chunk C.

6. Attacker repeats step 3 continuously to prevent re-queuing.
```

The exact corrupted value is the `CacheEntry` for key `ChunkProductionKey { shard_id: S, epoch_id: E, height_created: H }` in `OrphanStateWitnessPool::witness_cache`, which is overwritten with a forged witness whose `prev_block_hash` will never match any arriving block.

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

**File:** core/primitives/src/stateless_validation/mod.rs (L18-23)
```rust
#[derive(Debug, Hash, PartialEq, Eq, Clone, BorshSerialize, BorshDeserialize, ProtocolSchema)]
pub struct ChunkProductionKey {
    pub shard_id: ShardId,
    pub epoch_id: EpochId,
    pub height_created: BlockHeight,
}
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L228-284)
```rust
    pub fn handle_orphan_witness(
        &mut self,
        witness: ChunkStateWitness,
        witness_size: ChunkStateWitnessSize,
    ) -> Result<HandleOrphanWitnessOutcome, Error> {
        let chunk_header = witness.chunk_header();
        let witness_height = chunk_header.height_created();
        let witness_shard = chunk_header.shard_id();

        let _span = tracing::debug_span!(
            target: "chunk_validation",
            "handle_orphan_witness",
            witness_height,
            ?witness_shard,
            witness_chunk = ?chunk_header.chunk_hash(),
            witness_prev_block = ?chunk_header.prev_block_hash(),
        )
        .entered();

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
    }
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L286-308)
```rust
    /// Processes orphan witnesses that are now ready because their previous block has arrived.
    fn process_ready_orphan_witnesses(&self, new_block: &Block) {
        let ready_witnesses = self
            .orphan_witness_pool
            .lock()
            .take_state_witnesses_waiting_for_block(new_block.hash());

        for witness in ready_witnesses {
            let header = witness.chunk_header();
            tracing::debug!(
                target: "chunk_validation",
                witness_height = header.height_created(),
                witness_shard = ?header.shard_id(),
                witness_chunk = ?header.chunk_hash(),
                witness_prev_block = ?header.prev_block_hash(),
                "processing an orphaned chunk state witness, its previous block has arrived"
            );

            if let Err(err) = self.process_chunk_state_witness(witness, new_block, None) {
                tracing::error!(target: "chunk_validation", ?err, "error processing orphan chunk state witness");
            }
        }
    }
```

**File:** chain/client/src/stateless_validation/chunk_validation_actor.rs (L335-357)
```rust
    fn process_chunk_state_witness(
        &self,
        witness: ChunkStateWitness,
        prev_block: &Block,
        processing_done_tracker: Option<ProcessingDoneTracker>,
    ) -> Result<(), Error> {
        let _span = tracing::debug_span!(
            target: "chunk_validation",
            "process_chunk_state_witness",
            chunk_hash = ?witness.chunk_header().chunk_hash(),
            height = %witness.chunk_header().height_created(),
            shard_id = %witness.chunk_header().shard_id(),
        )
        .entered();

        // Validate that block hash matches
        if witness.chunk_header().prev_block_hash() != prev_block.hash() {
            return Err(Error::Other(format!(
                "Previous block hash mismatch: witness={}, block={}",
                witness.chunk_header().prev_block_hash(),
                prev_block.hash()
            )));
        }
```
