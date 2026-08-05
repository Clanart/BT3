Based on the evidence gathered, this is a global, single, stake-blind `TokenBucket` shared across all repair requesters, where per-request token consumption is not weighted by stake — the only stake-awareness in the path is a best-effort priority sort that only activates once decoded requests exceed 1024 per one-second listen cycle.

### Title
Unstaked/zero-stake peers can drain the shared repair-serving bandwidth budget on par with staked peers - ([File: core/src/repair/serve_repair.rs])

### Summary
`ServeRepair::run_listen` maintains a single shared `data_budget: &TokenBucket` used to rate-limit *all* outbound repair response bytes for the validator, regardless of requester. `handle_requests` consumes tokens from this shared bucket per request using `max_response_cost = request.max_response_bytes() * byte_cost_multiplier`, with no dependency on the requester's stake.

### Finding Description
Requests are decoded and stake is looked up in `decode_request`, but stake is only used for statistics and as a *tie-breaker* when the decoded request count for a single ~1s listen iteration exceeds `MAX_REQUESTS_PER_ITERATION` (1024): [1](#0-0) 
Below that truncation threshold, no stake-based gating occurs at all before `handle_requests` is called, and the actual bandwidth accounting is stake-blind: [2](#0-1) 
The `data_budget` is a single global `TokenBucket` (not per-peer, not per-stake, not per-IP) shared across every repair requester the node serves: [3](#0-2) 
The consumption path in `handle_requests` treats a stake-0 requester's `consume_tokens` call identically to a stake-heavy validator's — `stats` differentiate staked vs. unstaked bytes served only for *metrics*, not for gating: [4](#0-3) 
This mirrors the FrankenDAO `castVote` bug class exactly: an unprivileged actor (a repair requester with zero/near-zero stake) is processed by a shared, finite resource pool (`data_budget`) without any check that the actor holds the resource (stake) the QoS design assumes it needs, because the only weight-based gate is a rare-case truncation, not the actual consumption path.

### Impact Explanation
An attacker controlling many identities (each cheaply generated, since repair requests are gated only by sig-verify, ping-cache checks, and a valid keypair — none of which require stake) can continuously send repair requests below the 1024-per-iteration truncation threshold. Because token consumption in `handle_requests` is stake-blind, this attacker traffic consumes the same shared `data_budget` that legitimate stake-weighted validators depend on for timely repair service. Once the bucket is exhausted, `dropped_requests_outbound_bandwidth` increments and legitimate staked repair requests get dropped (`data_budget.consume_tokens(...).is_err()`), degrading the validator's ability to serve repair traffic to real cluster peers. This is a non-RPC remote resource-exhaustion vector on a validator's repair-serving capacity, not requiring the attacker to be a validator, have stake, or be trusted.

### Likelihood Explanation
Likelihood is moderate-to-high: repair requests are UDP/gossip-adjacent and permissionless by design (unstaked nodes are intentionally allowed to request repairs so they can catch up before acquiring stake). The only friction is per-packet sig-verify and a ping-cache liveness check, both of which are cheap for an attacker to satisfy with throwaway keypairs from many source addresses, keeping request volume under the 1024/iteration truncation trigger while still exhausting the shared token bucket.

### Recommendation
Weight token consumption in `handle_requests` (and/or the effective budget estimate in `decode_requests`) by requester stake — e.g., require zero/low-stake requesters to draw from a separate, smaller sub-budget (similar to the QUIC `SwQos`/`stream_throttle` design's `ConnectionPeerType::Unstaked` carve-out) rather than sharing the same `data_budget` pool 1:1 with staked requesters, so that a swarm of zero-stake identities cannot starve legitimate stake-weighted repair traffic.

### Proof of Concept
Not independently executable from static analysis alone; the code path itself demonstrates the issue: `decode_request` computes `stake` per requester but does not gate on it [5](#0-4) , and `handle_requests` consumes from the single shared `data_budget` with `stake` used only to bucket statistics after the fact [6](#0-5) . A concrete runtime PoC (spinning up many zero-stake keypairs sending sub-1024/iteration repair requests against a live validator) would need to be built and run in a Devin session with cluster/test-validator access, which is outside the scope of this read-only analysis.

**Note on confidence**: I could not fully trace whether `byte_cost_multiplier` or upstream QUIC/UDP-level rate limiting (e.g., `max_connections_per_ipaddr_per_min`) elsewhere in the repair-socket receive path independently caps per-IP request volume enough to make this impractical in practice — that would require reviewing the UDP socket receive/dispatch code feeding `requests_receiver`, which I did not have iterations left to inspect. If such an independent per-IP/per-connection cap exists and is stricter than assumed, the practical severity of this issue would be lower than described.

### Citations

**File:** core/src/repair/serve_repair.rs (L1072-1088)
```rust
        let stake = *epoch_staked_nodes
            .as_ref()
            .and_then(|stakes| stakes.get(request.sender()?))
            .unwrap_or(&0);

        let whitelisted = request
            .sender()
            .map(|pubkey| whitelist.contains(pubkey))
            .unwrap_or_default();

        Ok(RepairRequestWithMeta {
            request,
            from_addr,
            stake,
            whitelisted,
        })
    }
```

**File:** core/src/repair/serve_repair.rs (L1246-1254)
```rust
        let whitelisted_request_count = decoded_requests.iter().filter(|r| r.whitelisted).count();
        stats.decode_time_us += decode_start.elapsed().as_micros() as u64;
        stats.whitelisted_requests += whitelisted_request_count.min(MAX_REQUESTS_PER_ITERATION);

        if decoded_requests.len() > MAX_REQUESTS_PER_ITERATION {
            stats.dropped_requests_low_stake += decoded_requests.len() - MAX_REQUESTS_PER_ITERATION;
            decoded_requests.sort_unstable_by_key(|r| Reverse((r.whitelisted, r.stake)));
            decoded_requests.truncate(MAX_REQUESTS_PER_ITERATION);
        }
```

**File:** core/src/repair/serve_repair.rs (L1564-1573)
```rust
            // we deliberately consume early assuming that request succeeds,
            // if it does we will refund the unused tokens
            let max_response_cost = request.max_response_bytes() * byte_cost_multiplier;
            if data_budget
                .consume_tokens(max_response_cost as u64)
                .is_err()
            {
                stats.dropped_requests_outbound_bandwidth += 1;
                continue;
            }
```

**File:** core/src/repair/serve_repair.rs (L1604-1613)
```rust
            if packet_batch_sender.try_send(rsp).is_ok() {
                stats.total_response_packets += num_response_packets;
                match stake > 0 {
                    true => stats.total_response_bytes_staked += num_response_bytes,
                    false => stats.total_response_bytes_unstaked += num_response_bytes,
                }
            } else {
                stats.dropped_requests_outbound_bandwidth += 1;
                stats.total_dropped_response_packets += num_response_packets;
            }
```

**File:** net-utils/src/token_bucket.rs (L72-94)
```rust
    /// Attempts to consume tokens from bucket.
    ///
    /// On success, returns Ok(amount of tokens left in the bucket).
    /// On failure, returns Err(amount of tokens missing to fill request).
    #[inline]
    pub fn consume_tokens(&self, request_size: u64) -> Result<u64, u64> {
        let now = self.time_us();
        self.update_state(now);
        match self.tokens.fetch_update(
            Ordering::AcqRel,  // winner publishes new amount
            Ordering::Acquire, // everyone observed correct number
            |tokens| {
                if tokens >= request_size {
                    Some(tokens.saturating_sub(request_size))
                } else {
                    None
                }
            },
        ) {
            Ok(prev) => Ok(prev.saturating_sub(request_size)),
            Err(prev) => Err(request_size.saturating_sub(prev)),
        }
    }
```
