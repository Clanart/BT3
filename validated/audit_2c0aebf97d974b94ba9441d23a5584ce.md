### Title
Epoch-Unbound `descendant_of_tracked_shard_cache` Returns Stale Shard-Tracking Decision After Resharding — (`chain/epoch-manager/src/shard_tracker.rs`)

### Summary

`ShardTracker::check_if_descendant_of_tracked_shard` caches its result in `descendant_of_tracked_shard_cache: HashMap<ShardId, bool>`, keyed only by `ShardId`. The underlying computation is epoch-dependent (it walks the shard layout history for the given `epoch_id`). After a resharding event, the same `ShardId` can appear in a new shard layout version with a different ancestry relationship, but the cache returns the stale pre-resharding answer. This causes `cares_about_shard`, `gc_state`, `should_apply_chunk`, and state-sync decisions to be permanently wrong for any `ShardId` that was queried before the resharding.

### Finding Description

`ShardTracker` holds two caches:

- `tracked_accounts_shard_cache: SyncLruCache<EpochId, BitMask>` — correctly keyed by `EpochId`.
- `descendant_of_tracked_shard_cache: Mutex<HashMap<ShardId, bool>>` — keyed only by `ShardId`, **with no epoch binding**. [1](#0-0) 

`check_if_descendant_of_tracked_shard` is called with both `shard_id` and `epoch_id`. On a cache hit it returns the stored `bool` without consulting `epoch_id`: [2](#0-1) 

The underlying `check_if_descendant_of_tracked_shard_impl` is epoch-sensitive: it resolves the protocol version and shard layout for `epoch_id`, then walks the layout history to determine ancestry: [3](#0-2) 

After a resharding, the shard layout version increments. The same `ShardId` that existed in the old layout (e.g., an unchanged sibling shard) gets a new `ShardUId` (new version) in the new layout. The existing test explicitly asserts this version change: [4](#0-3) 

**Stale-cache scenario:**

1. **Epoch E (pre-resharding):** `check_if_descendant_of_tracked_shard(shard_id=X, epoch_id=E)` is called. `check_if_descendant_of_tracked_shard_impl` correctly returns `false` (shard X is not a descendant of any tracked shard in epoch E). Cache stores `{X → false}`.

2. **Epoch E+1 (post-resharding):** `check_if_descendant_of_tracked_shard(shard_id=X, epoch_id=E+1)` is called. The correct answer is now `true` (shard X is a child of a tracked parent in the new layout). But the cache returns `false` immediately — the `epoch_id` argument is never consulted.

The inverse is equally possible: a shard that was tracked pre-resharding is incorrectly reported as tracked post-resharding when it should not be.

`tracks_shard_at_epoch` calls `check_if_descendant_of_tracked_shard` for the `TrackedShardsConfig::Shards` variant: [5](#0-4) 

Every downstream consumer of `cares_about_shard`, `will_care_about_shard`, `should_apply_chunk`, `gc_state`, and `get_state_sync_info` inherits the corrupted result.

### Impact Explanation

For nodes configured with `TrackedShardsConfig::Shards` (archival nodes tracking a specific shard subset):

- **`should_apply_chunk`** returns wrong values → chunks for the affected shard are silently skipped or double-applied, causing state root divergence.
- **`gc_state`** uses `cares_about_shard_this_or_next_epoch` to decide which shard state to delete. A stale `false` causes the node to GC state it must retain, permanently destroying trie data for the shard.
- **`get_state_sync_info` / `should_catch_up_shard`** uses the same predicate to decide whether to initiate state sync. A stale answer causes the node to skip state sync for a shard it now needs, leaving it unable to apply future chunks. [6](#0-5) [7](#0-6) 

### Likelihood Explanation

- Resharding is an active production feature in NEAR (static and dynamic resharding are both implemented and tested).
- Any node using `TrackedShardsConfig::Shards` — the standard configuration for archival nodes tracking a subset of shards — is affected the first time a resharding event occurs after the node starts.
- The cache is never invalidated; it persists for the lifetime of the `ShardTracker` instance. Once poisoned, the wrong answer is returned for every subsequent query for that `ShardId`.
- No privileged action is required; the bug is triggered by normal epoch progression through a resharding boundary.

### Recommendation

Change the cache key from `ShardId` to `(ShardId, EpochId)` to bind each cached result to the epoch for which it was computed:

```rust
descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<(ShardId, EpochId), bool>>>,
```

Update `check_if_descendant_of_tracked_shard` to use `(shard_id, *epoch_id)` as the lookup and insertion key. This matches the pattern already used correctly by `tracked_accounts_shard_cache`, which is keyed by `EpochId`.

### Proof of Concept

1. Start a node with `TrackedShardsConfig::Shards([parent_shard_uid])` where `parent_shard_uid` has `ShardId = P`.
2. In epoch E (pre-resharding), call `tracks_shard_at_epoch(shard_id=C, epoch_id=E)` for a sibling shard `C` that is not a descendant of `P`. The cache stores `{C → false}`.
3. A resharding occurs: shard `P` splits into children `P` and `C` (or `C` becomes a child of `P` under the new layout). Now `C` is a descendant of the tracked shard.
4. In epoch E+1, call `tracks_shard_at_epoch(shard_id=C, epoch_id=E+1)`. The cache hits on `C` and returns `false`.
5. `gc_state` is called at the epoch boundary. It calls `cares_about_shard_this_or_next_epoch` for shard `C`, which returns `false` (stale). GC deletes the entire state prefix for shard `C`.
6. The node is now unable to apply chunks for shard `C` in epoch E+1 and beyond — its trie data has been permanently deleted. [8](#0-7) [9](#0-8)

### Citations

**File:** chain/epoch-manager/src/shard_tracker.rs (L40-46)
```rust
    tracked_accounts_shard_cache: Arc<SyncLruCache<EpochId, BitMask>>,
    /// Caches whether a given shard is a descendant of any of the `tracked_shards`.
    /// This is required in scenarios with resharding, where the node must continue tracking
    /// not only the originally configured shards but also their descendants.
    /// The result is cached to avoid recomputing descendant relationships repeatedly.
    /// Only relevant when `TrackedShardsConfig` is set to `Shards(tracked_shards)`.
    descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>>,
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L92-99)
```rust
            TrackedShardsConfig::Shards(tracked_shards) => {
                // TODO(#13445): Turn the check below into a debug assert and call it earlier,
                // for all `tracked_shards_config` variants.
                let shard_layout = self.epoch_manager.get_shard_layout(epoch_id)?;
                if !shard_layout.shard_ids().contains(&shard_id) {
                    return Ok(false);
                }
                self.check_if_descendant_of_tracked_shard(shard_id, tracked_shards, epoch_id)
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L454-469)
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

**File:** chain/epoch-manager/src/shard_tracker.rs (L571-615)
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

    let mut shard_uid = ShardUId::from_shard_id_and_layout(shard_id, &shard_layout);
    if tracked_shards.contains(&shard_uid) {
        // We explicitly track `shard_id` (the shard is a descendant of itself).
        return Ok(true);
    }

    // `shard_uid` does not belong to `tracked_shards`, but it might be a descendant of one.
    // Iterate through consecutive pairs of historical shard layouts (newest to oldest) to trace
    // the ancestry. Each pair represents a resharding transition.
    let layout_history = epoch_manager.get_shard_layout_history(protocol_version, None);
    for window in layout_history.windows(2) {
        let current_layout = &window[0];
        let prev_layout = &window[1];
        let Some(parent_shard_id) = current_layout.try_get_parent_shard_id(shard_uid.shard_id())?
        else {
            debug_assert!(
                false,
                "Parent shard is missing for shard {} in shard layout {:?}",
                shard_uid, current_layout,
            );
            return Ok(false);
        };
        shard_uid = ShardUId::from_shard_id_and_layout(parent_shard_id, &prev_layout);
        if tracked_shards.contains(&shard_uid) {
            return Ok(true);
        }
    }
    Ok(false)
}
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L848-849)
```rust
        // We expect the shard layout version to change in this test.
        assert_ne!(non_parent_shard_new_uid.version, not_parent_shard_uid.version);
```

**File:** chain/chain/src/garbage_collection.rs (L1117-1170)
```rust
fn gc_state(
    chain_store_update: &mut ChainStoreUpdate,
    epoch_manager: &dyn EpochManagerAdapter,
    block_hash: &CryptoHash,
    shard_tracker: &ShardTracker,
) -> Result<(), Error> {
    // Return if we are not dealing with the last block of the epoch
    if !epoch_manager.is_last_block_in_finished_epoch(block_hash)? {
        return Ok(());
    }

    tracing::debug!(target: "garbage_collection", "GC state");
    let latest_block_hash = chain_store_update.head()?.last_block_hash;
    let last_block_hash_in_gc_epoch = block_hash;

    // Get all the shards that belong to the gc_epoch for shards_to_cleanup
    let block_info = epoch_manager.get_block_info(last_block_hash_in_gc_epoch)?;
    let mut shards_to_cleanup =
        epoch_manager.get_shard_layout(block_info.epoch_id())?.shard_uids().collect_vec();

    // Remove shards that we are currently tracking from shards_to_cleanup
    shards_to_cleanup.retain(|shard_uid| {
        !shard_tracker
            .cares_about_shard_this_or_next_epoch(&latest_block_hash, shard_uid.shard_id())
    });

    // reverse iterate over the epochs starting from epoch of latest_block_hash upto gc_epoch
    // The current_block_hash is the hash of the last block in the current iteration epoch.
    let store = chain_store_update.store();
    let mut current_block_hash = *epoch_manager.get_block_info(&latest_block_hash)?.hash();
    while &current_block_hash != last_block_hash_in_gc_epoch {
        shards_to_cleanup.retain(|shard_uid| {
            // If shard_uid exists in the TrieChanges column, it means we were tracking the shard_uid in this epoch.
            // We would like to remove shard_uid from shards_to_cleanup
            let trie_changes_key = get_block_shard_uid(&current_block_hash, shard_uid);
            !store.exists(DBCol::TrieChanges, &trie_changes_key)
        });

        // Go to the previous epoch last_block_hash
        let epoch_block_info = epoch_manager.get_block_info(&current_block_hash)?;
        let epoch_first_block_hash = epoch_block_info.epoch_first_block();
        let epoch_first_block = store.chain_store().get_block_header(epoch_first_block_hash)?;
        current_block_hash = *epoch_first_block.prev_hash();
    }

    // Delete State of `shards_to_cleanup` and associated ShardUId mapping.
    tracing::debug!(target: "garbage_collection", ?shards_to_cleanup, "state shards cleanup");
    let mut trie_store_update = store.trie_store().store_update();
    for shard_uid_prefix in shards_to_cleanup {
        trie_store_update.delete_shard_uid_prefixed_state(shard_uid_prefix);
    }
    chain_store_update.merge(trie_store_update.into());
    Ok(())
}
```
