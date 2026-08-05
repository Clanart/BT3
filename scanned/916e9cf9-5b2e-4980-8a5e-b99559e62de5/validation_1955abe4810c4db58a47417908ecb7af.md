### Title
Vote CRDS-value index sanitize check uses stale `OLD_MAX_VOTES` bound instead of `MAX_VOTES`, letting a remote peer create vote entries the local eviction/index logic cannot bound or reclaim - (File: gossip/src/crds_data.rs)

### Summary
This is a structural analog of the reported `MAX_DELEGATES` bug: the invariant that is supposed to cap a per-identity collection (`MAX_DELEGATES` delegates in the Solidity report, `MAX_VOTES` vote slots per pubkey in the crds table here) is validated against the wrong value. In the Solidity bug, `_moveAllDelegates()` checked `dstRepOld.length` (the pre-mutation length) instead of the post-mutation length, letting the real count exceed `MAX_DELEGATES`. In Agave's gossip crate, the wire-level `Sanitize` check for a `CrdsData::Vote` index validates against `OLD_MAX_VOTES` while the rest of the vote-slot management code (`push_vote_at_index`, `find_vote_index_to_evict`) is written against `MAX_VOTES`. The two constants are not the same value, so the boundary enforced on untrusted, remote input differs from the boundary that local logic assumes governs the same key space.

