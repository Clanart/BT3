### Title
`--gossip-validator` restriction is not enforced on inbound gossip traffic, allowing any unlisted peer to inject/retrieve CRDS data - (File: `gossip/src/cluster_info.rs`)

### Summary
The external report describes a `PRIVATE` DAO type whose name implies a membership restriction, but the join-path code never checks any allow/deny list, so the "restriction" is purely cosmetic and anyone can join. The Agave analog is the `--gossip-validator` / `gossip_validators` option: it is documented and used as a mechanism to restrict which peers a node gossips with, but the restriction is only applied to the *outbound* direction (who this node chooses to push to / pull from). It is never checked on the *inbound* path where unsolicited `PullRequest`s and `PushMessage`s are accepted and processed from arbitrary senders.

### Finding Description
`gossip_validators: Option<HashSet<Pubkey>>` is threaded from the CLI flag through `ClusterInfo::gossip` into `run_gossip`, and is only consumed by `get_gossip_nodes` when selecting peers for outbound pull requests and by `refresh_push_active_set` when building the outbound push active set: [1](#0-0) [2](#0-1) 

Both call sites filter with `gossip_validators.is_some_and(|nodes| !nodes.contains(node_pubkey))`, which only decides who *we* select as an outbound peer for pushes/pulls.

On the inbound side, `ClusterInfo::process_packets` classifies incoming `Protocol::PullRequest`, `Protocol::PullResponse`, and `Protocol::PushMessage` packets and dispatches them to `handle_batch_pull_requests`, `handle_batch_pull_responses`, and `handle_batch_push_messages` with no reference to `gossip_validators` at all: [3](#0-2) 

`handle_pull_requests`, which actually answers pull requests from arbitrary senders, filters only on shred version, ping/pong liveness checks, and per-IP rate budget — never on `gossip_validators`: [4](#0-3) 

Likewise, `run_socket_consume`'s `verify_packet` only checks stake/`GossipFilterDirection::Ingress` filtering and signature verification, not membership in `gossip_validators`: [5](#0-4) 

So the "restriction" is asymmetric by design or by omission: it constrains who this validator will proactively contact, but does not constrain who may contact this validator, send it push messages, or request/receive pull responses from it. Any peer — including one never listed in `--gossip-validator` — can still send `PullRequest`/`PushMessage` packets and have them fully processed (subject only to shred-version match, ping-pong, and rate budgets), exactly mirroring the reported bug class: a named "restriction" that exists as a label/config in the code but is not enforced against all interaction paths.

### Impact Explanation
`--gossip-validator` is presented to operators as a peering restriction, plausibly used to reduce a node's gossip attack surface or exposure. Because the restriction is one-directional, an operator relying on it to limit interaction to trusted validators gets no actual protection against unsolicited inbound gossip messages from unlisted/arbitrary nodes — those nodes can still push CRDS data into the node's table and consume its pull-response/pull-request budget. This does not itself cause fund loss, but it undermines the security assumption an operator may build around the flag, allowing any low-cost unlisted peer to continue interacting with (and consuming resources of) a node that was configured to restrict its gossip peer set, unlike the DAO case, this is a config/allowlist enforcement gap in a genuinely unprivileged, remote-reachable path (gossip UDP), not merely a naming/UX issue.

### Likelihood Explanation
High: no attacker capability beyond being a normal, unauthenticated gossip participant (any peer able to route valid gossip packets to the node) is required, and the gap is structural — the filtering exists at exactly the two outbound helper call sites and nowhere in the three inbound handlers.

### Recommendation
If `gossip_validators` is meant to be an enforced restriction rather than purely an outbound peer-selection preference, inbound packet handling in `process_packets`/`handle_pull_requests`/`handle_batch_push_messages` should also check sender pubkey membership in `gossip_validators` before processing/responding, or the flag's documentation should explicitly state it is outbound-only-directional and does not gate inbound gossip.

### Proof of Concept
Not independently exercised (no test harness run); the analysis is based on static code tracing. To validate: start a validator `A` with `--gossip-validator <pubkey_B>` (i.e., B is the only allowed outbound gossip peer), then have an unlisted node `C` send a `Protocol::PullRequest` (or `PushMessage`) directly to `A`'s gossip socket. Per `handle_pull_requests`/`process_packets` above, `A` will still respond to `C`'s pull request and ingest `C`'s pushed CRDS values, since `gossip_validators` is never consulted in `handle_pull_requests`, `handle_batch_pull_responses`, or `handle_batch_push_messages`. [3](#0-2) [6](#0-5)

### Citations

**File:** gossip/src/crds_gossip.rs (L320-347)
```rust
// Returns active and valid cluster nodes to gossip with.
pub(crate) fn get_gossip_nodes<R: Rng>(
    rng: &mut R,
    now: u64,
    pubkey: &Pubkey, // This node.
    // By default, should only push to or pull from gossip nodes with the same
    // shred-version.
    verify_shred_version: impl Fn(/*shred_version:*/ u16) -> bool,
    crds: &RwLock<Crds>,
    gossip_validators: Option<&HashSet<Pubkey>>,
    stakes: &HashMap<Pubkey, u64>,
    socket_addr_space: &SocketAddrSpace,
) -> Vec<GossipStakePubkey> {
    // Exclude nodes which have not been active for this long.
    const ACTIVE_TIMEOUT: Duration = Duration::from_secs(60);
    let active_cutoff = now.saturating_sub(ACTIVE_TIMEOUT.as_millis() as u64);
    let crds = crds.read().unwrap();
    crds.get_nodes()
        .filter_map(|value| {
            let node = value.value.contact_info()?;
            let gossip = node.gossip().filter(|addr| socket_addr_space.check(addr))?;
            let node_pubkey = node.pubkey();
            if node_pubkey == pubkey
                || !verify_shred_version(node.shred_version())
                || gossip_validators.is_some_and(|nodes| !nodes.contains(node_pubkey))
            {
                return None;
            }
```

**File:** gossip/src/crds_gossip_push.rs (L236-261)
```rust
    /// Refresh the push active set.
    #[allow(clippy::too_many_arguments)]
    pub(crate) fn refresh_push_active_set(
        &self,
        crds: &RwLock<Crds>,
        stakes: &HashMap<Pubkey, u64>,
        gossip_validators: Option<&HashSet<Pubkey>>,
        self_keypair: &Keypair,
        self_shred_version: u16,
        ping_cache: &Mutex<PingCache>,
        pings: &mut Vec<(SocketAddr, Ping)>,
        socket_addr_space: &SocketAddrSpace,
    ) {
        let mut rng = rand::rng();
        // Active and valid gossip nodes with matching shred-version.
        let nodes = crds_gossip::get_gossip_nodes(
            &mut rng,
            timestamp(), // now
            &self_keypair.pubkey(),
            // Only push to nodes with the same shred version.
            |shred_version| shred_version == self_shred_version,
            crds,
            gossip_validators,
            stakes,
            socket_addr_space,
        );
```

**File:** gossip/src/cluster_info.rs (L1665-1825)
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

    // Pull requests take an incoming bloom filter of contained entries from a node
    // and tries to send back to them the values it detects are missing.
    fn handle_pull_requests(
        &self,
        recycler: &PacketBatchRecycler,
        mut requests: Vec<PullRequest>,
        stakes: &HashMap<Pubkey, u64>,
    ) -> RecycledPacketBatch {
        const DEFAULT_EPOCH_DURATION_MS: u64 = DEFAULT_SLOTS_PER_EPOCH * DEFAULT_MS_PER_SLOT;
        let output_size_limit =
            self.update_data_budget(stakes.len()) / PULL_RESPONSE_MIN_SERIALIZED_SIZE;
        let mut packet_batch =
            RecycledPacketBatch::new_with_recycler(recycler, 64, "handle_pull_requests");
        let mut rng = rand::rng();
        requests.retain({
            let now = Instant::now();
            self.check_pull_request(now, &mut rng, &mut packet_batch)
        });
        let now = timestamp();
        let self_id = self.id();
        let is_full_alpenglow_epoch = self.is_full_alpenglow_epoch();
        let pull_responses = {
            let _st = ScopedTimer::from(&self.stats.generate_pull_responses);
            self.gossip.generate_pull_responses(
                &requests,
                output_size_limit,
                now,
                |value| {
                    should_retain_crds_value(
                        value,
                        stakes,
                        GossipFilterDirection::EgressPullResponse,
                        is_full_alpenglow_epoch,
                    )
                },
                |request, scan_entries| {
                    self.try_consume_pull_request_scan_budget(request, scan_entries)
                },
                &self.stats,
            )
        };
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
    }
```

**File:** gossip/src/cluster_info.rs (L2085-2147)
```rust
        for (from_addr, packet) in packets.drain(..).flatten() {
            match packet {
                Protocol::PullRequest(filter, caller) => {
                    if !check_pull_request_shred_version(self_shred_version, &caller) {
                        self.stats.skip_pull_shred_version.add_relaxed(1);
                        continue;
                    }
                    let request = PullRequest {
                        pubkey: caller.pubkey(),
                        addr: from_addr,
                        wallclock: caller.wallclock(),
                        filter,
                    };
                    if request.pubkey == self_pubkey {
                        self.stats.window_request_loopback.add_relaxed(1);
                    } else {
                        pull_requests.push(request);
                    }
                }
                Protocol::PullResponse(_, mut data) => {
                    if should_check_duplicate_instance {
                        check_duplicate_instance(&data)?;
                    }
                    data.retain(&mut verify_gossip_addr);
                    if !data.is_empty() {
                        pull_responses.append(&mut data);
                    }
                }
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
                Protocol::PruneMessage(_from, data) => prune_messages.push(data),
                Protocol::PingMessage(ping) => ping_messages.push((from_addr, ping)),
                Protocol::PongMessage(pong) => pong_messages.push((from_addr, pong)),
            }
        }
        let pings = pings
            .into_iter()
            .map(|(addr, ping)| (addr, Protocol::PingMessage(ping)));
        send_gossip_packets(pings, recycler, response_sender, &self.stats);
        self.handle_batch_ping_messages(ping_messages, recycler, response_sender);
        self.handle_batch_prune_messages(prune_messages, stakes);
        self.handle_batch_push_messages(
            push_messages,
            thread_pool,
            recycler,
            stakes,
            response_sender,
        );
        self.handle_batch_pull_responses(pull_responses, stakes, epoch_duration);
        self.trim_crds_table(CRDS_UNIQUE_PUBKEY_CAPACITY, stakes);
        self.handle_batch_pong_messages(pong_messages, Instant::now());
        self.handle_batch_pull_requests(pull_requests, recycler, stakes, response_sender);
        Ok(())
```

**File:** gossip/src/cluster_info.rs (L2176-2208)
```rust
        fn verify_packet(
            packet: PacketRef,
            stakes: &HashMap<Pubkey, u64>,
            stats: &GossipStats,
            sigverify_cache: &SigVerifyCache,
            is_full_alpenglow_epoch: bool,
        ) -> Option<(SocketAddr, Protocol)> {
            let result: wincode::ReadResult<Protocol> = packet
                .data(..)
                .ok_or(wincode::ReadError::Custom("packet discarded"))
                .and_then(deserialize_protocol);
            let mut protocol: Protocol = stats.record_received_packet(result)?;
            protocol.sanitize().ok()?;
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
