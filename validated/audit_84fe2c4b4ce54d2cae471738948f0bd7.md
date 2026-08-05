## Title
Per-source-IP gossip pull-request scan-budget can be exhausted and bypassed via trivial IP rotation, enabling unauthenticated denial-of-service against CRDS pull-response generation - (File: `gossip/src/cluster_info.rs`)

## Summary
`ClusterInfo::handle_pull_requests` protects the cost of scanning the CRDS table with a `pull_request_budget: KeyedRateLimiter<IpAddr>` that is keyed strictly by the source IP address of the incoming UDP packet. [1](#0-0)  This mirrors the report's bug class exactly: a resource-protection check ("no more than N items scanned per unit time") is enforced only against one identifying key (the IP), while the resource actually being protected (CPU time spent scanning/filtering the shared CRDS table in `generate_pull_responses`) is shared across all keys. Because gossip `PullRequest` packets are received over UDP with no connection/ownership proof prior to the ping-cache check being satisfied, and the ping-cache check and the scan-budget check are independent, an attacker can spread pull-request traffic across many distinct source IPs to make the per-IP budget check pass every time, defeating the purpose of the limiter, analogous to using "alias accounts" to bypass the daily-allowance check in the original report.

## Finding Description
`try_consume_pull_request_scan_budget` computes a cost from the bloom filter size and number of scanned CRDS entries and consumes tokens from a `KeyedRateLimiter<IpAddr>` keyed on `request.addr.ip()`: [2](#0-1) 

This budget is deliberately documented as being isolated per IP address, based on the assumption that "validators are assumed not to share IPs": [3](#0-2) 

The unit test `test_pull_request_scan_budget_exhaustion_isolated_per_ip` explicitly demonstrates and asserts this isolation: after exhausting the budget for one source IP, an otherwise-identical request from a second IP address (same pubkey, same filter) still succeeds and does not trip `pull_request_scan_budget_exhausted`: [4](#0-3) 

Before the scan-budget check runs, `handle_pull_requests` only filters requests through `check_pull_request`, which performs a ping-pong return-routability check keyed by `(pubkey, addr)`: [5](#0-4) 

Critically, the return-routability check is keyed by `(pubkey, addr)`, not solely by `addr`. An attacker fully controls the `pubkey` field inside the signed `LegacyContactInfo`/`ContactInfo` payload of the `PullRequest` (the `caller` CRDS value), so they can mint an unbounded number of distinct `(pubkey, addr)` pairs (i.e., new keypairs) from the same or different source addresses, and separately can vary the source IP itself (which for gossip's UDP packets is not otherwise authenticated by a QUIC-style handshake). Once `check_pull_request` passes for a given `(pubkey, addr)` (which requires having previously answered a `Ping`), the scan cost check that follows is keyed *only* by IP — so an attacker who owns (or spoofs to the extent the network permits, e.g. via cloud IP churn or IPv6 address rotation) multiple source IPs, or who distributes the same attack across many colluding low-stake/unstaked nodes, gets an independent, full budget allotment (`GOSSIP_PULL_SCAN_BUDGET_CAPACITY` / refill rate) *per IP*, even though all of that scanning work is performed against the same single, node-wide CRDS table (`Crds`) and burns the same CPU on the target validator.

This is the structural analog of the report: the resource-protection invariant ("total pull-request-driven CRDS scan work per unit time is bounded") is enforced via a check keyed on an attacker-controlled/attacker-multipliable identifier (source IP) rather than on the actual shared resource being protected, exactly as `mint()`'s daily-allowance check was enforced per-account while the protected resource (total items minted) could be moved between accounts through the unchecked `safeTransferFrom()` path. Here there is no missing check on a second "path" per se; rather the same architectural weakness — a per-key throttle guarding a shared, global-cost resource — allows the guard to be trivially multiplied away by acquiring more keys (IP addresses / identities), just as the original bug allowed the guard to be trivially multiplied away by acquiring more accounts.

## Impact Explanation
An attacker who obtains many source IP addresses (cheap via cloud providers or IPv6 prefixes, which trivially grant billions of distinct addresses) can each be paired with a cheap ping-verified `(pubkey, addr)` pair and then send bloom-filter pull requests that each individually stay within the per-IP budget but in aggregate cause unbounded CRDS scanning work (`generate_pull_responses` / `filter_crds_values`) on the target validator, well beyond the single global `GOSSIP_PULL_SCAN_BUDGET_CAPACITY` that the mechanism was designed to enforce. This is a non-RPC, remote, unprivileged CPU-exhaustion vector against the gossip service, which can degrade a validator's ability to process legitimate gossip traffic (CRDS propagation, contact-info/vote gossip), and at scale can be a route to broader network degradation.

## Likelihood Explanation
Likelihood is moderate: it requires the attacker to acquire and use multiple source IP addresses (readily available via cloud egress IP pools or IPv6 allocations) and pass the lightweight ping-pong return-routability check for each `(pubkey, addr)` pair used, which is inexpensive (a single UDP round trip per pair) and explicitly designed only to prevent blind IP spoofing, not to prevent an attacker from owning many real addresses. No stake, no special privilege, and no cluster membership are required — this is reachable by any unauthenticated UDP client that can exchange gossip packets with a validator's gossip port.

## Recommendation
- Do not rely solely on source IP as the rate-limiting key for a resource (CRDS scan cost) that is shared/global on the node. Add (or fall back to) a global token bucket bounding total pull-request scan cost across all IPs, in addition to the existing per-IP bucket, similar to how the QUIC streamer combines a global `overall_connection_rate_limiter` with the per-IP `ConnectionRateLimiter`.
- Consider additionally keying/limiting by verified pubkey (post ping-pong) with a stake-weighted budget, so unstaked/low-stake identities cannot each claim a full-size scan budget regardless of how many IPs or keypairs they rotate through.
- Re-evaluate whether the "validators are assumed not to share IPs" comment should instead be "attackers are assumed not to have many IPs," which is false in practice, and design the limiter accordingly.

## Proof of Concept
The existing regression test already proves the isolation-per-IP behavior that underlies the bypass: [6](#0-5) 

To weaponize this in the field: an attacker (1) generates `k` distinct keypairs and pairs each with a distinct source IP (real or cloud-allocated) they control; (2) for each pair, responds to the one-time `Ping` challenge issued by `check_pull_request`/`ping_cache.check` to become "verified" for that `(pubkey, addr)`; (3) then continuously sends maximally-costly `PullRequest` bloom filters from each IP. Each IP independently gets the full `GOSSIP_PULL_SCAN_BUDGET_CAPACITY`/`GOSSIP_PULL_SCAN_BUDGET_REFILL_PER_SEC` allotment (per `try_consume_pull_request_scan_budget`), so total sustained CRDS-scan cost imposed on the victim scales linearly with `k`, unbounded by the single-IP design limit.

Uncertainty note: I was not able to fully verify, purely from the indexed code, the exact upstream rate at which an attacker can mint new verified `(pubkey, addr)` ping-cache entries per unit time (i.e., whether `PingCache` itself imposes any additional global cap on outstanding/verified entries beyond its LRU capacity `GOSSIP_PING_CACHE_CAPACITY`), which would affect the practical amplification factor. A Devin session with full repository access could confirm this by inspecting `gossip/src/ping_pong.rs` in full and any admission control on `PingCache::mock_pong`/`check` insertion paths.

### Citations

**File:** gossip/src/cluster_info.rs (L127-134)
```rust
pub(crate) const GOSSIP_PING_CACHE_OUTSTANDING_PING_TIMEOUT_MS: Range<u64> = 1000..2000;
// Per-IP scan budget for incoming pull requests; validators are assumed not
// to share IPs. Mirrors the ping-pong cache capacity.
const GOSSIP_PULL_SCAN_BUDGET_CACHE_CAPACITY: usize = GOSSIP_PING_CACHE_CAPACITY;
const GOSSIP_PULL_SCAN_BUDGET_CAPACITY: u64 = 16 * crds_gossip_pull::MIN_NUM_BLOOM_ITEMS as u64;
const GOSSIP_PULL_SCAN_BUDGET_REFILL_PER_SEC: u64 =
    4 * crds_gossip_pull::MIN_NUM_BLOOM_ITEMS as u64;
const GOSSIP_PULL_SCAN_BUDGET_SHARD_COUNT: usize = 64;
```

**File:** gossip/src/cluster_info.rs (L184-227)
```rust
    outbound_budget: DataBudget,
    my_contact_info: RwLock<ContactInfo>,
    ping_cache: Mutex<PingCache>,
    pull_request_budget: KeyedRateLimiter<IpAddr>,
    pub(crate) stats: GossipStats,
    local_message_pending_push_queue: Mutex<Vec<CrdsValue>>,
    contact_debug_interval: u64, // milliseconds, 0 = disabled
    contact_save_interval: u64,  // milliseconds, 0 = disabled
    contact_info_path: PathBuf,
    socket_addr_space: SocketAddrSpace,
    bind_ip_addrs: Arc<BindIpAddrs>,
    sigverify_cache: SigVerifyCache,
    /// Alpenglow migration status
    migration_status: OnceLock<Arc<MigrationStatus>>,
}

impl ClusterInfo {
    pub fn new(
        contact_info: ContactInfo,
        keypair: Arc<Keypair>,
        socket_addr_space: SocketAddrSpace,
    ) -> Self {
        assert_eq!(contact_info.pubkey(), &keypair.pubkey());
        let me = Self {
            gossip: CrdsGossip::default(),
            keypair: ArcSwap::from(keypair),
            entrypoints: RwLock::default(),
            known_validators: OnceLock::new(),
            outbound_budget: DataBudget::default(),
            my_contact_info: RwLock::new(contact_info),
            ping_cache: Mutex::new(PingCache::new(
                GOSSIP_PING_CACHE_TTL,
                GOSSIP_PING_CACHE_OUTSTANDING_PING_TIMEOUT_MS,
                GOSSIP_PING_CACHE_CAPACITY,
            )),
            pull_request_budget: KeyedRateLimiter::new(
                GOSSIP_PULL_SCAN_BUDGET_CACHE_CAPACITY,
                TokenBucket::new(
                    GOSSIP_PULL_SCAN_BUDGET_CAPACITY,
                    GOSSIP_PULL_SCAN_BUDGET_CAPACITY,
                    GOSSIP_PULL_SCAN_BUDGET_REFILL_PER_SEC as f64,
                ),
                GOSSIP_PULL_SCAN_BUDGET_SHARD_COUNT,
            ),
```

**File:** gossip/src/cluster_info.rs (L1665-1703)
```rust
    // Returns a predicate checking if the pull request is from a valid
    // address, and if the address have responded to a ping request. Also
    // appends ping packets for the addresses which need to be (re)verified.
    fn check_pull_request<'a, R>(
        &'a self,
        now: Instant,
        rng: &'a mut R,
        packet_batch: &'a mut RecycledPacketBatch,
    ) -> impl FnMut(&PullRequest) -> bool + 'a
    where
        R: Rng + CryptoRng,
    {
        let mut cache = HashMap::<(Pubkey, SocketAddr), bool>::new();
        let mut ping_cache = self.ping_cache.lock().unwrap();
        let mut hard_check = move |node| {
            let (check, ping) = ping_cache.check(rng, &self.keypair(), now, node);
            if let Some(ping) = ping {
                let ping = Protocol::PingMessage(ping);
                if let Some(pkt) = make_gossip_packet(node.1, &ping, &self.stats) {
                    packet_batch.push(pkt);
                }
            }
            if !check {
                self.stats
                    .pull_request_ping_pong_check_failed_count
                    .add_relaxed(1)
            }
            check
        };
        // Because pull-responses are sent back to packet.meta().socket_addr() of
        // incoming pull-requests, pings are also sent to request.from_addr (as
        // opposed to caller.gossip address).
        move |request| {
            ContactInfo::is_valid_address(&request.addr, &self.socket_addr_space) && {
                let node = (request.pubkey, request.addr);
                *cache.entry(node).or_insert_with(|| hard_check(node))
            }
        }
    }
```

**File:** gossip/src/cluster_info.rs (L1705-1721)
```rust
    fn try_consume_pull_request_scan_budget(
        &self,
        request: &PullRequest,
        scan_entries: usize,
    ) -> bool {
        let cost = pull_request_scan_cost(scan_entries, request.filter.bloom_hash_count());
        if self
            .pull_request_budget
            .consume_tokens(request.addr.ip(), cost)
            .is_ok()
        {
            true
        } else {
            self.stats.pull_request_scan_budget_exhausted.add_relaxed(1);
            false
        }
    }
```

**File:** gossip/src/cluster_info.rs (L2724-2780)
```rust
    #[test]
    fn test_pull_request_scan_budget_exhaustion_isolated_per_ip() {
        let keypair = Arc::new(Keypair::new());
        let contact_info = ContactInfo::new_localhost(&keypair.pubkey(), timestamp());
        let mut cluster_info =
            ClusterInfo::new(contact_info, keypair, SocketAddrSpace::Unspecified);
        // Keep the test independent of wall-clock refill.
        cluster_info.pull_request_budget = KeyedRateLimiter::new(
            GOSSIP_PULL_SCAN_BUDGET_CACHE_CAPACITY,
            TokenBucket::new(
                GOSSIP_PULL_SCAN_BUDGET_CAPACITY,
                GOSSIP_PULL_SCAN_BUDGET_CAPACITY,
                f64::MIN_POSITIVE,
            ),
            GOSSIP_PULL_SCAN_BUDGET_SHARD_COUNT,
        );
        let request = PullRequest {
            pubkey: Pubkey::new_unique(),
            addr: SocketAddr::from(([127, 0, 0, 1], 12_345)),
            wallclock: timestamp(),
            filter: CrdsFilter::new_rand(
                crds_gossip_pull::MIN_NUM_BLOOM_ITEMS,
                solana_packet::PACKET_DATA_SIZE,
            ),
        };
        let second_ip_request = PullRequest {
            pubkey: request.pubkey,
            addr: SocketAddr::from(([127, 0, 0, 2], request.addr.port())),
            wallclock: request.wallclock,
            filter: request.filter.clone(),
        };
        request.filter.sanitize().unwrap();
        let crds_len = crds_gossip_pull::MIN_NUM_BLOOM_ITEMS;
        let cost = pull_request_scan_cost(crds_len, request.filter.bloom_hash_count());
        let successful_requests = GOSSIP_PULL_SCAN_BUDGET_CAPACITY / cost;

        assert!(cost <= GOSSIP_PULL_SCAN_BUDGET_CAPACITY);
        assert_eq!(
            cluster_info
                .stats
                .pull_request_scan_budget_exhausted
                .load_relaxed(),
            0,
        );
        for _ in 0..successful_requests {
            assert!(cluster_info.try_consume_pull_request_scan_budget(&request, crds_len));
        }
        assert!(!cluster_info.try_consume_pull_request_scan_budget(&request, crds_len));
        assert!(cluster_info.try_consume_pull_request_scan_budget(&second_ip_request, crds_len));
        assert_eq!(
            cluster_info
                .stats
                .pull_request_scan_budget_exhausted
                .load_relaxed(),
            1,
        );
    }
```
