### Title
Non-stake-weighted "score" in gossip `ReceivedCache` lets zero-stake nodes guarantee permanent ingress slots, bypassing the stake-weighted anti-eclipse protection - (File: `gossip/src/received_cache.rs`)

### Summary
`ReceivedCache` tracks, per CRDS `origin`, a `score` for each peer (`node`) that relays that origin's values to us, incremented on every "timely" delivery regardless of the relaying peer's stake. `CrdsGossipPush::prune_received_cache` uses this score as the *primary* sort key (stake is only a tiebreaker) when deciding which ingress peers to keep vs. prune, and unconditionally exempts the top `CRDS_GOSSIP_PRUNE_MIN_INGRESS_NODES` (2) highest-scoring peers from pruning, independent of their stake. This mirrors the AgentDAO pattern: a purely activity-based counter (score), incrementable by an unprivileged/low-cost action, is used to make a trust/priority decision that is supposed to be stake-weighted, without any minimum-weight gate.

### Finding Description
`ReceivedCacheEntry::record` increments a peer's `score` any time it forwards a "timely" (non-duplicate-flooded) copy of a CRDS value for a given origin, with no stake check at record time: [1](#0-0) 

`ReceivedCacheEntry::prune` (called from `CrdsGossipPush::prune_received_cache`) then ranks all recorded peers for that origin by `Reverse((score, stake))` — i.e., **score dominates, stake is only a tiebreaker** — and always exempts the top `min_ingress_nodes` (2) from the prune list via `.skip(min_ingress_nodes)`, before even applying the `min_ingress_stake` threshold check: [2](#0-1) 

The call site wires this into the live gossip push protocol: [3](#0-2) 

The comment in `prune` explicitly references the stake-based anti-Sybil protection from solana-labs/solana#3214 ("Enforce a minimum aggregate ingress stake"), but the sort key defeats that intent for the guaranteed `min_ingress_nodes` slots: any peer — including one with zero stake — that simply relays messages faster/more consistently than legitimate high-stake peers for a given origin will rank #1 or #2 by `score` and is therefore **never pruned**, regardless of how low its stake is relative to `stake_threshold`.

### Impact Explanation
This breaks the invariant that gossip ingress diversity for a CRDS origin is stake-weighted (an anti-eclipse/anti-Sybil control). A zero/low-stake attacker that runs a low-latency relay and consistently wins the "first to deliver" race for a target validator's CRDS values can lock in one of only two protected ingress slots for that origin, permanently displacing a legitimate high-stake relay from that slot. Because gossip ingress paths influence what CRDS/contact-info data a validator ultimately observes and propagates, an attacker occupying a guaranteed, stake-immune ingress slot for a victim origin increases surface for eclipse-style manipulation of gossip data reaching that node, undermining the specific stake-weighted protection the code claims to implement.

### Likelihood Explanation
The action needed (relaying push messages quickly/non-duplicated) is unprivileged and requires no stake — any gossip participant can do it — making the primitive cheap to execute repeatedly against many origins in parallel. However, actually reaching the #1/#2 rank for a specific victim origin requires winning the "fastest non-duplicate delivery" race against genuine, well-connected high-stake relays, which is competitive and network/latency-dependent, and only 2 guaranteed slots exist per origin — moderating exploitability compared to the original AgentDAO case where any address trivially gets +1 score for free.

### Recommendation
Do not let raw delivery-count `score` outrank stake in the prune ordering. Either (a) weight `score` by the relaying peer's stake before comparing (e.g., sort by `(score, stake)` with stake as primary or a combined metric), or (b) require the `min_ingress_nodes` exemption to also satisfy a minimum stake floor (not stake-agnostic), so that a purely activity-based score cannot alone guarantee an ingress slot regardless of stake.

### Proof of Concept
Conceptual walk-through based on the code paths above (no live cluster access to execute):
1. Attacker controls two nodes: `Origin` (any keypair, arbitrary stake) and `Attacker` (0 stake), plus observes `Victim`'s gossip traffic pattern.
2. `Origin` periodically emits CRDS values (e.g., contact-info updates).
3. `Attacker` maintains a low-latency direct link to `Victim` and consistently forwards `Origin`'s freshly-seen values to `Victim` before other (legitimate, higher-stake) relays can, triggering `ReceivedCache::record(origin, Attacker, num_dups=0)` on every delivery — see `crds_gossip_push.rs:141-145`.
4. Over `MIN_NUM_UPSERTS` (20) deliveries, `Attacker`'s `score` for `origin=Origin` becomes the highest recorded, per `received_cache.rs:72-87`.
5. When `Victim` runs `prune_received_cache` for `Origin`, `Attacker` sorts to a top-2 position by `Reverse((score, stake))` and is excluded from the prune list via `.skip(min_ingress_nodes)` in `received_cache.rs:105-119`, regardless of `Attacker`'s stake being 0 and below `min_ingress_stake`.
6. Result: `Attacker` retains a guaranteed ingress slot into `Victim`'s view of `Origin`'s gossip data with zero stake, contrary to the stake-weighted design intent documented in the code comments (reference to solana-labs/solana#3214).

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

**File:** gossip/src/received_cache.rs (L89-120)
```rust
    fn prune(
        self,
        pubkey: &Pubkey, // This node.
        origin: &Pubkey, // CRDS value owner.
        stake_threshold: f64,
        min_ingress_nodes: usize,
        stakes: &HashMap<Pubkey, u64>,
    ) -> impl Iterator<Item = Pubkey> + use<> {
        debug_assert!((0.0..=1.0).contains(&stake_threshold));
        debug_assert!(self.num_upserts >= ReceivedCache::MIN_NUM_UPSERTS);
        // Enforce a minimum aggregate ingress stake; see:
        // https://github.com/solana-labs/solana/issues/3214
        let min_ingress_stake = {
            let stake = stakes.get(pubkey).min(stakes.get(origin));
            (stake.copied().unwrap_or_default() as f64 * stake_threshold) as u64
        };
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
    }
```

**File:** gossip/src/crds_gossip_push.rs (L91-158)
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

    fn wallclock_window(&self, now: u64) -> impl RangeBounds<u64> + use<> {
        now.saturating_sub(self.msg_timeout)..=now.saturating_add(self.msg_timeout)
    }

    /// Process a push message to the network.
    ///
    /// Returns origins' pubkeys of upserted values.
    pub(crate) fn process_push_message(
        &self,
        crds: &RwLock<Crds>,
        messages: Vec<(/*from:*/ Pubkey, Vec<CrdsValue>)>,
        now: u64,
    ) -> HashSet<Pubkey> {
        let mut received_cache = self.received_cache.lock().unwrap();
        let mut crds = crds.write().unwrap();
        let wallclock_window = self.wallclock_window(now);
        let mut origins = HashSet::new();
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
            }
        }
        origins
    }
```
