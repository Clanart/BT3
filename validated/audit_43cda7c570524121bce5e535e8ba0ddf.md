### Title
`SnapshotHostsCache::insert` Does Not Reconcile Stale `hosts_for_shard` Entries When a Peer's Shard List Shrinks — (`chain/network/src/snapshot_hosts/mod.rs`)

---

### Summary

`Inner::insert` in `SnapshotHostsCache` only **adds** a peer to the shards listed in its new `SnapshotHostInfo` but never **removes** it from shards it previously advertised and no longer serves. When a peer sends a valid, higher-epoch update with a reduced shard list (same `sync_hash`), the old shard entries remain in `hosts_for_shard`. The syncing node then permanently routes state-part requests to a peer that no longer serves those shards, stalling state sync for the affected shards.

---

### Finding Description

`Inner::insert` is called whenever a peer's `SnapshotHostInfo` is accepted as newer (higher `epoch_height`):

```rust
fn insert(&mut self, d: &Arc<SnapshotHostInfo>) {
    self.add_to_shard_hosts(d.as_ref());          // (1) adds new shards only
    if let Some((displaced, _)) = self.hosts.push(d.peer_id.clone(), d.clone()) {
        if displaced != d.peer_id {               // (2) only fires on LRU eviction
            self.remove_from_shard_hosts(&displaced);
        }
    }
}
``` [1](#0-0) 

When `hosts.push` is called with an existing key (same-peer update), the LRU cache returns `Some((peer_id, old_value))` where `displaced == d.peer_id`. The guard `if displaced != d.peer_id` is therefore **false**, so `remove_from_shard_hosts` is never called. The code's own comment documents this incorrect assumption:

> "`push` returns a different peer on eviction, or `d` itself on a same-key update; **only an eviction should leave the shard caches**." [2](#0-1) 

`add_to_shard_hosts` only inserts the peer into the new shards; it never removes it from shards dropped from the list:

```rust
fn add_to_shard_hosts(&mut self, info: &SnapshotHostInfo) {
    if self.current_state_sync_hash.as_ref() != Some(&info.sync_hash) { return; }
    for shard_id in &info.shards {
        self.hosts_for_shard.entry(*shard_id).or_default().insert(info.peer_id.clone());
    }
}
``` [3](#0-2) 

The invariant that `hosts_for_shard[shard_id]` contains exactly the peers whose **current** `SnapshotHostInfo.shards` includes `shard_id` is therefore broken on any same-key update that removes shards.

The `hosts_for_shard` map is only fully rebuilt when the local `current_state_sync_hash` changes:

```rust
fn update_current_state_sync_hash(&mut self, sync_hash: &CryptoHash) {
    if self.current_state_sync_hash == Some(*sync_hash) { return; }
    self.current_state_sync_hash = Some(*sync_hash);
    self.hosts_for_shard.clear();
    self.peer_selector.clear();
    // rebuild from all known hosts
    ...
}
``` [4](#0-3) 

Within a single sync session (same `sync_hash`), stale entries are never cleared.

---

### Impact Explanation

`select_host_for_part` reads directly from `hosts_for_shard` to populate `PartPeerSelector`:

```rust
let available_hosts = self.hosts_for_shard.get(&shard_id)?;
if selector.tried_everybody() && selector.len() < available_hosts.len() {
    // expand selector from available_hosts
}
``` [5](#0-4) 

If the stale peer is the **only** entry in `hosts_for_shard[shard_id]`, then `selector.len() == available_hosts.len()` after the first attempt, so no new hosts are ever added. The selector keeps returning the stale peer, which no longer serves that shard. State sync for the affected shard stalls indefinitely — the syncing node cannot obtain the required state parts and cannot complete catchup or initial sync.

---

### Likelihood Explanation

Any peer in the network can trigger this with two consecutive, validly-signed `SyncSnapshotHosts` messages:

1. `SnapshotHostInfo { sync_hash: H, shards: [0, 1, 2, 3], epoch_height: N }` — peer is added to `hosts_for_shard[0..3]`
2. `SnapshotHostInfo { sync_hash: H, shards: [1, 3], epoch_height: N+1 }` — `is_new` returns `true` (higher epoch), `insert` is called, stale entries for shards 0 and 2 remain [6](#0-5) 

Both messages pass signature verification (signed with the peer's own key). No privileged role is required. The `SyncSnapshotHosts` message is accepted from any connected T2 peer: [7](#0-6) 

Legitimate resharding also naturally produces this scenario: a node that previously snapshotted all shards may stop serving some shards after a shard split, and will publish a new `SnapshotHostInfo` with a reduced shard list at the next epoch.

---

### Recommendation

In `Inner::insert`, before adding the peer to its new shards, remove it from all shards it previously served:

```rust
fn insert(&mut self, d: &Arc<SnapshotHostInfo>) {
    // If this peer already has an entry, remove its old shard registrations
    // before adding the new ones, so dropped shards are not left stale.
    if self.hosts.peek(&d.peer_id).is_some() {
        self.remove_from_shard_hosts(&d.peer_id);
    }
    self.add_to_shard_hosts(d.as_ref());
    if let Some((displaced, _)) = self.hosts.push(d.peer_id.clone(), d.clone()) {
        if displaced != d.peer_id {
            self.remove_from_shard_hosts(&displaced);
        }
    }
}
```

This ensures `hosts_for_shard` always reflects the **current** shard list for every peer, regardless of whether the update adds or removes shards.

---

### Proof of Concept

```
1. Syncing node begins state sync with sync_hash = H.
   select_host_for_header(H, shard_0) triggers update_current_state_sync_hash(H).
   hosts_for_shard is cleared and rebuilt (empty at this point).

2. Peer A sends: SnapshotHostInfo { peer_id: A, sync_hash: H, shards: [0,1,2,3], epoch_height: 100 }
   → is_new: true (no prior entry for A)
   → insert: add_to_shard_hosts adds A to hosts_for_shard[0], [1], [2], [3]
   → hosts.push: no displaced entry (first insert)

3. Peer A sends: SnapshotHostInfo { peer_id: A, sync_hash: H, shards: [1,3], epoch_height: 101 }
   → is_new: true (101 > 100)
   → insert:
       add_to_shard_hosts: A already in hosts_for_shard[1] and [3]; no-op
       hosts.push: returns Some((A, old_info)); displaced == d.peer_id → remove_from_shard_hosts NOT called
   → hosts_for_shard[0] = {A}  ← STALE
   → hosts_for_shard[2] = {A}  ← STALE
   → hosts_for_shard[1] = {A}, hosts_for_shard[3] = {A}  ← correct

4. Syncing node calls select_host_for_part(H, shard_0, part_id) → returns A
   Peer A does not serve shard 0 → request fails / no response.

5. select_host_for_part is called again:
   selector.tried_everybody() = true, selector.len() (1) == available_hosts.len() (1)
   → no expansion; returns A again.

6. State sync for shard 0 stalls indefinitely.
``` [8](#0-7) [9](#0-8)

### Citations

**File:** chain/network/src/snapshot_hosts/mod.rs (L136-153)
```rust
struct Inner {
    /// The latest known SnapshotHostInfo for each node in the network
    hosts: LruCache<PeerId, Arc<SnapshotHostInfo>>,
    /// The current sync hash being actively synced by this node. Used to reset peer selectors when changed.
    /// Updated only by locally-produced sync requests.
    current_state_sync_hash: Option<CryptoHash>,
    /// Minimum epoch height to keep in the snapshot host cache. Snapshot infos below this are discarded.
    /// Updated based on chain head progression.
    discard_snapshot_infos_below_epoch_height: Option<EpochHeight>,
    /// Available hosts for the active state sync, by shard
    hosts_for_shard: HashMap<ShardId, HashSet<PeerId>>,
    /// Local data structures used to distribute state part requests among known hosts
    peer_selector: HashMap<(ShardId, u64), PartPeerSelector>,
    /// Batch size for populating the peer_selector from the hosts
    part_selection_cache_batch_size: usize,
    /// Epoch retention window
    epoch_retention_window: EpochHeight,
}
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L156-168)
```rust
    fn is_new(&self, h: &SnapshotHostInfo) -> bool {
        // Discard snapshot infos below the epoch height threshold set by chain progression
        if self
            .discard_snapshot_infos_below_epoch_height
            .is_some_and(|min_epoch| min_epoch > h.epoch_height)
        {
            return false;
        }
        match self.hosts.peek(&h.peer_id) {
            Some(old) if old.epoch_height >= h.epoch_height => false,
            _ => true,
        }
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L183-190)
```rust
    fn add_to_shard_hosts(&mut self, info: &SnapshotHostInfo) {
        if self.current_state_sync_hash.as_ref() != Some(&info.sync_hash) {
            return;
        }
        for shard_id in &info.shards {
            self.hosts_for_shard.entry(*shard_id).or_default().insert(info.peer_id.clone());
        }
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L200-209)
```rust
    fn insert(&mut self, d: &Arc<SnapshotHostInfo>) {
        self.add_to_shard_hosts(d.as_ref());
        // `push` returns a different peer on eviction, or `d` itself on a
        // same-key update; only an eviction should leave the shard caches.
        if let Some((displaced, _)) = self.hosts.push(d.peer_id.clone(), d.clone()) {
            if displaced != d.peer_id {
                self.remove_from_shard_hosts(&displaced);
            }
        }
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L213-228)
```rust
    fn update_current_state_sync_hash(&mut self, sync_hash: &CryptoHash) {
        if self.current_state_sync_hash == Some(*sync_hash) {
            return;
        }

        self.current_state_sync_hash = Some(*sync_hash);
        // Reset peer selectors and shard-specific caches for the new sync hash
        self.hosts_for_shard.clear();
        self.peer_selector.clear();

        // Rebuild the shard-specific caches with hosts that match the new sync hash
        let known_hosts: Vec<_> = self.hosts.iter().map(|(_, info)| info.clone()).collect();
        for info in &known_hosts {
            self.add_to_shard_hosts(info.as_ref());
        }
    }
```

**File:** chain/network/src/snapshot_hosts/mod.rs (L265-308)
```rust
    /// Given a state part request produced by the local node,
    /// selects a host to which the request should be routed.
    pub fn select_host_for_part(
        &mut self,
        sync_hash: &CryptoHash,
        shard_id: ShardId,
        part_id: u64,
    ) -> Option<PeerId> {
        self.update_current_state_sync_hash(sync_hash);

        let selector =
            self.peer_selector.entry((shard_id, part_id)).or_insert(PartPeerSelector::default());

        // Insert more hosts into the selector if needed
        let available_hosts = self.hosts_for_shard.get(&shard_id)?;
        if selector.tried_everybody() && selector.len() < available_hosts.len() {
            let mut new_peers = BinaryHeap::new();
            let already_included = selector.peer_set();

            for peer_id in available_hosts {
                if already_included.contains(peer_id) {
                    continue;
                }

                let score = priority_score(peer_id, shard_id, part_id);

                // Wrap entries with `Reverse` so that we pop the *least* desirable options
                new_peers.push(std::cmp::Reverse(StatePartHost {
                    peer_id: peer_id.clone(),
                    score,
                    num_requests: 0,
                }));

                if new_peers.len() > self.part_selection_cache_batch_size {
                    new_peers.pop();
                }
            }

            selector.insert_peers(new_peers.drain().map(|e| e.0));
        }

        let res = selector.next();
        res
    }
```

**File:** chain/network/src/peer/peer_actor.rs (L1237-1261)
```rust
            PeerMessage::SyncSnapshotHosts(msg) => {
                metrics::SYNC_SNAPSHOT_HOSTS.with_label_values(&["received"]).inc();
                // Early exit, if there is no data in the message.
                if msg.hosts.is_empty() {
                    #[cfg(test)]
                    message_processed_event();
                    return;
                }
                let network_state = self.network_state.clone();
                let tcp = self.tcp.clone();
                self.handle.spawn("handle sync snapshot hosts", async move {
                    if let Some(err) = network_state.add_snapshot_hosts(msg.hosts, tcp).await {
                        conn.stop(Some(match err {
                            SnapshotHostInfoError::VerificationError(
                                SnapshotHostInfoVerificationError::InvalidSignature,
                            ) => ReasonForBan::InvalidSignature,
                            SnapshotHostInfoError::VerificationError(
                                SnapshotHostInfoVerificationError::TooManyShards(_),
                            )
                            | SnapshotHostInfoError::DuplicatePeerId => ReasonForBan::Abusive,
                        }));
                    }
                    #[cfg(test)]
                    message_processed_event();
                });
```
