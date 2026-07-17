### Title
Global State-Sync Rate Limiter Exhausted by Single Peer Blocks All Legitimate State-Part Responses — (`chain/client/src/state_request_actor.rs`)

---

### Summary

`StateRequestActor::throttle_state_sync_request` maintains a **single, global** sliding-window counter (`state_request_timestamps`) that is shared across every peer that sends `StateRequestHeader` or `StateRequestPart` messages. Because there is no per-peer accounting, a single malicious peer can fill the window in one burst, causing every subsequent request from every legitimate peer to be silently dropped for the entire `state_request_throttle_period` (default 30 s). Validators performing shard catchup and nodes recovering from downtime depend on these responses to complete state sync before the epoch boundary; exhausting the limiter blocks them entirely.

---

### Finding Description

`StateRequestActor` is the sole server-side handler for state-sync header and part requests. [1](#0-0) 

Its rate-limiter is a `VecDeque<Instant>` wrapped in `Arc<Mutex<…>>`, created fresh inside each call to `StateRequestActor::new`: [2](#0-1) 

`throttle_state_sync_request` evicts timestamps older than `throttle_period` and then checks whether the remaining count has reached `num_state_requests_per_throttle_period`: [3](#0-2) 

The throttle check runs **before** `validate_sync_hash`, so even requests carrying a completely invalid or random `sync_hash` consume a slot: [4](#0-3) [5](#0-4) 

At the network layer, `StateRequestHeader` and `StateRequestPart` appear in `RateLimitedPeerMessageKey` but are **not** included in `standard_preset()`, so there is no per-peer token-bucket guard before messages reach the actor: [6](#0-5) [7](#0-6) 

The actor is spawned with `state_request_server_threads` OS threads, each calling `make_actor_fn()` independently and therefore each getting its **own** `state_request_timestamps` deque: [8](#0-7) [9](#0-8) 

Because the crossbeam channel distributes incoming messages across threads in round-robin fashion, an attacker sending `T × num_state_requests_per_throttle_period` requests (where `T` = thread count) exhausts every thread's window simultaneously. With the default of 30 requests per 30-second window, a single peer sending 30 × T rapid requests saturates all threads and causes every subsequent legitimate request to return `None` for the next 30 seconds.

---

### Impact Explanation

State sync is on the critical path for:

1. **Validator catchup** — a chunk producer assigned to a new shard in the next epoch must complete state sync within the current epoch or it cannot produce chunks and loses rewards.
2. **Node recovery** — any node that fell behind by more than two epochs must complete state sync to rejoin the network.

A 30-second blackout per attack cycle (repeatable indefinitely) is sufficient to prevent either from completing within the epoch window. Unlike ordinary resource-exhaustion DoS, this attack does not degrade the attacker's own connectivity; the attacker's requests are simply dropped after the limit is hit, costing only the bandwidth of the initial burst.

**Severity: High** — unprivileged network peer, no authentication required, directly prevents validators from fulfilling their protocol obligations.

---

### Likelihood Explanation

- Any peer reachable by the node can send `StateRequestHeader` / `StateRequestPart` without authentication.
- The attack requires sending at most `T × 30` UDP/TCP messages per 30-second window — trivially cheap.
- The attacker does not need a valid `sync_hash`; any bytes pass the throttle gate before hash validation.
- The attack is repeatable every `throttle_period` with no cost increase.

---

### Recommendation

1. **Per-peer rate limiting at the network layer**: add `StateRequestHeader` and `StateRequestPart` to `standard_preset()` with a per-connection token bucket (e.g., 5 requests / 30 s per peer). This mirrors how `EpochSyncRequest` is already protected. [10](#0-9) 

2. **Per-peer accounting inside `StateRequestActor`**: replace the single global `VecDeque` with a `HashMap<PeerId, VecDeque<Instant>>` so that one peer exhausting its quota does not affect others. The peer identity is already available in the routed message envelope.

3. **Validate `sync_hash` before recording the timestamp**: move `validate_sync_hash` before `throttle_state_sync_request` so that requests with invalid hashes are rejected without consuming a rate-limit slot. [11](#0-10) 

---

### Proof of Concept

```
Default config:
  state_request_throttle_period  = 30 s
  state_requests_per_throttle_period = 30
  state_request_server_threads   = N  (e.g. 4)

Attack:
  t=0:  attacker sends 4×30 = 120 StateRequestHeader messages
        (any sync_hash, any shard_id)
        → all 120 consume slots across the 4 thread windows
        → windows are now full

  t=1s: legitimate syncing validator sends StateRequestHeader
        → throttle_state_sync_request() returns true → None returned
        → validator receives no response, retries

  t=0..30s: every legitimate request is dropped

  t=30s: attacker sends another 120 messages → windows refill
         → another 30-second blackout begins

Result: validator cannot complete state sync before epoch boundary,
        misses chunk production, loses staking rewards.
``` [3](#0-2) [12](#0-11)

### Citations

**File:** chain/client/src/state_request_actor.rs (L22-31)
```rust
pub struct StateRequestActor {
    clock: Clock,
    state_sync_adapter: ChainStateSyncAdapter,
    epoch_manager: Arc<dyn EpochManagerAdapter>,
    chain_store: ChainStoreAdapter,
    genesis_hash: CryptoHash,
    throttle_period: Duration,
    num_state_requests_per_throttle_period: usize,
    state_request_timestamps: Arc<Mutex<VecDeque<Instant>>>,
}
```

**File:** chain/client/src/state_request_actor.rs (L64-70)
```rust
            throttle_period,
            num_state_requests_per_throttle_period,
            state_request_timestamps: Arc::new(Mutex::new(VecDeque::with_capacity(
                num_state_requests_per_throttle_period,
            ))),
        }
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

**File:** chain/client/src/state_request_actor.rs (L225-236)
```rust
        if self.throttle_state_sync_request() {
            tracing::debug!(target: "sync", "throttling state sync request for shard");
            metrics::STATE_SYNC_REQUESTS_THROTTLED_TOTAL.inc();
            return None;
        }

        if self.validate_sync_hash(&sync_hash) == SyncHashValidationResult::Rejected {
            metrics::STATE_SYNC_REQUESTS_SERVED_TOTAL
                .with_label_values(&["header", "failed"])
                .inc();
            return None;
        }
```

**File:** chain/client/src/state_request_actor.rs (L271-274)
```rust
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

**File:** chain/network/src/rate_limits/messages_limits.rs (L269-271)
```rust
        PeerMessage::StateRequestHeader(_, _) => Some((StateRequestHeader, 1)),
        PeerMessage::StateRequestPart(_, _, _) => Some((StateRequestPart, 1)),
        PeerMessage::VersionedStateResponse(_) => Some((VersionedStateResponse, 1)),
```

**File:** nearcore/src/lib.rs (L530-542)
```rust
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
```

**File:** core/async/src/multithread/runtime_handle.rs (L101-105)
```rust
        thread::spawn(move || {
            let mut instrumentation =
                handle.instrumentation.new_writer_with_global_registration(Some(thread_id));
            let mut actor = make_actor_fn();
            let window_update_ticker = crossbeam_channel::tick(Duration::from_secs(1));
```

**File:** core/chain-configs/src/client_config.rs (L532-537)
```rust
pub fn default_state_request_throttle_period() -> Duration {
    Duration::seconds(30)
}

pub fn default_state_requests_per_throttle_period() -> usize {
    30
```
