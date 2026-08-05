## Analysis

The reported bug class is: **a single unprivileged actor can exhaust a shared, time-windowed rate-limit resource in one shot, denying legitimate users access to that resource until the window resets.** The relevant broken invariant is "the rate-limit budget for window `W` should be fairly shared across many legitimate participants," not "first attacker to spend it wins it all."

The closest analog in this Agave snapshot is in the gossip pull-response path, where a single shared, un-keyed byte budget (`outbound_budget`) is drained across *all* peers' pull requests received in one gossip round, and the only fairness mechanism (a `WeightedShuffle`) can be probabilistically dominated by high request volume from a single actor.

### Title
Shared gossip outbound-bandwidth budget for pull-responses can be volumetrically exhausted by a single peer, starving legitimate pull-response traffic - (File: gossip/src/cluster_info.rs)

### Summary
`ClusterInfo` maintains one global, non-keyed `DataBudget` (`outbound_budget`) that limits the total bytes of CRDS pull-response data sent per ~100ms gossip round [1](#0-0) . Every pull-response packet generated in `handle_pull_requests`, regardless of requester, is drawn from this single budget via `self.outbound_budget.take(...)` inside a `take_while` [2](#0-1) . The only defense against a single requester unfairly consuming it is a randomized `WeightedShuffle` ordering by stake/recency/type [3](#0-2) , not a hard per-peer/per-IP cap on the *number* of pull requests contributing to the budget draw in a round. This mirrors the PSM bug: one global counter reset on a fixed timer, drained in full by whichever caller races into it first (here, probabilistically, by volume), blocking everyone else until the next refill.

### Finding Description
`outbound_budget` is refreshed once per gossip round by `update_data_budget`, which adds a fixed number of bytes proportional to `num_staked` peers, capped at `5x` the per-interval amount [4](#0-3) . This is a *global* resource — unlike the pull-request *scan* budget (`pull_request_budget`), which is explicitly a `KeyedRateLimiter` isolated per source IP (confirmed by the dedicated test `test_pull_request_scan_budget_exhaustion_isolated_per_ip`) [5](#0-4) [6](#0-5) .

In `handle_pull_requests`, all valid (ping-verified) pull requests received in the current round are turned into response batches, scored, weighted-shuffled, and then packets are emitted **until the shared `outbound_budget` runs out**; any packet after that point is simply dropped and counted in `gossip_pull_request_no_budget` [7](#0-6) . The scan-cost budget only throttles how expensive it is for an attacker to get *scanned* (bloom-filter cost), not how many separate low-cost requests an attacker can submit from different source addresses in the same round to bias the weighted shuffle. Because `WeightedShuffle` selects items with probability proportional to score/(remaining total score) rather than enforcing a strict priority cutoff, an attacker submitting a very large number of valid (ping-passed) low/no-stake pull requests from many distinct IPs can accumulate enough aggregate weight-participation to consume most or all of the shared byte budget before legitimate, higher-stake peers' requests are served in that round.

### Impact Explanation
If successful, this starves gossip pull-response bandwidth for one or more ~100ms windows, delaying propagation of CRDS values (contact info, vote/epoch-slots gossip metadata, duplicate-shred proofs, etc.) to other validators. Sustained abuse (repeating the attack every round, since the attacker only needs to keep pinging and issuing pull requests) degrades CRDS convergence cluster-wide — a non-RPC, remote exhaustion condition on the gossip subsystem. It does not itself cause fund loss or consensus halt, but persistent gossip-layer starvation can delay propagation of information relied upon by other subsystems (e.g., duplicate-shred detection, contact-info freshness for repair/turbine), which is within the "non-RPC remote exhaustion/crash or degradation" impact category.

### Likelihood Explanation
Sending gossip `PullRequest`s does not require being a staked validator — any reachable UDP peer that passes the ping/pong liveness check can issue them (`check_pull_request`) [8](#0-7) , satisfying the "unprivileged" requirement. However, likelihood is only **moderate**, not high, because:
- The `WeightedShuffle` mechanism still statistically favors staked/high-recency requests, so the attacker must generate large enough aggregate volume/weight to meaningfully bias outcomes — this is a probabilistic, not deterministic, exhaustion (unlike the PSM bug's deterministic first-come exhaustion).
- I could not fully verify, within the available index, whether there is an additional per-round cap on the total number of `PullRequest`s a single node processes (e.g., via packet ingestion/coalesce limits upstream of `handle_batch_pull_requests`), which would further reduce attacker leverage. This is a gap in my analysis that a full repository session could resolve.

### Recommendation
Apply a per-peer (or per-IP) cap/keying to `outbound_budget` consumption analogous to the existing `pull_request_budget` `KeyedRateLimiter`, so that no single requester (regardless of request volume) can consume more than a fair share of the shared response-bandwidth budget per round, rather than relying solely on probabilistic weighted-shuffle fairness.

### Proof of Concept
Conceptual (not executed, no fund-impact PoC possible in read-only review):
1. Attacker stands up N sockets/source addresses that each pass the gossip ping/pong liveness check (`check_pull_request`).
2. Each socket sends a valid `PullRequest` with a bloom filter costed low enough to stay under the per-IP `pull_request_scan_budget` (`GOSSIP_PULL_SCAN_BUDGET_CAPACITY`) [9](#0-8) .
3. In the same `handle_batch_pull_requests`/`handle_pull_requests` invocation, these N attacker-controlled, low/no-stake requests are shuffled together with legitimate validator requests; with enough aggregate volume, the `WeightedShuffle` draw statistically consumes a majority of `outbound_budget` bytes via `self.outbound_budget.take(...)` before legitimate peers' pull-responses are packetized [10](#0-9) .
4. Legitimate peers' pull-responses are dropped for that round (`gossip_pull_request_no_budget` incremented), and the attacker repeats each subsequent round to sustain the starvation.

**Note on uncertainty:** I was not able to fully confirm within the indexed code whether an upstream ingestion-rate limiter caps the total number of `PullRequest`s processed per round per source, which would materially affect exploitability. A background Devin session with full repository/build access could verify this and attempt to reproduce the effect empirically (e.g., via a local gossip test harness) before treating this as a confirmed, high-confidence finding.

### Citations

**File:** gossip/src/cluster_info.rs (L211-213)
```rust
            known_validators: OnceLock::new(),
            outbound_budget: DataBudget::default(),
            my_contact_info: RwLock::new(contact_info),
```

**File:** gossip/src/cluster_info.rs (L1650-1663)
```rust
    fn update_data_budget(&self, num_staked: usize) -> usize {
        const INTERVAL_MS: u64 = 100;
        // epoch slots + votes ~= 1.5kB/slot ~= 4kB/s
        // Allow 10kB/s per staked validator.
        const BYTES_PER_INTERVAL: usize = 1024;
        const MAX_BUDGET_MULTIPLE: usize = 5; // allow budget build-up to 5x the interval default
        let num_staked = num_staked.max(2);
        self.outbound_budget.update(INTERVAL_MS, |bytes| {
            std::cmp::min(
                bytes + num_staked * BYTES_PER_INTERVAL,
                MAX_BUDGET_MULTIPLE * num_staked * BYTES_PER_INTERVAL,
            )
        })
    }
```

**File:** gossip/src/cluster_info.rs (L1668-1703)
```rust
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

**File:** gossip/src/cluster_info.rs (L1764-1793)
```rust
        // Prioritize more recent values, staked values and ContactInfos.
        let get_score = |value: &CrdsValue| -> u64 {
            let age = now.saturating_sub(value.wallclock());
            // score CrdsValue: 2x score if staked; 2x score if ContactInfo
            let score = DEFAULT_EPOCH_DURATION_MS
                .saturating_sub(age)
                .div(CRDS_GOSSIP_PULL_CRDS_TIMEOUT_MS)
                .max(1);
            let score = if stakes.contains_key(&value.pubkey()) {
                2 * score
            } else {
                score
            };
            match value.data() {
                CrdsData::ContactInfo(_) => 2 * score,
                _ => score,
            }
        };
        let mut num_crds_values = 0;
        let (scores, mut pull_responses): (Vec<_>, Vec<_>) = requests
            .iter()
            .zip(pull_responses)
            .flat_map(|(PullRequest { addr, .. }, values)| {
                num_crds_values += values.len();
                split_gossip_messages(PULL_RESPONSE_MAX_PAYLOAD_SIZE, values).map(move |values| {
                    let score = values.iter().map(get_score).max().unwrap_or_default();
                    (score, (addr, values))
                })
            })
            .collect();
```

**File:** gossip/src/cluster_info.rs (L1794-1824)
```rust
        let (total_bytes, sent_crds_values) = WeightedShuffle::new("handle-pull-requests", scores)
            .shuffle(&mut rng)
            .filter_map(|k| {
                let (addr, values) = &mut pull_responses[k];
                let num_values = values.len();
                let response = Protocol::PullResponse(self_id, std::mem::take(values));
                let packet = make_gossip_packet(*addr, &response, &self.stats)?;
                Some((packet, num_values))
            })
            .take_while(|(packet, _)| {
                if self.outbound_budget.take(packet.meta().size) {
                    true
                } else {
                    self.stats.gossip_pull_request_no_budget.add_relaxed(1);
                    false
                }
            })
            .map(|(packet, num_values)| {
                let num_bytes = packet.meta().size;
                packet_batch.push(packet);
                (num_bytes, num_values)
            })
            .fold((0, 0), |a, b| (a.0 + b.0, a.1 + b.1));
        let dropped_responses = num_crds_values.saturating_sub(sent_crds_values);
        self.stats
            .gossip_pull_request_dropped_requests
            .add_relaxed(dropped_responses as u64);
        self.stats
            .gossip_pull_request_sent_bytes
            .add_relaxed(total_bytes as u64);
        packet_batch
```

**File:** gossip/src/cluster_info.rs (L2718-2722)
```rust
    #[test]
    fn test_pull_request_scan_budget_production_config() {
        assert_eq!(GOSSIP_PULL_SCAN_BUDGET_CAPACITY, 1_048_576);
        assert_eq!(GOSSIP_PULL_SCAN_BUDGET_REFILL_PER_SEC, 262_144);
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
