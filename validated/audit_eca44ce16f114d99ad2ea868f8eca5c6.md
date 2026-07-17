### Title
`descendant_of_tracked_shard_cache` Keyed Only by `ShardId` Without Epoch Context Causes Stale Shard-Tracking Decisions Across Resharding — (`File: chain/epoch-manager/src/shard_tracker.rs`)

---

### Summary

`ShardTracker::check_if_descendant_of_tracked_shard` caches its result in a `HashMap<ShardId, bool>` that is keyed only by the bare numeric `ShardId`. Because `ShardId` values are **reused across shard layout versions** (e.g., `ShardId(0)`–`ShardId(3)` exist in both `ShardLayoutV1` and `ShardLayoutV2`), a result computed for one epoch's layout is silently returned for a different epoch's layout when the same numeric `ShardId` is queried. This is the direct nearcore analog of the Linea bridge bug: a mapping that does not include the "layer" (here: epoch/layout version) as part of its key.

---

### Finding Description

`ShardTracker` holds a persistent cache:

```rust
descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>>,
``` [1](#0-0) 

The cache is populated and read in `check_if_descendant_of_tracked_shard`:

```rust
pub fn check_if_descendant_of_tracked_shard(
    &self,
    shard_id: ShardId,
    tracked_shards: &Vec<ShardUId>,
    epoch_id: &EpochId,          // ← epoch_id is accepted …
) -> Result<bool, EpochError> {
    if let Some(is_tracked) = self.descendant_of_tracked_shard_cache.lock().get(&shard_id) {
        return Ok(*is_tracked);  // ← … but NEVER used as part of the cache key
    }
    let is_tracked = check_if_descendant_of_tracked_shard_impl(
        shard_id, &tracked_shards, &epoch_id, &self.epoch_manager,
    )?;
    self.descendant_of_tracked_shard_cache.lock().insert(shard_id, is_tracked);
    Ok(is_tracked)
}
``` [2](#0-1) 

The underlying implementation correctly resolves the shard's ancestry using the epoch-specific shard layout: [3](#0-2) 

But the cache discards the epoch dimension entirely. `ShardId` is a plain `u64` with no layout-version component: [4](#0-3) 

`ShardUId` (the correct cross-epoch identifier) pairs `ShardId` with a `ShardVersion`: [5](#0-4) 

In `ShardLayoutV1` and `ShardLayoutV2`, the same numeric `ShardId` values (0–3) appear in **both** layouts. For example, `ShardId(3)` in V1 is the parent shard that splits into `ShardId(3)` and `ShardId(4)` in V2. These are distinct shards (`ShardUId{v:1,s:3}` vs `ShardUId{v:2,s:3}`), but the cache treats them as identical.

**Concrete collision scenario** (V1 → V2 resharding, which occurred on mainnet):

| Step | Call | Cache state | Correct answer |
|------|------|-------------|----------------|
| 1 | `tracks_shard_at_epoch(ShardId(3), epoch_V2)` with config `Shards([ShardUId{v:2,s:3}])` | miss → compute `true` (V2 shard 3 IS tracked) → store `{3→true}` | `true` ✓ |
| 2 | `tracks_shard_at_epoch(ShardId(3), epoch_V1)` (prev-epoch check) | **hit → returns `true`** | `false` ✗ (V1 shard 3 is the *parent*, not a descendant of `ShardUId{v:2,s:3}`) |

The stale `true` is then consumed by `should_catch_up_shard`:

```rust
fn should_catch_up_shard(&self, prev_hash: &CryptoHash, shard_id: ShardId) -> bool {
    // …
    let tracked_before =
        self.cared_about_shard_in_prev_epoch_from_prev_hash(prev_hash, shard_id);
    !tracked_before   // ← returns false (no sync needed) because cache says "tracked before"
}
``` [6](#0-5) 

The node skips state sync for a shard it has no state for.

The `ShardTracker` is queried for current, next, and previous epochs from the same long-lived instance: [7](#0-6) 

---

### Impact Explanation

A node configured with `TrackedShardsConfig::Shards` tracking a post-resharding child shard (e.g., `ShardUId{v:2,s:3}`) will, after the cache is primed for the current epoch, incorrectly report that it also tracked the pre-resharding parent shard (`ShardUId{v:1,s:3}`) in the previous epoch. This causes `should_catch_up_shard` to return `false`, suppressing the state sync that is required. The node then attempts to apply chunks for a shard whose state it never downloaded, resulting in a broken state invariant, chunk application failure, or validator penalty.

The inverse collision (cache primed for an old epoch, queried for a new epoch) can cause a node to believe it does **not** track a shard it should, causing it to refuse to serve RPC queries for that shard.

**Severity: High** — broken shard-tracking commitment leads to missing state sync and potential validator failure for any node using `TrackedShardsConfig::Shards` across a resharding boundary where `ShardId` values are reused.

---

### Likelihood Explanation

- `TrackedShardsConfig::Shards` is a supported production configuration for archival and RPC nodes.
- The V1 → V2 resharding (mainnet) reuses `ShardId` values 0–3 across both layouts, making the collision reachable on any node that was tracking specific shards during that transition.
- The `ShardTracker` is routinely queried for current, next, and previous epochs within the same process lifetime (see `cares_about_shard`, `will_care_about_shard`, `cared_about_shard_in_prev_epoch_from_prev_hash`), so the collision is triggered by normal operation without any external attacker input.
- The cache has no eviction or invalidation mechanism tied to epoch transitions.

---

### Recommendation

Change the cache key from `ShardId` to `(ShardId, EpochId)` so that results are epoch-scoped:

```rust
descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<(ShardId, EpochId), bool>>>,
```

And update the lookup/insert in `check_if_descendant_of_tracked_shard` to use `(shard_id, *epoch_id)` as the key. This mirrors the correct design of `tracked_accounts_shard_cache`, which is already keyed by `EpochId`: [8](#0-7) 

Alternatively, key by `ShardUId` (which encodes both `ShardId` and `ShardVersion`) after resolving the `ShardUId` from the epoch's shard layout at the start of the function.

---

### Proof of Concept

**Setup**: Node with `TrackedShardsConfig::Shards([ShardUId{version:2, shard_id:3}])`, running across the V1→V2 resharding boundary. Both V1 and V2 layouts contain `ShardId(3)`.

**Trigger sequence** (all within one `ShardTracker` instance lifetime):

1. At the start of a V2 epoch, `cares_about_shard(prev_hash_V2, ShardId(3))` is called → `tracks_shard_at_epoch(ShardId(3), epoch_V2)` → `check_if_descendant_of_tracked_shard(ShardId(3), …, epoch_V2)` → cache miss → `check_if_descendant_of_tracked_shard_impl` resolves `ShardUId{v:2,s:3}` → found in `tracked_shards` → returns `true` → **cache stores `{ShardId(3) → true}`**.

2. `should_catch_up_shard(prev_hash_V2, ShardId(3))` calls `cared_about_shard_in_prev_epoch_from_prev_hash` → `tracks_shard_prev_epoch_from_prev_block` → `tracks_shard_at_epoch(ShardId(3), epoch_V1)` → `check_if_descendant_of_tracked_shard(ShardId(3), …, epoch_V1)` → **cache hit → returns `true`** (stale).

3. `should_catch_up_shard` returns `false` (no state sync needed), but the node has no V1 shard-3 state. Subsequent chunk application for V1 shard 3 fails. [2](#0-1) [6](#0-5)

### Citations

**File:** chain/epoch-manager/src/shard_tracker.rs (L40-40)
```rust
    tracked_accounts_shard_cache: Arc<SyncLruCache<EpochId, BitMask>>,
```

**File:** chain/epoch-manager/src/shard_tracker.rs (L46-46)
```rust
    descendant_of_tracked_shard_cache: Arc<Mutex<HashMap<ShardId, bool>>>,
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

**File:** core/primitives-core/src/types.rs (L80-80)
```rust
pub struct ShardId(u64);
```

**File:** core/primitives/src/shard_layout/mod.rs (L479-482)
```rust
pub struct ShardUId {
    pub version: ShardVersion,
    pub shard_id: u32,
}
```