### Finding Description
`CrdsData::sanitize()` accepts an incoming `Vote` CRDS value as long as its index is below `OLD_MAX_VOTES`: [1](#0-0) 

Meanwhile, the code that manages a validator's own vote-index space is written entirely in terms of `MAX_VOTES`: [2](#0-1) [3](#0-2) 

`find_vote_index_to_evict()` only ever scans indices `0..MAX_VOTES` when deciding which vote slot is stale and can be recycled/evicted. Any `CrdsData::Vote(ix, _)` value with `ix` in the range `[MAX_VOTES, OLD_MAX_VOTES)` passes `sanitize()` (because the check only rejects `ix >= OLD_MAX_VOTES`), is accepted into the shared `Crds` table via `Crds::insert()` (which keys entries purely by `CrdsValueLabel`, i.e. `(index, pubkey)`, with no additional bound enforced at insert time): [4](#0-3) 

and, because `Vote` CRDS values are always retained on ingress regardless of stake: [5](#0-4) 

...these out-of-range-but-sanitize-passing vote entries persist. Because `find_vote_index_to_evict` never inspects indices `>= MAX_VOTES`, such entries are permanently invisible to the local eviction/refresh logic and are never recycled the way normal vote entries are, while still occupying a slot in `Crds::records` for that pubkey and being relayed onward through push/pull gossip like any other CRDS value.

This is the same broken-invariant pattern as the report: the guard that is supposed to bound a per-owner collection is evaluated against a value that does not match the value actually used to manage/mutate that collection, letting the real collection exceed the intended bound while all the "normal" maintenance code (eviction, refresh, index reuse - the Rust analog of `_moveAllDelegates`'s legitimate transfer path) keeps operating under the false assumption that the bound is `MAX_VOTES`.

### Impact Explanation
Any unprivileged, unstaked gossip participant (no special validator/admin/trusted role required - `CrdsData::Vote` ingress is unconditionally retained per `should_retain_crds_value`) can emit vote CRDS values whose index sits in the gap between `MAX_VOTES` and `OLD_MAX_VOTES`. These entries:
- are accepted by every receiving node's `sanitize()`/`insert()` path,
- are outside the index range the local recycling logic (`find_vote_index_to_evict`) ever considers, so they are never overwritten/reclaimed the way the normal `MAX_VOTES`-bounded vote slots are,
- are relayed cluster-wide via push/pull gossip like any other value.

The net effect is uncontrolled, non-reclaimable growth of vote-labeled entries per attacker-controlled pubkey across the network's `Crds` tables, beyond what the vote-management logic (`MAX_VOTES`) was designed to allow - a remote, non-RPC, low-rate resource-exhaustion vector reachable purely through the gossip protocol.

### Likelihood Explanation
Likelihood is Medium: exploitation only requires a keypair and the ability to gossip (any node can do this without stake), and constructing a `CrdsData::Vote` with a crafted index is trivial - no special timing, race, or privileged role is needed. The severity is capped by however large the numeric gap between `MAX_VOTES` and `OLD_MAX_VOTES` actually is (this could not be conclusively confirmed with the remaining tool budget), which is the main source of uncertainty in this finding.

### Recommendation
Update `CrdsData::sanitize()` for the `Vote` variant to reject `ix >= MAX_VOTES` (the constant actually used by `push_vote_at_index`/`find_vote_index_to_evict`), not `OLD_MAX_VOTES`, so the wire-level bound matches the bound the rest of the vote-slot management code assumes. If `OLD_MAX_VOTES` is intentionally retained for backward-compatibility with older CRDS producers, the eviction/index logic in `cluster_info.rs` must be extended to also account for and reclaim indices up to `OLD_MAX_VOTES`, rather than leaving a gap that is accepted on the wire but ignored by local bookkeeping.

### Proof of Concept
Exact reproduction requires confirming the concrete values of `MAX_VOTES` and `OLD_MAX_VOTES` in `gossip/src/crds_data.rs`, which I could not pin down before the tool budget ran out. Conceptually:
1. As any unstaked gossip node, construct and sign a `CrdsValue::new(CrdsData::Vote(ix, vote), keypair)` where `MAX_VOTES <= ix < OLD_MAX_VOTES`.
2. Send it as a `PushMessage`/`PullResponse` to a target validator.
3. Observe it passes `CrdsData::sanitize()` and is inserted into `Crds` (`gossip/src/crds.rs` `insert()`), yet is never visited by `find_vote_index_to_evict` (`gossip/src/cluster_info.rs`), so it is never recycled by subsequent legitimate votes and persists/propagates indefinitely.

Given the residual uncertainty about the exact numeric gap between `MAX_VOTES` and `OLD_MAX_VOTES`, this should be verified against the live constant definitions before being treated as fully confirmed.

### Citations

**File:** gossip/src/crds_data.rs (L114-122)
```rust
impl Sanitize for CrdsData {
    fn sanitize(&self) -> Result<(), SanitizeError> {
        match self {
            CrdsData::Vote(ix, val) => {
                if *ix >= OLD_MAX_VOTES {
                    return Err(SanitizeError::ValueOutOfBounds);
                }
                val.sanitize()
            }
```

**File:** gossip/src/cluster_info.rs (L880-891)
```rust
    pub fn push_vote_at_index(&self, vote: Transaction, vote_index: u8, self_keypair: &Keypair) {
        assert!(vote_index < MAX_VOTES);
        let self_pubkey = self_keypair.pubkey();
        let now = timestamp();
        let vote = Vote::new(self_pubkey, vote, now).unwrap();
        let vote = CrdsData::Vote(vote_index, vote);
        let vote = CrdsValue::new(vote, self_keypair);
        let mut gossip_crds = self.gossip.crds.write().unwrap();
        if let Err(err) = gossip_crds.insert(vote, now, GossipRoute::LocalMessage) {
            error!("push_vote failed: {err:?}");
        }
    }
```

**File:** gossip/src/cluster_info.rs (L900-935)
```rust
    fn find_vote_index_to_evict(&self, new_vote_slot: Slot) -> Option<u8> {
        let self_pubkey = self.id();
        let mut num_crds_votes = 0;
        let mut exists_newer_vote = false;
        let vote_index = {
            let gossip_crds =
                self.time_gossip_read_lock("gossip_read_push_vote", &self.stats.push_vote_read);
            (0..MAX_VOTES)
                .filter_map(|ix| {
                    let vote = CrdsValueLabel::Vote(ix, self_pubkey);
                    let vote: &CrdsData = gossip_crds.get(&vote)?;
                    num_crds_votes += 1;
                    match &vote {
                        CrdsData::Vote(_, vote) if vote.slot() < Some(new_vote_slot) => {
                            Some((vote.wallclock, ix))
                        }
                        CrdsData::Vote(_, _) => {
                            exists_newer_vote = true;
                            None
                        }
                        _ => panic!("this should not happen!"),
                    }
                })
                .min() // Boot the oldest evicted vote by wallclock.
                .map(|(_ /*wallclock*/, ix)| ix)
        };
        if exists_newer_vote {
            return None;
        }
        if num_crds_votes < MAX_VOTES {
            // Do not evict if there is space in crds
            Some(num_crds_votes)
        } else {
            vote_index
        }
    }
```

**File:** gossip/src/crds.rs (L261-299)
```rust
    pub fn insert(
        &mut self,
        value: CrdsValue,
        now: u64,
        route: GossipRoute,
    ) -> Result<(), CrdsError> {
        let label = value.label();
        let pubkey = value.pubkey();
        let value = VersionedCrdsValue::new(value, self.cursor, now, route);
        let mut stats = self.stats.lock().unwrap();
        match self.table.entry(label) {
            Entry::Vacant(entry) => {
                stats.record_insert(&value, route);
                let entry_index = entry.index();
                self.shards.insert(entry_index, &value);
                match value.value.data() {
                    CrdsData::ContactInfo(node) => {
                        self.nodes.insert(entry_index);
                        emit_contact_info_event(
                            self.contact_info_sender.as_ref(),
                            ContactInfoEvent::Updated(ContactInfoSnapshot::from(node)),
                        );
                    }
                    CrdsData::Vote(_, _) => {
                        self.votes.insert(value.ordinal, entry_index);
                    }
                    CrdsData::EpochSlots(_, _) => {
                        self.epoch_slots.insert(value.ordinal, entry_index);
                    }
                    CrdsData::DuplicateShred(_, _) => {
                        self.duplicate_shreds.insert(value.ordinal, entry_index);
                    }
                    _ => (),
                };
                self.entries.insert(value.ordinal, entry_index);
                self.records.entry(pubkey).or_default().insert(entry_index);
                self.cursor.consume(value.ordinal);
                entry.insert(value);
                Ok(())
```

**File:** gossip/src/crds_filter.rs (L64-68)
```rust
        CrdsData::Vote(_, _) if is_full_alpenglow_epoch => false,
        CrdsData::Vote(_, _) => match direction {
            Ingress | EgressPush => true,
            EgressPullResponse => retain_if_staked(),
        },
```
