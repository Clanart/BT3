## Title
Unbounded, unstaked gossip contact-info list drives an O(n log n) node sort/dedup on every turbine cache refresh - ([File: turbine/src/cluster_nodes.rs])

## Summary
The Union Finance report describes a griefing vector where an unprivileged actor grows an attacker-influenced list (stakers/borrowers) that is later fed into an expensive sort routine, and the only defense is a size cap (`MAX_TRUST_LIMIT`) whose adequacy was never rigorously proven. The Agave analog is `get_nodes`/`sort_and_dedup_nodes` in `turbine/src/cluster_nodes.rs`, which builds the turbine/retransmit node list from **all** gossip TVU peers — not just staked ones — and sorts/dedups that list every time the `ClusterNodesCache` entry is (re)built. Unlike stake-weighted structures elsewhere in the code (e.g. `VoteAccounts::clone_and_filter_for_vat`), this list has no hard cap tied to an economic cost; it is bounded only by whatever the gossip CRDS table happens to admit.

## Finding Description
`new_cluster_nodes` calls `get_nodes(cluster_info, cluster_type, stakes)`: [1](#0-0) 

`get_nodes` concatenates three sources: the local node, **all** `cluster_info.tvu_peers(...)` (any gossip peer with a valid TVU contact info, staked or not), and all staked nodes from the epoch stake map, then sorts the combined list by stake (descending) and dedups it: [2](#0-1) 

Membership in `cluster_info.tvu_peers(...)` requires only that a node be present in the local gossip CRDS table with a valid contact info — it does **not** require any stake. Any number of unstaked nodes can join gossip (subject only to the CRDS table's per-node/global capacity limits enforced in `gossip/src/crds.rs`'s `trim`/`drop`), inflating the `nodes` vector that `sort_and_dedup_nodes` must process. This mirrors the Union Finance pattern precisely: an attacker-controlled, cheaply-grown list feeds a sort whose cost scales with list length, and the only defense is a size limit (the CRDS table's admission/eviction policy) that was tuned for gossip liveness, not for bounding this specific sort's cost.

This sort/dedup work is re-executed on every cache miss or TTL expiry of `ClusterNodesCache`, which uses a 5-second TTL and a small epoch-count cap: [3](#0-2) 

That means the cost is not a one-time epoch-boundary expense; it recurs roughly every 5 seconds per node type (broadcast/retransmit) across the whole validator fleet, for as long as the gossip table remains inflated with unstaked contact infos.

## Impact Explanation
This does not corrupt consensus-critical state or steal funds, but it is a real-time-critical hot path (turbine broadcast/retransmit tree construction) executed by every validator on every shred-relevant cache refresh. If the gossip table is inflated with a large number of cheap, unstaked contact infos, `get_nodes`/`sort_and_dedup_nodes`/`dedup_tvu_addrs` becomes proportionally more expensive, degrading the turbine data-plane construction on every node network-wide and potentially delaying shred propagation — a non-RPC, network-wide performance-degradation vector, analogous in class (though not necessarily severity) to the Union Finance griefing report: an unprivileged, cheap operation (joining gossip) inflates a list that feeds an expensive routine whose cost is bounded only by an admission cap not specifically designed for this purpose.

## Likelihood Explanation
Joining gossip with a valid contact info is significantly cheaper than staking economically meaningful SOL, so growing the `tvu_peers` set is comparatively low-cost for an attacker running many lightweight nodes/IPs (subject to `MAX_NUM_NODES_PER_IP_ADDRESS` dedup and CRDS admission/eviction limits). The actual severity depends on tunable constants (CRDS table capacity, `dedup_tvu_addrs` cost) that were not fully verified from the available index — I could not confirm the exact value of `CRDS_UNIQUE_PUBKEY_CAPACITY` or the full complexity of `dedup_tvu_addrs` within this session, so the magnitude of the resulting slowdown is uncertain without further investigation (which would require a live Devin session with full file access).

## Recommendation
- Restrict the turbine/broadcast node list construction to staked nodes plus a small, capped number of unstaked contact infos (similar in spirit to `VoteAccounts::clone_and_filter_for_vat`'s `select_nth_unstable_by` truncation with an explicit `max_vote_accounts` bound), rather than including the entire unfiltered `tvu_peers()` result.
- Benchmark `get_nodes`/`sort_and_dedup_nodes`/`dedup_tvu_addrs` at the maximum CRDS table capacity to quantify worst-case cache-refresh latency, mirroring the recommendation in the original report to test at the configured cap.
- Consider using a partial/O(n) selection (as already done elsewhere in this codebase) instead of a full sort when only relative stake ordering for the weighted shuffle is needed.

## Proof of Concept
Conceptual (not exploited/verified against a live cluster in this session):
1. Spin up many low-cost nodes that only advertise valid `ContactInfo` with TVU sockets in gossip, without acquiring meaningful stake.
2. These nodes are admitted into the local CRDS table of every validator (subject to `Crds::trim`/`drop` capacity limits) and are returned by `cluster_info.tvu_peers(...)`. [4](#0-3) 
3. Every 5 seconds (`ClusterNodesCache` TTL) or on cache miss, each validator rebuilds `ClusterNodes` by calling `get_nodes` → `sort_and_dedup_nodes`, whose cost scales with the inflated peer count. [5](#0-4) 
4. Repeated across the whole validator set, this creates a persistent, attacker-amplifiable CPU cost on the turbine hot path — the Agave analog of the Union Finance "expensive sort over an attacker-inflatable list bounded only by an insufficiently-tested cap" pattern.

### Citations

**File:** turbine/src/cluster_nodes.rs (L371-417)
```rust
// All staked nodes + other known tvu-peers + the node itself;
// sorted by (stake, pubkey) in descending order.
fn get_nodes(
    cluster_info: &ClusterInfo,
    cluster_type: ClusterType,
    stakes: &HashMap<Pubkey, u64>,
) -> Vec<Node> {
    let self_pubkey = cluster_info.id();
    let should_dedup_tvu_addrs = match cluster_type {
        ClusterType::Development => false,
        ClusterType::Devnet | ClusterType::Testnet | ClusterType::MainnetBeta => true,
    };
    let mut nodes: Vec<Node> = std::iter::once({
        // The local node itself.
        let stake = stakes.get(&self_pubkey).copied().unwrap_or_default();
        let node = ContactInfo::from(&cluster_info.my_contact_info());
        let node = NodeId::from(node);
        Node { node, stake }
    })
    // All known tvu-peers from gossip.
    .chain(
        cluster_info
            .tvu_peers(|node| ContactInfo::from(node))
            .into_iter()
            .map(|node| {
                let stake = stakes.get(node.pubkey()).copied().unwrap_or_default();
                let node = NodeId::from(node);
                Node { node, stake }
            }),
    )
    // All staked nodes.
    .chain(
        stakes
            .iter()
            .filter(|(_, stake)| **stake > 0)
            .map(|(&pubkey, &stake)| Node {
                node: NodeId::from(pubkey),
                stake,
            }),
    )
    .collect();
    sort_and_dedup_nodes(&mut nodes);
    if should_dedup_tvu_addrs {
        dedup_tvu_addrs(&mut nodes);
    };
    nodes
}
```

**File:** turbine/src/cluster_nodes.rs (L419-426)
```rust
// Sorts nodes by highest stakes first and dedups by pubkey.
fn sort_and_dedup_nodes(nodes: &mut Vec<Node>) {
    nodes.sort_unstable_by(|a, b| cmp_nodes_stake(b, a));
    // dedup_by keeps the first of consecutive elements which compare equal.
    // Because if all else are equal above sort puts NodeId::ContactInfo before
    // NodeId::Pubkey, this will keep nodes with contact-info.
    nodes.dedup_by(|a, b| a.pubkey() == b.pubkey());
}
```

**File:** turbine/src/cluster_nodes.rs (L545-624)
```rust
impl<T> ClusterNodesCache<T> {
    pub fn new(
        // Capacity of underlying LRU-cache in terms of number of epochs.
        cap: usize,
        // A time-to-live eviction policy is enforced to refresh entries in
        // case gossip contact-infos are updated.
        ttl: Duration,
    ) -> Self {
        Self {
            cache: RwLock::new(LruCache::new(cap)),
            ttl,
        }
    }
}

impl<T: 'static> ClusterNodesCache<T> {
    pub(crate) fn get(
        &self,
        shred_slot: Slot,
        root_bank: &Bank,
        working_bank: &Bank,
        cluster_info: &ClusterInfo,
    ) -> Arc<ClusterNodes<T>> {
        // Returns the cached entry for the epoch if it is either uninitialized
        // or not expired yet. Discards the entry if it is already initialized
        // but also expired.
        let get_epoch_entry = |cache: &LruCache<Epoch, _>, epoch, ttl| {
            let entry: &Arc<OnceLock<(Instant, _)>> = cache.get(&epoch)?;
            let Some((asof, _)) = entry.get() else {
                return Some(entry.clone()); // not initialized yet
            };
            (asof.elapsed() < ttl).then(|| entry.clone())
        };
        let epoch_schedule = root_bank.epoch_schedule();
        let epoch = epoch_schedule.get_epoch(shred_slot);
        // Read from the cache with a shared lock.
        let entry = {
            let cache = self.cache.read().unwrap();
            get_epoch_entry(&cache, epoch, self.ttl)
        };
        let use_cha_cha_8 = check_feature_activation_from_bank(
            &feature_set::switch_to_chacha8_turbine::ID,
            shred_slot,
            root_bank,
        );
        // Fall back to exclusive lock if there is a cache miss or the cached
        // entry has already expired.
        let entry: Arc<OnceLock<_>> = entry.unwrap_or_else(|| {
            let mut cache = self.cache.write().unwrap();
            get_epoch_entry(&cache, epoch, self.ttl).unwrap_or_else(|| {
                // Either a cache miss here or the existing entry has already
                // expired. Upsert and return an uninitialized entry.
                let entry = Arc::<OnceLock<_>>::default();
                cache.put(epoch, Arc::clone(&entry));
                entry
            })
        });
        // Initialize if needed by only a single thread outside locks.
        let (_, nodes) = entry.get_or_init(|| {
            let epoch_staked_nodes = [root_bank, working_bank]
                .iter()
                .find_map(|bank| bank.epoch_staked_nodes(epoch))
                .unwrap_or_else(|| {
                    error!(
                        "ClusterNodesCache::get: unknown Bank::epoch_staked_nodes for epoch: \
                         {epoch}, slot: {shred_slot}"
                    );
                    inc_new_counter_error!("cluster_nodes-unknown_epoch_staked_nodes", 1);
                    Arc::<HashMap<Pubkey, /*stake:*/ u64>>::default()
                });
            let nodes = new_cluster_nodes::<T>(
                cluster_info,
                root_bank.cluster_type(),
                &epoch_staked_nodes,
                use_cha_cha_8,
            );
            (Instant::now(), Arc::new(nodes))
        });
        nodes.clone()
    }
```
