### Title
Wrong `ShardLayout` version used to construct parent `ShardUId` in `get_split_parent_shard_uids` — (File: `core/primitives/src/shard_layout/mod.rs`)

---

### Summary

`ShardLayout::get_split_parent_shard_uids()` constructs `ShardUId` values for parent shards using `self.version()` — the **new** (child) layout's version — instead of the **previous** layout's version. Because `ShardUId` is the database key for all trie state, this produces a UID that does not match any stored data. Every caller that uses the returned UIDs to look up or map state data silently operates on the wrong key space.

---

### Finding Description

`ShardUId` is a two-field struct `{ version: ShardVersion, shard_id: u32 }`. The `version` field encodes which shard layout the shard belongs to; it is the sole mechanism that distinguishes the same logical shard across resharding epochs. All trie state in `DBCol::State` and all shard-UID mappings in the cold store are keyed by the full `ShardUId` (version + shard_id).

`get_split_parent_shard_uids()` is documented as returning the UIDs of shards **from the previous layout** that were split:

```rust
// core/primitives/src/shard_layout/mod.rs  lines 428-434
pub fn get_split_parent_shard_uids(&self) -> BTreeSet<ShardUId> {
    let parent_shard_ids = self.get_split_parent_shard_ids();
    parent_shard_ids
        .into_iter()
        .map(|shard_id| ShardUId::new(self.version(), shard_id))  // ← BUG
        .collect()
}
```

`self` is the **new** layout (version N+1). The parent shard IDs are ordinals that existed in the **old** layout (version N). The correct UID for a parent shard is `ShardUId { version: N, shard_id: parent_id }`, but the function emits `ShardUId { version: N+1, shard_id: parent_id }` — a UID that has never been written to any database column.

**Propagation into `update_state_shard_uid_mapping` (cold store)**

```rust
// core/store/src/archive/cold_storage.rs  lines 207-222
fn update_state_shard_uid_mapping(cold_db: &ColdDB, shard_layout: &ShardLayout) {
    let split_parents = shard_layout.get_split_parent_shard_uids();   // wrong version
    for parent_shard_uid in split_parents {
        // Looks up mapping for ShardUId{version:N+1, shard_id:P} — never stored
        let mapped_shard_uid = get_shard_uid_mapping(&cold_store, parent_shard_uid);
        // Falls back to parent_shard_uid itself (wrong version N+1)
        let children = shard_layout
            .get_children_shards_uids(parent_shard_uid.shard_id())
            .expect("...");
        for child_shard_uid in children {
            // Maps child → ShardUId{version:N+1, shard_id:P}  ← wrong
            update.trie_store_update().set_shard_uid_mapping(child_shard_uid, mapped_shard_uid);
        }
    }
}
```

Step by step:
1. `get_split_parent_shard_uids()` returns `{ShardUId{version:N+1, shard_id:P}}`.
2. `get_shard_uid_mapping` finds no entry for that key (the real mapping, if any, was stored under version N). It returns the key itself: `ShardUId{version:N+1, shard_id:P}`.
3. Every child shard is mapped to `ShardUId{version:N+1, shard_id:P}`.
4. When the cold store later reads trie nodes for a child shard, the mapping redirects to `ShardUId{version:N+1, shard_id:P}`, but all parent-shard trie data is stored under `ShardUId{version:N, shard_id:P}`. The lookup returns nothing.

**Propagation into `get_resharding_parent_shard_uid`**

```rust
// chain/epoch-manager/src/adapter.rs  lines 908-922
fn get_resharding_parent_shard_uid(...) -> Result<Option<ShardUId>, EpochError> {
    let next_layout = self.get_shard_layout(&next_epoch_id)?;
    ...
    let split_parent_shard_uids = next_layout.get_split_parent_shard_uids(); // wrong version
    Ok(split_parent_shard_uids.into_iter().next())
}
```

Any downstream code that uses the returned `ShardUId` to look up `ChunkExtra`, trie state, or state-transition data will silently miss, because the version component of the key is wrong.

The analog to the external report is exact: just as `l2TargetChainId` (EVM namespace) was passed where `wormholeTargetChainId` (Wormhole namespace) was required, here `self.version()` (new-layout namespace) is used where the previous-layout version (old-layout namespace) is required. Both bugs produce a syntactically valid identifier that belongs to the wrong ID space, causing silent misrouting.

---

### Impact Explanation

**Cold store (archival nodes):** After any resharding event, the shard-UID mapping for every child shard points to a non-existent parent UID. All trie-node reads for child shards that rely on the mapping (i.e., reads that should transparently fall through to parent-shard data) return empty. Archival nodes permanently lose the ability to serve historical state queries for any account in a resharded shard. This is a data-availability failure for the entire archival tier.

**`get_resharding_parent_shard_uid`:** Any caller that uses the returned `ShardUId` to fetch `ChunkExtra` or trie state will receive a `DBNotFoundErr` or silently wrong data, potentially causing resharding logic to skip or misapply the split.

---

### Likelihood Explanation

The bug fires on every resharding event for shard layouts where the version increments across the boundary (V1→V2, V2→V2', etc.). It does not fire for V3 layouts where `VERSION` is a fixed constant (`3`) for both old and new layouts. Mainnet/testnet already underwent the V1→V2 resharding; future dynamic reshardings under V2 will trigger the cold-store path if archival nodes are running the cold-store feature. The code path is unconditional — there is no guard that would prevent the wrong UID from being written.

---

### Recommendation

`get_split_parent_shard_uids()` must be given access to the **previous** layout's version. The simplest fix is to add a companion method that accepts the previous layout explicitly:

```rust
pub fn get_split_parent_shard_uids_with_prev_layout(
    &self,
    prev_layout: &ShardLayout,
) -> BTreeSet<ShardUId> {
    self.get_split_parent_shard_ids()
        .into_iter()
        .map(|shard_id| ShardUId::from_shard_id_and_layout(shard_id, prev_layout))
        .collect()
}
```

All callers (`update_state_shard_uid_mapping`, `get_resharding_parent_shard_uid`) already have access to both the current and previous layouts and should be updated to pass the previous layout.

---

### Proof of Concept

1. An archival node with cold-store enabled processes a resharding block where shard layout transitions from version N to version N+1 (e.g., shard `P` at version N splits into children `C1`, `C2` at version N+1).
2. `update_state_shard_uid_mapping` is called with the new layout (version N+1).
3. `get_split_parent_shard_uids()` returns `{ShardUId{version:N+1, shard_id:P}}`.
4. `get_shard_uid_mapping` finds no entry for `ShardUId{version:N+1, shard_id:P}` (the real parent data is under `ShardUId{version:N, shard_id:P}`); returns the key itself.
5. `set_shard_uid_mapping(C1, ShardUId{version:N+1, shard_id:P})` and `set_shard_uid_mapping(C2, ShardUId{version:N+1, shard_id:P})` are committed.
6. A client queries historical state for an account in shard `C1`. The cold store follows the mapping to `ShardUId{version:N+1,