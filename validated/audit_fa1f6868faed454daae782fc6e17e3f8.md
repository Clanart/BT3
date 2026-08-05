## Title
Unauthenticated `from` field in gossip `PushMessage` allows arbitrary node impersonation, corrupting push-redundancy scoring and triggering forged `PruneMessage` delivery to victim validators - (File: `gossip/src/protocol.rs`, `gossip/src/crds_gossip_push.rs`, `gossip/src/cluster_info.rs`)

## Summary
The RaptorCast Secondary bug class — an identity field carried inside a signed message that is never checked against the actual cryptographic signer — has a direct analog in Agave's gossip `PushMessage` handling. `Protocol::PushMessage(Pubkey, Vec<CrdsValue>)` carries a `from` pubkey that is completely independent of the per-`CrdsValue` signatures that `Protocol::verify()` actually checks, so any unauthenticated UDP sender can claim to be any other validator's `from` identity.

## Finding Description
`Protocol::verify()` only validates the signatures of the individual `CrdsValue`s inside a push message; it never checks the outer `from` pubkey against anything: [1](#0-0) 

`Sanitize::sanitize()` for `Protocol` likewise only checks the `from` field for `PruneMessage` (`*from != val.pubkey`), but explicitly skips this for `PushMessage`/`PullResponse`: [2](#0-1) 

When an incoming packet is deserialized in `run_socket_consume`/`verify_packet`, `PushMessage(from, values)` is passed through with `from` untouched, and only `protocol.verify(sigverify_cache)` (i.e. only per-value signatures) gates delivery: [3](#0-2) 

The unauthenticated `from` is then used as ground truth in `CrdsGossipPush::process_push_message`, feeding `received_cache.record(origin, from, num_dups)`: [4](#0-3) 

`received_cache` scores are later consumed by `prune_received_cache`/`ReceivedCacheEntry::prune`, which is stake-weighted and decides which gossip peers should be sent an actual, correctly-signed `PruneMessage`: [5](#0-4) [6](#0-5) 

`generate_prune_messages` resolves the pruned pubkey's *real* gossip address from the CRDS table and sends it a genuinely signed `PruneMessage`: [7](#0-6) 

## Impact Explanation
Because `from` is never bound to the packet's actual sender, an attacker (no stake, no valid identity required) can:
- Impersonate any validator's pubkey as the `from` of a `PushMessage` carrying a legitimately-signed `CrdsValue` (rebroadcast/replay of any observed value), poisoning that validator's redundancy score in the victim node's `received_cache` without the victim ever sending anything.
- Cause the receiving node to make pruning decisions (`prune_received_cache`) based on forged provenance data, and to emit real, correctly-signed `PruneMessage`s to the impersonated validator's genuine gossip address, altering that validator's local push active-set state for origins it never actually relayed.
- At scale, repeatedly forging `from` for many pubkeys degrades the accuracy of gossip's stake-weighted push-redundancy control, creating noise/confusion in gossip propagation paths analogous to the RaptorCast group-poisoning attack, at effectively zero cost to the attacker (an open gossip UDP port is sufficient, no valid keypair required for the `from` field itself).

This is a lower-severity analog (protocol-integrity/noise degradation of gossip's push mechanism, not itself a crash or consensus halt), matching the "Medium-Low" characterization of the original RaptorCast report rather than a high-severity primitive.

## Likelihood Explanation
High: no privileged position, stake, or valid keypair is needed to set `from`; only a signature on the (attacker-obtainable) `CrdsValue` payload itself is checked, and that payload can simply be replayed from any legitimately observed push message while forging the outer `from` pubkey.

## Recommendation
Bind the `from` field to the actual signer/sender the same way `PruneData`/`PruneMessage` already does, e.g., either remove reliance on the unauthenticated `from` in `received_cache`/pruning decisions, or require `from` to be validated against a signed sender assertion (or against the packet's originating gossip contact-info entry with matching signature) before it is used for any scoring/pruning/trust decision, mirroring the `Protocol::PruneMessage` sanitize check (`*from != val.pubkey`).

## Proof of Concept
1. Observe any legitimate `Protocol::PushMessage(real_from, values)` on the wire (values are individually signed by their true origins and remain valid regardless of `from`).
2. Craft a new packet `Protocol::PushMessage(victim_pubkey, values)` reusing the same (still validly signed) `values`, substituting an arbitrary `victim_pubkey` for `from`.
3. Send it to a target node's gossip port from any address (no valid keypair for `victim_pubkey` required).
4. `verify_packet` accepts it because only `value.verify_with_cache` is checked [3](#0-2) ; `process_push_message` records `received_cache.record(origin, victim_pubkey, ...)` as if `victim_pubkey` had relayed it [8](#0-7) .
5. Repeat/vary origins and forged `from` pubkeys to skew `prune_received_cache` outcomes, causing the target to emit genuine signed `PruneMessage`s to arbitrary validators' real gossip addresses based on forged provenance.

### Citations

**File:** gossip/src/protocol.rs (L136-146)
```rust
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
```

**File:** gossip/src/protocol.rs (L197-230)
```rust
impl Sanitize for Protocol {
    fn sanitize(&self) -> Result<(), SanitizeError> {
        match self {
            Protocol::PullRequest(filter, val) => {
                filter.sanitize()?;
                // PullRequest is only allowed to have ContactInfo in its CrdsData
                match val.data() {
                    CrdsData::ContactInfo(_) => val.sanitize(),
                    _ => Err(SanitizeError::InvalidValue),
                }
            }
            Protocol::PullResponse(_, val) => {
                // PullResponse is allowed to carry anything in its CrdsData, including deprecated Crds
                // such that a deprecated Crds does not get pulled and then rejected.
                val.sanitize()
            }
            Protocol::PushMessage(_, val) => {
                // PushMessage is allowed to carry anything in its CrdsData, including deprecated Crds
                // such that a deprecated Crds gets ingested instead of the node having to pull it from
                // other nodes that have inserted it into their Crds table
                val.sanitize()
            }
            Protocol::PruneMessage(from, val) => {
                if *from != val.pubkey {
                    Err(SanitizeError::InvalidValue)
                } else {
                    val.sanitize()
                }
            }
            Protocol::PingMessage(ping) => ping.sanitize(),
            Protocol::PongMessage(pong) => pong.sanitize(),
        }
    }
}
```

**File:** gossip/src/cluster_info.rs (L1951-2012)
```rust
    fn generate_prune_messages(
        &self,
        thread_pool: &ThreadPool,
        // Unique origin pubkeys of upserted CRDS values from push messages.
        origins: impl IntoIterator<Item = Pubkey>,
        stakes: &HashMap<Pubkey, u64>,
    ) -> Vec<(SocketAddr, Protocol /*::PruneMessage*/)> {
        let _st = ScopedTimer::from(&self.stats.generate_prune_messages);
        let self_keypair = self.keypair();
        let self_pubkey = self_keypair.pubkey();
        // Obtain redundant gossip links which can be pruned.
        let prunes: HashMap</*gossip peer:*/ Pubkey, /*origins:*/ Vec<Pubkey>> = {
            let _st = ScopedTimer::from(&self.stats.prune_received_cache);
            self.gossip
                .prune_received_cache(&self_pubkey, origins, stakes)
        };
        // Look up gossip addresses of destination nodes.
        let prunes: Vec<(
            Pubkey,      // gossip peer to be pruned
            SocketAddr,  // gossip socket-addr of peer
            Vec<Pubkey>, // CRDS value origins
        )> = {
            let gossip_crds = self.gossip.crds.read().unwrap();
            thread_pool.install(|| {
                prunes
                    .into_par_iter()
                    .filter_map(|(pubkey, prunes)| {
                        let addr = get_node_addr(
                            pubkey,
                            ContactInfo::gossip,
                            &gossip_crds,
                            &self.socket_addr_space,
                        )?;
                        Some((pubkey, addr, prunes))
                    })
                    .collect()
            })
        };
        // Create and sign Protocol::PruneMessages.
        thread_pool.install(|| {
            let wallclock = timestamp();
            prunes
                .into_par_iter()
                .flat_map(|(destination, addr, prunes)| {
                    // Chunk up origins so that each chunk fits into a packet.
                    let prunes = prunes.into_par_iter().chunks(MAX_PRUNE_DATA_NODES);
                    rayon::iter::repeat((destination, addr)).zip(prunes)
                })
                .map(|((destination, addr), prunes)| {
                    let mut prune_data = PruneData {
                        pubkey: self_pubkey,
                        prunes,
                        signature: Signature::default(),
                        destination,
                        wallclock,
                    };
                    prune_data.sign(&self_keypair);
                    let prune_message = Protocol::PruneMessage(self_pubkey, prune_data);
                    (addr, prune_message)
                })
                .collect()
        })
```

**File:** gossip/src/cluster_info.rs (L2189-2208)
```rust
            if let Protocol::PullResponse(_, values) | Protocol::PushMessage(_, values) =
                &mut protocol
            {
                values.retain(|value| {
                    should_retain_crds_value(
                        value,
                        stakes,
                        GossipFilterDirection::Ingress,
                        is_full_alpenglow_epoch,
                    )
                });
                if values.is_empty() {
                    return None;
                }
            }
            protocol.verify(sigverify_cache).then(|| {
                stats.packets_received_verified_count.add_relaxed(1);
                (packet.meta().socket_addr(), protocol)
            })
        }
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

**File:** gossip/src/crds_gossip_push.rs (L124-158)
```rust
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

**File:** gossip/src/received_cache.rs (L64-97)
```rust
impl ReceivedCacheEntry {
    // Limit how big the cache can get if it is spammed
    // with old messages with random pubkeys.
    const CAPACITY: usize = 50;
    // Threshold for the number of duplicates before which a message
    // is counted as timely towards node's score.
    const NUM_DUPS_THRESHOLD: usize = 2;

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

    fn prune(
        self,
        pubkey: &Pubkey, // This node.
        origin: &Pubkey, // CRDS value owner.
        stake_threshold: f64,
        min_ingress_nodes: usize,
        stakes: &HashMap<Pubkey, u64>,
    ) -> impl Iterator<Item = Pubkey> + use<> {
        debug_assert!((0.0..=1.0).contains(&stake_threshold));
```
