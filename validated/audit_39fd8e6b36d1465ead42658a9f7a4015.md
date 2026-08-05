## Finding Description

`ReceivedCacheEntry` tracks, per CRDS origin, which gossip peers (`node`, i.e. the packet's `from` pubkey) relayed messages from that origin, in a `HashMap<Pubkey, usize>` called `nodes`. The `record` method only bounds `nodes.len()` against `CAPACITY = 50` in the "stale/duplicate" branch; the "timely" branch (`num_dups < NUM_DUPS_THRESHOLD` i.e. `num_dups` is 0 or 1) inserts unconditionally with no size check at all: [1](#0-0) 

`prune()` later sorts the entire `nodes` map with `sorted_unstable_by_key`, an `O(n log n)` operation on whatever size `nodes` has grown to: [2](#0-1) 

This is invoked from `CrdsGossipPush::prune_received_cache`, called on every batch of push messages processed on the gossip thread: [3](#0-2) [4](#0-3) 

The `node` key passed to `record` is the `from` field of the wire-level `Protocol::PushMessage(Pubkey, Vec<CrdsValue>)` envelope: [5](#0-4) 

Critically, this outer `from` pubkey is **not cryptographically authenticated**. `Protocol::verify` only checks signatures on the inner `CrdsValue`s, never on the outer envelope pubkey: [6](#0-5) 

And in `process_packets`, the push message's declared `from` pubkey is taken at face value and is never cross-checked against the actual sending `from_addr` or against any known/verified contact info for that pubkey: [7](#0-6) 

So an unprivileged, single attacker (one UDP endpoint) can:
1. Act as the `origin` (sign CrdsValues with their own keypair, e.g. successive `ContactInfo` updates with strictly increasing `wallclock`).
2. For each such genuinely-new value, wrap it in a separate `Protocol::PushMessage(from, [value])` packet, choosing an arbitrary, distinct, unauthenticated `from` pubkey for each packet.
3. Each such push, because the value is new to the CRDS table, results in `crds.insert(...) == Ok(())`, hence `received_cache.record(origin, from, /*num_dups:*/0)` — which hits the *uncapped* branch in `record()`, inserting a brand-new entry into `nodes` for every distinct spoofed `from`.

This defeats the design intent of `CAPACITY = 50`, whose comment states it is meant to "limit how big the cache can get if it is spammed," but the cap is only applied to the num_dups≥2 path, not the "new/timely" path, under the (violated) assumption that the population of distinct legitimate relayers for one origin is naturally bounded by real gossip topology/fanout. Because `from` is unauthenticated, that assumption doesn't hold.

## Impact Explanation

Once `nodes` for an origin has grown to a large size (thousands+), the next `prune_received_cache` call for that origin triggers `ReceivedCacheEntry::prune`'s `sorted_unstable_by_key` over the full inflated `nodes` map, turning the intended `O(CAPACITY log CAPACITY)` bound into `O(n log n)` proportional to attacker-supplied volume. This runs synchronously on the gossip processing thread (inside `handle_batch_push_messages`, which also holds the `received_cache` mutex during `process_push_message` and `prune_received_cache`), so a sufficiently large `nodes` map causes CPU exhaustion / stalling of gossip processing for the node, degrading its participation in the gossip network. This is a remote, non-RPC CPU-exhaustion effect reachable via ordinary gossip packets, consistent with the "unprivileged... exhaust... CPU... through non-RPC public protocols such as... gossip" scope.

## Likelihood Explanation

Moderate-to-high. It requires only:
- A gossip endpoint reachable by any unprivileged peer (standard gossip is open).
- The attacker's own keypair as `origin` (no privilege needed — origin is just whichever pubkey is claimed by an unverified `ContactInfo`/CrdsValue).
- The ability to craft many small UDP packets with distinct `from` fields, each wrapping a validly-signed, wallclock-fresh CrdsValue.
No stake, no existing trust relationship, and no bypass of any signature check is needed, because the vulnerable field (`from` in `Protocol::PushMessage`) is never checked against a signature or the sender's real identity.

## Recommendation
- Bound `nodes.len()` in the `num_dups < NUM_DUPS_THRESHOLD` branch of `record()` as well (i.e., cap unconditionally at `CAPACITY`, not just in the "stale" branch).
- Alternatively/additionally, authenticate the `from` field of `Protocol::PushMessage` (or otherwise correlate it with a verified peer identity, e.g., via signed contact info / ping-pong-verified address) before using it as a tracking key in `ReceivedCache`.

## Proof of Concept
Benchmark-style reasoning (exact repro would be done by a Devin session with build tooling):
1. Instantiate a `CrdsGossipPush` with default config.
2. Simulate `process_push_message` calls where `origin` is a fixed attacker-controlled pubkey with successively increasing-wallclock `ContactInfo`/`CrdsData` values (ensuring `crds.insert` returns `Ok(())` each time, i.e., `num_dups = 0`), while varying `from` to a fresh random `Pubkey::new_unique()` on every call — mirroring what an attacker can send with unauthenticated `from` fields.
3. After inserting on the order of 100k such records for the same `origin`, call `CrdsGossipPush::prune_received_cache` (or `ReceivedCache::prune` directly) and time it, comparing to the baseline case where `nodes.len() <= 50`.
4. Expect wall-clock cost to scale with attacker-controlled `n`, not the intended `CAPACITY`.

The exact numbers (achievable `n` within realistic wallclock/window constraints and per-packet signing cost) were not independently benchmarked here; a background Devin session with the actual crate build would be needed to produce concrete timing numbers, but the code path itself confirms the missing cap on the "timely" branch of `ReceivedCacheEntry::record` and the unauthenticated `from` field are both present as described.

### Citations

**File:** gossip/src/received_cache.rs (L72-87)
```rust
    fn record(&mut self, node: Pubkey, num_dups: usize) {
        if num_dups == 0 {
            self.num_upserts = self.num_upserts.saturating_add(1);
        }
        // If the message has been timely enough increment node's score.
        if num_dups < Self::NUM_DUPS_THRESHOLD {
            let score = self.nodes.entry(node).or_default();
            *score = score.saturating_add(1);
        } else if self.nodes.len() < Self::CAPACITY {
            // Ensure that node is inserted into the cache for later pruning.
            // This intentionally does not negatively impact node's score, in
            // order to prevent replayed messages with spoofed addresses force
            // pruning a good node.
            let _ = self.nodes.entry(node).or_default();
        }
    }
```

**File:** gossip/src/received_cache.rs (L105-119)
```rust
        self.nodes
            .into_iter()
            .map(|(node, score)| {
                let stake = stakes.get(&node).copied().unwrap_or_default();
                (node, score, stake)
            })
            .sorted_unstable_by_key(|&(_, score, stake)| Reverse((score, stake)))
            .scan(0u64, |acc, (node, _score, stake)| {
                let old = *acc;
                *acc = acc.saturating_add(stake);
                Some((node, old))
            })
            .skip(min_ingress_nodes)
            .skip_while(move |&(_, stake)| stake < min_ingress_stake)
            .map(|(node, _stake)| node)
```

**File:** gossip/src/crds_gossip_push.rs (L91-115)
```rust
    pub(crate) fn prune_received_cache<I>(
        &self,
        self_pubkey: &Pubkey,
        origins: I, // Unique pubkeys of crds values' owners.
        stakes: &HashMap<Pubkey, u64>,
    ) -> HashMap</*gossip peer:*/ Pubkey, /*origins:*/ Vec<Pubkey>>
    where
        I: IntoIterator<Item = Pubkey>,
    {
        let mut received_cache = self.received_cache.lock().unwrap();
        origins
            .into_iter()
            .flat_map(|origin| {
                received_cache
                    .prune(
                        self_pubkey,
                        origin,
                        CRDS_GOSSIP_PRUNE_STAKE_THRESHOLD_PCT,
                        CRDS_GOSSIP_PRUNE_MIN_INGRESS_NODES,
                        stakes,
                    )
                    .zip(repeat(origin))
            })
            .into_group_map()
    }
```

**File:** gossip/src/crds_gossip_push.rs (L134-154)
```rust
        for (from, values) in messages {
            self.num_total.fetch_add(values.len(), Ordering::Relaxed);
            for value in values {
                if !wallclock_window.contains(&value.wallclock()) {
                    continue;
                }
                let origin = value.pubkey();
                match crds.insert(value, now, GossipRoute::PushMessage(&from)) {
                    Ok(()) => {
                        received_cache.record(origin, from, /*num_dups:*/ 0);
                        origins.insert(origin);
                    }
                    Err(CrdsError::DuplicatePush(num_dups)) => {
                        received_cache.record(origin, from, usize::from(num_dups));
                        self.num_old.fetch_add(1, Ordering::Relaxed);
                    }
                    Err(CrdsError::InsertFailed) => {
                        received_cache.record(origin, from, /*num_dups:*/ usize::MAX);
                        self.num_old.fetch_add(1, Ordering::Relaxed);
                    }
                }
```

**File:** gossip/src/cluster_info.rs (L1917-1936)
```rust
    fn handle_batch_push_messages(
        &self,
        messages: Vec<(Pubkey, Vec<CrdsValue>)>,
        thread_pool: &ThreadPool,
        recycler: &PacketBatchRecycler,
        stakes: &HashMap<Pubkey, u64>,
        response_sender: &impl ChannelSend<PacketBatch>,
    ) {
        let _st = ScopedTimer::from(&self.stats.handle_batch_push_messages_time);
        if messages.is_empty() {
            return;
        }
        // Origins' pubkeys of upserted crds values.
        let origins: HashSet<_> = {
            let _st = ScopedTimer::from(&self.stats.process_push_message);
            let now = timestamp();
            self.gossip.process_push_message(messages, now)
        };
        // Generate prune messages.
        let prune_messages = self.generate_prune_messages(thread_pool, origins, stakes);
```

**File:** gossip/src/cluster_info.rs (L2113-2124)
```rust
                Protocol::PushMessage(from, mut data) => {
                    if should_check_duplicate_instance {
                        check_duplicate_instance(&data)?;
                    }
                    data.retain(&mut verify_gossip_addr);
                    if !data.is_empty() {
                        self.stats
                            .push_message_value_count
                            .add_relaxed(data.len() as u64);
                        push_messages.push((from, data));
                    }
                }
```

**File:** gossip/src/protocol.rs (L135-147)
```rust
    // Returns true if all signatures verify.
    #[must_use]
    pub(crate) fn verify(&self, cache: &SigVerifyCache) -> bool {
        match self {
            Self::PullRequest(_, caller) => caller.verify_with_cache(cache),
            Self::PullResponse(_, data) => data.iter().all(|value| value.verify_with_cache(cache)),
            Self::PushMessage(_, data) => data.iter().all(|value| value.verify_with_cache(cache)),
            Self::PruneMessage(_, data) => data.verify(),
            Self::PingMessage(ping) => ping.verify(),
            Self::PongMessage(pong) => pong.verify(),
        }
    }
}
```
