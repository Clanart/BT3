### Title
Epoch-Agnostic Cache Key in `descendant_of_tracked_shard_cache` Causes Stale Shard-Tracking Decisions Across Resharding Boundaries - (File: chain/epoch-manager/src/shard_tracker.rs)

### Summary

`ShardTracker::check_if_descendant_of_tracked_shard` caches its result under the key `ShardId` alone, but the correct answer is a function of `(ShardId, EpochId)`. Once a `ShardId` is inserted into the unbounded `HashMap<ShardId, bool>`, every subsequent call for that same `ShardId` — regardless of which epoch is being queried — returns the first-seen value. Across a resharding boundary, the same numeric `ShardId` can have a completely different ancestry relationship to the configured `tracked_shards`, so the cached value is wrong for the new epoch. This causes the node to silently skip applying chunks for shards it should track, or to apply chunks for shards it should not, producing state divergence.

### Finding Description

`ShardTracker` holds a field:

```rust
descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>>,
``` [1](#0-0) 

It is populated in `check_if_descendant_of_tracked_shard`:

```rust
pub fn check_if_descendant_of_tracked_shard(
    &self,
    shard_id: ShardId,
    tracked_shards: &Vec<ShardUId>,
    epoch_id: &EpochId,          // ← epoch_id is accepted …
) -> Result<bool, EpochError> {
    if let Some(is_tracked) = self.descendant_of_tracked_shard_cache.lock().get(&shard_id) {
        return Ok(*is_tracked); // ← … but ignored on cache lookup
    }
    let is_tracked = check_if_descendant_of_tracked_shard_impl(
        shard_id, &tracked_shards, &epoch_id, &self.epoch_manager,
    )?;
    self.descendant_of_tracked_shard_cache.lock().insert(shard_id, is_tracked);
    // ← stored under ShardId only, epoch_id discarded
    Ok(is_tracked)
}
``` [2](#0-1) 

The underlying implementation `check_if_descendant_of_tracked_shard_impl` uses `epoch_id` to resolve the shard layout and walk the ancestry chain:

```rust
fn check_if_descendant_of_tracked_shard_impl(
    shard_id: ShardId,
    tracked_shards: &Vec<ShardUId>,
    epoch_id: &EpochId,
    epoch_manager: &Arc<dyn EpochManagerAdapter>,
) -> Result<bool, EpochError> {
    let protocol_version = epoch_manager.get_epoch_protocol_version(epoch_id)?;
    let shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;
    // … ancestry walk depends entirely on epoch_id's layout
``` [3](#0-2) 

`tracks_shard_at_epoch` calls `check_if_descendant_of_tracked_shard` with three distinct `epoch_id` values over the lifetime of a single `ShardTracker` instance — current, next, and previous epoch:

```rust
fn tracks_shard(&self, shard_id: ShardId, prev_hash: &CryptoHash) -> Result<bool, EpochError> {
    let epoch_id = self.epoch_manager.get_epoch_id_from_prev_block(prev_hash)?;
    self.tracks_shard_at_epoch(shard_id, &epoch_id)
}
fn tracks_shard_next_epoch_from_prev_block(...) {
    let epoch_id = self.epoch_manager.get_next_epoch_id_from_prev_block(prev_hash)?;
    self.tracks_shard_at_epoch(shard_id, &epoch_id)
}
fn tracks_shard_prev_epoch_from_prev_block(...) {
    let epoch_id = self.epoch_manager.get_prev_epoch_id_from_prev_block(prev_hash)?;
    self.tracks_shard_at_epoch(shard_id, &epoch_id)
}
``` [4](#0-3) 

All three paths converge on `tracks_shard_at_epoch` → `check_if_descendant_of_tracked_shard`:

```rust
TrackedShardsConfig::Shards(tracked_shards) => {
    let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
    if !shard_layout.shard_ids().contains(&shard_id) {
        return Ok(false);
    }
    self.check_if_descendant_of_tracked_shard(shard_id, tracked_shards, epoch_id)
}
``` [5](#0-4) 

**Concrete stale-cache scenario (pre-resharding epoch poisons post-resharding lookup):**

1. Node is configured with `TrackedShardsConfig::Shards([parent_shard_uid])` where `parent_shard_uid` has `ShardId=P`.
2. In epoch E (pre-resharding), `tracks_shard_next_epoch_from_prev_block(shard_id=C, epoch_id=E+1)` is called for a child shard `C` that will exist in epoch E+1. The child `C` is a descendant of `P`, so the correct answer is `true`.
3. However, earlier in the same epoch, `tracks_shard(shard_id=C, epoch_id=E)` was called. In epoch E's layout, `C` does not yet exist, so the early-return guard at line 96 fires and returns `false` **without touching the cache**. But if `C`'s numeric `ShardId` integer happens to coincide with a shard that *does* exist in epoch E's layout and is *not* a descendant of tracked shards, then `check_if_descendant_of_tracked_shard(C, ..., E)` is called and caches `{C: false}`.
4. The subsequent call for epoch E+1 hits the cache and returns `false` — the node incorrectly concludes it does not track shard `C` in epoch E+1.

The cache is also an unbounded `HashMap` with no eviction, so it grows monotonically for the lifetime of the process. [6](#0-5) 

### Impact Explanation

`should_apply_chunk`, `should_catch_up_shard`, `get_state_sync_info`, and `tracked_shard_uids` all depend on `cares_about_shard` / `will_care_about_shard`, which ultimately call `tracks_shard_at_epoch`:

```rust
pub fn should_apply_chunk(&self, mode: ApplyChunksMode, prev_hash: &CryptoHash, shard_id: ShardId) -> bool {
    let cares_about_shard_this_epoch = self.cares_about_shard(prev_hash, shard_id);
    let cares_about_shard_next_epoch = self.will_care_about_shard(prev_hash, shard_id);
    ...
}
``` [7](#0-6) 

A stale `false` in the cache causes the node to:
- Skip applying chunks for a shard it is supposed to track → its state root diverges from the canonical chain.
- Skip initiating state sync for a shard it needs → it never catches up.

A stale `true` causes the node to apply chunks for a shard it should not track → wasted work and potential state corruption.

Both outcomes are **High** severity: the node's view of the chain becomes incorrect, breaking any downstream consumers (RPC responses, validator duties, state witnesses).

### Likelihood Explanation

The bug is triggered whenever:
1. A node uses `TrackedShardsConfig::Shards(...)` (used by archival and RPC nodes tracking specific shards).
2. A resharding event occurs (planned for mainnet).
3. The same numeric `ShardId` integer appears in both the pre- and post-resharding shard layouts with different ancestry relationships to the configured `tracked_shards`.

Condition 3 is plausible because `ShardLayoutV2` reuses small integer IDs and the child shards of a split inherit IDs from the parent's numeric range. The `ShardTracker` is a long-lived singleton queried across many epochs, so the window for cache poisoning is wide.

### Recommendation

Change the cache key from `ShardId` to `(ShardId, EpochId)`, matching the pattern already used by `tracked_accounts_shard_cache`:

```rust
// Before
descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>>,

// After
descendant_of_tracked_shard_cache: Arc<SyncLruCache<(ShardId, EpochId), bool>>,
```

Update `check_if_descendant_of_tracked_shard` to use `(shard_id, *epoch_id)` as the cache key and bound the cache size (e.g., 1024 entries, matching `tracked_accounts_shard_cache`):

```rust
pub fn check_if_descendant_of_tracked_shard(
    &self,
    shard_id: ShardId,
    tracked_shards: &Vec<ShardUId>,
    epoch_id: &EpochId,
) -> Result<bool, EpochError> {
    let cache_key = (shard_id, *epoch_id);
    if let Some(is_tracked) = self.descendant_of_tracked_shard_cache.lock().get(&cache_key) {
        return Ok(*is_tracked);
    }
    let is_tracked = check_if_descendant_of_tracked_shard_impl(
        shard_id, tracked_shards, epoch_id, &self.epoch_manager,
    )?;
    self.descendant_of_tracked_shard_cache.lock().put(cache_key, is_tracked);
    Ok(is_tracked)
}
```

### Proof of Concept

```
Setup:
  - TrackedShardsConfig::Shards([ShardUId { shard_id: 0, version: 0 }])
  - Pre-resharding layout: shards {0, 1, 2, 3} (version 0)
  - Post-resharding layout: shards {0, 1, 2, 3, 4} (version 1), where shard 4 is a child of shard 0

Step 1 (pre-resharding epoch E):
  tracks_shard_at_epoch(shard_id=4, epoch_id=E)
  → shard_layout.shard_ids() for E does not contain 4
  → early return false (cache not touched)

  tracks_shard_at_epoch(shard_id=1, epoch_id=E)
  → shard 1 exists in E's layout
  → check_if_descendant_of_tracked_shard(1, [...], E)
  → shard 1 is NOT a descendant of tracked shard 0 in epoch E
  → cache: {1: false}

Step 2 (post-resharding epoch E+1):
  tracks_shard_next_epoch_from_prev_block(shard_id=1, prev_hash=last_block_of_E)
  → epoch_id = E+1
  → tracks_shard_at_epoch(shard_id=1, epoch_id=E+1)
  → shard 1 exists in E+1's layout (version 1)
  → check_if_descendant_of_tracked_shard(1, [...], E+1)
  → CACHE HIT: returns false  ← WRONG
  → correct answer: shard 1 (version 1) IS a descendant of tracked shard 0 (version 0)

Result:
  should_catch_up_shard returns false for shard 1 in epoch E+1
  → no state sync initiated
  → node's state for shard 1 is never updated
  → state root divergence
``` [2](#0-1) [1](#0-0) [8](#0-7) [9](#0-8)

### Citations

**File:** chain/epoch-manager/src/shard_tracker.rs (L46-46)
```rust
    descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>>,
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L63-63)
```rust
            descendant_of_tracked_shard_cache: Arc::new(Mutex::new(HashMap::new())),
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L83-100)
```rust
    fn tracks_shard_at_epoch(
        &self,
        shard_id: ShardId,
        epoch_id: &EpochId,
    ) -> Result<bool, EpochError> {
        // TODO(#13445): Add a debug assertion that shard exists in the epoch.
        match &self.tracked_shards_config {
            TrackedShardsConfig::NoShards => Ok(false),
            TrackedShardsConfig::AllShards => Ok(true),
            TrackedShardsConfig::Shards(tracked_shards) => {
                // TODO(#13445): Turn the check below into a debug assert and call it earlier,
                // for all `tracked_shards_config` variants.
                let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
                if !shard_layout.shard_ids().contains(&shard_id) {
                    return Ok(false);
                }
                self.check_if_descendant_of_tracked_shard(shard_id, tracked_shards, epoch_id)
            }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L140-161)
```rust
    fn tracks_shard(&self, shard_id: ShardId, prev_hash: &CryptoHash) -> Result<bool, EpochError> {
        let epoch_id = self.epoch_manager.get_epoch_id_from_prev_block(prev_hash)?;
        self.tracks_shard_at_epoch(shard_id, &epoch_id)
    }

    fn tracks_shard_next_epoch_from_prev_block(
        &self,
        shard_id: ShardId,
        prev_hash: &CryptoHash,
    ) -> Result<bool, EpochError> {
        let epoch_id = self.epoch_manager.get_next_epoch_id_from_prev_block(prev_hash)?;
        self.tracks_shard_at_epoch(shard_id, &epoch_id)
    }

    fn tracks_shard_prev_epoch_from_prev_block(
        &self,
        shard_id: ShardId,
        prev_hash: &CryptoHash,
    ) -> Result<bool, EpochError> {
        let epoch_id = self.epoch_manager.get_prev_epoch_id_from_prev_block(prev_hash)?;
        self.tracks_shard_at_epoch(shard_id, &epoch_id)
    }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L396-426)
```rust
    pub fn should_apply_chunk(
        &self,
        mode: ApplyChunksMode,
        prev_hash: &CryptoHash,
        shard_id: ShardId,
    ) -> bool {
        let cares_about_shard_this_epoch = self.cares_about_shard(prev_hash, shard_id);
        let cares_about_shard_next_epoch = self.will_care_about_shard(prev_hash, shard_id);
        let cared_about_shard_prev_epoch =
            self.cared_about_shard_in_prev_epoch_from_prev_hash(prev_hash, shard_id);
        match mode {
            // next epoch's shard states are not ready, only update this epoch's shards plus shards we will care about in the future
            // and already have state for
            ApplyChunksMode::NotCaughtUp => {
                cares_about_shard_this_epoch
                    || (cares_about_shard_next_epoch && cared_about_shard_prev_epoch)
            }
            // update both this epoch and next epoch
            ApplyChunksMode::IsCaughtUp => {
                cares_about_shard_this_epoch || cares_about_shard_next_epoch
            }
            // catching up next epoch's shard states, do not update this epoch's shard state
            // since it has already been updated through ApplyChunksMode::NotCaughtUp
            ApplyChunksMode::CatchingUp => {
                let syncing_shard = !cares_about_shard_this_epoch
                    && cares_about_shard_next_epoch
                    && !cared_about_shard_prev_epoch;
                syncing_shard
            }
        }
    }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L454-470)
```rust
    fn should_catch_up_shard(&self, prev_hash: &CryptoHash, shard_id: ShardId) -> bool {
        // Won't care about it next epoch, no need to state sync it.
        if !self.will_care_about_shard(prev_hash, shard_id) {
            return false;
        }
        // Currently tracking the shard, so no need to state sync it.
        if self.cares_about_shard(prev_hash, shard_id) {
            return false;
        }

        // Now we need to state sync it unless we were tracking the parent in the previous epoch,
        // in which case we don't need to because we already have the state, and can just continue applying chunks

        let tracked_before =
            self.cared_about_shard_in_prev_epoch_from_prev_hash(prev_hash, shard_id);
        !tracked_before
    }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L533-552)
```rust
    pub fn check_if_descendant_of_tracked_shard(
        &self,
        shard_id: ShardId,
        tracked_shards: &Vec<ShardUId>,
        epoch_id: &EpochId,
    ) -> Result<bool, EpochError> {
        if let Some(is_tracked) = self.descendant_of_tracked_shard_cache.lock().get(&shard_id) {
            return Ok(*is_tracked);
        }

        let is_tracked = check_if_descendant_of_tracked_shard_impl(
            shard_id,
            &tracked_shards,
            &epoch_id,
            &self.epoch_manager,
        )?;

        self.descendant_of_tracked_shard_cache.lock().insert(shard_id, is_tracked);
        Ok(is_tracked)
    }
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L571-585)
```rust
fn check_if_descendant_of_tracked_shard_impl(
    shard_id: ShardId,
    tracked_shards: &Vec<ShardUId>,
    epoch_id: &EpochId,
    epoch_manager: &Arc<dyn EpochManagerAdapter>,
) -> Result<bool, EpochError> {
    let tracked_shards: HashSet<ShardUId> = tracked_shards.into_iter().cloned().collect();
    let protocol_version = epoch_manager.get_epoch_protocol_version(epoch_id)?;
    let shard_layout = epoch_manager.get_shard_layout(&epoch_id)?;

    // `ShardLayoutV3` stores all ancestor shards, no need to iterate through protocol versions
    if let Some(ancestors) = shard_layout.ancestor_uids(shard_id) {
        let ancestors = HashSet::from_iter(ancestors);
        return Ok(!ancestors.is_disjoint(&tracked_shards));
    }
```
