### Title
Global (Non-Per-Peer) State-Part Throttle in `StateRequestActor` Allows Any Peer to Exhaust the Serving Budget, Blocking Validator State Sync — (`chain/client/src/state_request_actor.rs`)

---

### Summary

`StateRequestActor.throttle_state_sync_request()` maintains a single sliding-window counter (`state_request_timestamps`) that is **shared across all incoming peers** on a given thread. Any single peer can exhaust the entire per-thread throttle budget within one `throttle_period`, causing all subsequent `StateRequestHeader` and `StateRequestPart` responses to be silently dropped for every other peer for the remainder of the window. Validators that must complete state sync before an epoch boundary to earn block-production rewards can be completely blocked at negligible cost to the attacker.

---

### Finding Description

`StateRequestActor` is spawned as a multithread actor pool: [1](#0-0) 

`spawn_multithread_actor` calls `make_actor_fn()` once per OS thread, so each thread receives its own independent `StateRequestActor` instance: [2](#0-1) 

Inside `StateRequestActor::new()`, the throttle counter is created fresh per instance: [3](#0-2) 

The throttle logic in `throttle_state_sync_request()` counts **all** requests regardless of which peer sent them: [4](#0-3) 

When `timestamps.len() >= num_state_requests_per_throttle_period`, the function returns `true` and the handler returns `None` — silently dropping the request with no response sent to the requester: [5](#0-4) [6](#0-5) 

The network-layer per-peer rate limiter (`RateLimits`) does **not** apply default limits to `StateRequestHeader` or `StateRequestPart` in the standard preset — only `EpochSyncRequest`/`EpochSyncResponse` are rate-limited there: [7](#0-6) 

`StateRequestHeader` and `StateRequestPart` appear in the `RateLimitedPeerMessageKey` enum but carry no default bucket: [8](#0-7) 

This means an attacker peer can send `num_state_requests_per_throttle_period` requests in rapid succession with no per-peer gate, filling the per-thread window. All subsequent requests from any other peer on that thread are dropped until the window slides forward.

The configuration fields that control the budget are: [9](#0-8) 

The test confirms the throttle fires after the budget is exhausted and that the window is shared (not per-peer): [10](#0-9) 

---

### Impact Explanation

State sync is on the critical path for validators. A validator assigned to a new shard must complete state sync within one epoch or it cannot produce chunks and forfeits block-production rewards: [11](#0-10) 

If an attacker exhausts the throttle budget on every snapshot-host node that serves the required shard, the syncing validator receives only `None` responses (silently dropped), cannot assemble the state parts, and misses the epoch boundary. The exact broken invariant is: `state_request_timestamps` is keyed on time only, not on `(peer_id, time)`, so one peer's requests consume the budget that was intended to be shared fairly across all peers.

---

### Likelihood Explanation

- **Unprivileged**: Any connected peer can send `StateRequestHeader` / `StateRequestPart` messages; no validator key or special role is required.
- **No per-peer gate at the network layer**: The standard rate-limit preset leaves these message types unlimited per peer.
- **Cheap**: Sending `N` small network messages (where `N = num_state_requests_per_throttle_period × state_request_server_threads`) exhausts all threads. Based on the test, `N` is on the order of 30 per thread. With the default thread count this is a few hundred messages — trivially cheap.
- **Repeatable**: The attacker simply re-floods every `throttle_period` to maintain the blockage indefinitely.

---

### Recommendation

Replace the single global `state_request_timestamps: Arc<Mutex<VecDeque<Instant>>>` with a per-peer map, e.g.:

```rust
state_request_timestamps: Arc<Mutex<HashMap<PeerId, VecDeque<Instant>>>>
```

Alternatively, add a per-peer token-bucket entry for `StateRequestHeader` and `StateRequestPart` in the network-layer `standard_preset()` (analogous to the existing `EpochSyncRequest` bucket), so the per-peer gate fires before the global actor-level throttle is reached. [7](#0-6) 

---

### Proof of Concept

1. Connect to a snapshot-host node as an ordinary peer (no special credentials needed).
2. Send `num_state_requests_per_throttle_period` `StateRequestHeader` messages for any valid `sync_hash` in rapid succession. Each message passes the network layer (no per-peer limit in the standard preset) and is counted by `throttle_state_sync_request()`.
3. The per-thread `state_request_timestamps` deque is now full. `throttle_state_sync_request()` returns `true` for every subsequent call on that thread.
4. A legitimate validator node that sends `StateRequestPart` to the same snapshot host receives `None` (no response) for every part it requests on that thread.
5. Repeat every `throttle_period` seconds across all `state_request_server_threads` threads to maintain complete blockage.
6. The validator cannot assemble the full state, misses the epoch boundary, and loses block-production rewards for the new shard. [4](#0-3) [12](#0-11)

### Citations

**File:** nearcore/src/lib.rs (L527-543)
```rust
    let state_request_addr = {
        let runtime = runtime.clone();
        let epoch_manager = epoch_manager.clone();
        actor_system.spawn_multithread_actor(
            config.client_config.state_request_server_threads,
            move || {
                StateRequestActor::new(
                    Clock::real(),
                    runtime.clone(),
                    epoch_manager.clone(),
                    genesis_id.hash,
                    config.client_config.state_request_throttle_period,
                    config.client_config.state_requests_per_throttle_period,
                )
            },
        )
    };
```

**File:** chain/client/src/state_request_actor.rs (L66-69)
```rust
            state_request_timestamps: Arc::new(Mutex::new(VecDeque::with_capacity(
                num_state_requests_per_throttle_period,
            ))),
        }
```

**File:** chain/client/src/state_request_actor.rs (L74-91)
```rust
    fn throttle_state_sync_request(&self) -> bool {
        let mut timestamps = self.state_request_timestamps.lock();
        let now = self.clock.now();
        while let Some(&instant) = timestamps.front() {
            // Assume that time is linear. While in different threads there might be some small differences,
            // it should not matter in practice.
            if now - instant > self.throttle_period {
                timestamps.pop_front();
            } else {
                break;
            }
        }
        if timestamps.len() >= self.num_state_requests_per_throttle_period {
            return true;
        }
        timestamps.push_back(now);
        false
    }
```

**File:** chain/client/src/state_request_actor.rs (L225-229)
```rust
        if self.throttle_state_sync_request() {
            tracing::debug!(target: "sync", "throttling state sync request for shard");
            metrics::STATE_SYNC_REQUESTS_THROTTLED_TOTAL.inc();
            return None;
        }
```

**File:** chain/client/src/state_request_actor.rs (L260-274)
```rust
impl Handler<StateRequestPart, Option<StatePartOrHeader>> for StateRequestActor {
    fn handle(&mut self, msg: StateRequestPart) -> Option<StatePartOrHeader> {
        let StateRequestPart { shard_id, sync_hash, part_id } = msg;
        let _timer =
            metrics::STATE_SYNC_REQUEST_TIME.with_label_values(&["StateRequestPart"]).start_timer();
        let _span =
            tracing::debug_span!(target: "sync", "StateRequestPart", ?shard_id, ?sync_hash, part_id)
                .entered();

        tracing::debug!(target: "sync", "handle state request part");

        if self.throttle_state_sync_request() {
            metrics::STATE_SYNC_REQUESTS_THROTTLED_TOTAL.inc();
            return None;
        }
```

**File:** chain/network/src/rate_limits/messages_limits.rs (L104-122)
```rust
    /// Returns a good preset of rate limit configuration valid for any type of node.
    pub fn standard_preset() -> Self {
        // TODO(trisfald): make presets for other message types
        let mut config = Self::default();
        // EpochSyncRequest is a very simple amplification attack vector, as it requires no arguments
        // and the response is large. So we rate limit it to 1 request per 30 seconds. In practice,
        // a peer should not need to epoch sync except when bootstrapping a node, so a request
        // should be rarely received. We still set it to a reasonable rate limit so a bootstrapping
        // node can retry without waiting for too long.
        config.rate_limits.insert(
            RateLimitedPeerMessageKey::EpochSyncRequest,
            SingleMessageConfig::new(1, 1.0 / 30.0, None),
        );
        config.rate_limits.insert(
            RateLimitedPeerMessageKey::EpochSyncResponse,
            SingleMessageConfig::new(1, 1.0 / 30.0, None),
        );
        config
    }
```

**File:** chain/network/src/rate_limits/messages_limits.rs (L163-165)
```rust
    StateRequestHeader,
    StateRequestPart,
    VersionedStateResponse,
```

**File:** nearcore/src/config.rs (L333-341)
```rust
    /// Throttling window for state requests (headers and parts).
    #[serde(with = "near_async::time::serde_duration_as_std")]
    #[serde(alias = "view_client_throttle_period")]
    pub state_request_throttle_period: Duration,
    /// Maximum number of state requests served per throttle period
    #[serde(alias = "view_client_num_state_requests_per_throttle_period")]
    pub state_requests_per_throttle_period: usize,
    /// Number of threads for StateRequestActor pool.
    pub state_request_server_threads: usize,
```

**File:** test-loop-tests/src/tests/sync/state_sync.rs (L731-749)
```rust
    for _ in 0..30 {
        let res = state_request.handle(StateRequestHeader { shard_id: ShardId::new(0), sync_hash });
        assert!(res.is_some());
    }

    // Immediately query again, should be rejected due to rate limit.
    let shard_id = ShardId::new(0);
    let res = state_request.handle(StateRequestHeader { shard_id, sync_hash });
    assert!(res.is_none());

    env.test_loop.run_for(Duration::seconds(40));

    let sync_hash = await_sync_hash(env);
    let state_request_handle = env.node_datas[0].state_request_sender.actor_handle();
    let state_request = env.test_loop.data.get_mut(&state_request_handle);

    // After the rate limit window resets, requests should succeed again.
    let res = state_request.handle(StateRequestHeader { shard_id, sync_hash });
    assert!(res.is_some());
```

**File:** docs/architecture/next/catchup_and_state_sync.md (L16-18)
```markdown

* catchups are high priority (the validator MUST catchup within 1 epoch - otherwise it will not be able to produce blocks for the new shards in the next epoch - and therefore it will not earn rewards).
* a lot more catchups in progress (with lots of shards basically every validator would have to catchup at least one shard at each epoch boundary) - this leads to a lot more potential traffic on the network
```
