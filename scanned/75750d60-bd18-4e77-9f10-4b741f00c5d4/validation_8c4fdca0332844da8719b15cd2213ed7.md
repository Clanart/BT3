## Assessment: Valid vulnerability confirmed

### Title
Unbounded per-origin `nodes` map growth in `ReceivedCacheEntry::record` via spoofed `from` pubkeys in gossip Push messages - (File: `gossip/src/received_cache.rs`)

### Summary
`ReceivedCacheEntry::record` only enforces the `CAPACITY = 50` bound on `nodes: HashMap<Pubkey, usize>` in the `else if` branch (taken when `num_dups >= NUM_DUPS_THRESHOLD`). The primary branch, taken whenever `num_dups < NUM_DUPS_THRESHOLD` (i.e. `num_dups == 0` or `1`), unconditionally does `self.nodes.entry(node).or_default()` with no size check. [1](#0-0) 

### Finding Description
`CrdsGossipPush::process_push_message` calls `received_cache.record(origin, from, num_dups)` for every `CrdsValue` in an incoming `Protocol::PushMessage`, using `from` — the sender field of the message — as the `node` key: [2](#0-1) 

- When `crds.insert()` succeeds (the normal case for any accepted, newer/self-signed value), `record(origin, from, /*num_dups:*/ 0)` is called.
- `num_dups == 0` is `< NUM_DUPS_THRESHOLD (2)`, so `record` takes the score-increment branch (`self.nodes.entry(node).or_default()`), which inserts unconditionally — no `CAPACITY` check.
- Only when `num_dups >= 2` (e.g. repeated/late duplicates, or `InsertFailed` → `num_dups = usize::MAX`) does the code reach the `else if self.nodes.len() < Self::CAPACITY` branch that actually caps growth.

The `from` field in `Protocol::PushMessage(from, values)` is not cryptographically bound to the sender's identity — only the `CrdsValue` itself is self-signed by its `origin` pubkey; `from` is simply metadata supplied by whichever peer sent the UDP packet. An attacker who is a normal (unprivileged) gossip participant can therefore:
1. Sign a stream of `CrdsValue`s under a single `origin` key they control, each with a monotonically increasing `wallclock` (so `crds.insert()` always succeeds, i.e. `num_dups = 0`).
2. Set an arbitrary, distinct, unauthenticated `from` pubkey on every packet.

Each such packet causes exactly one unconditional insertion into `ReceivedCacheEntry::nodes` for that `origin`, with no bound. Since the outer `ReceivedCache` is an LRU keyed by `origin` with capacity `CRDS_UNIQUE_PUBKEY_CAPACITY`, the *number of origins* is bounded, but the *size of the inner `nodes` map per origin* is not, and `Self::CAPACITY = 50` is documented as intending to be that bound ("Limit how big the cache can get if it is spammed with old messages with random pubkeys" — but this comment only applies to the `else if` branch). [3](#0-2) 

### Impact Explanation
An attacker can drive one `ReceivedCacheEntry.nodes` HashMap to grow far beyond the intended 50-entry cap by repeatedly sending Push messages for a single self-controlled origin with distinct spoofed `from` values, causing unbounded heap growth scoped to that structure. This matches the "non-RPC remote exhaustion" impact category (gossip is a public, unprivileged protocol).

### Likelihood Explanation
Reaching this code path requires no special privilege beyond being a gossip peer that can send `Protocol::PushMessage` packets, which is the normal operating mode for any node in the public gossip network. The attack only requires generating an unbounded stream of valid, self-signed `CrdsValue`s (easy — attacker controls the origin key and wallclock) paired with arbitrary `from` pubkeys, which requires no cryptographic forgery since `from` is not authenticated.

### Recommendation
Add the same `self.nodes.len() < Self::CAPACITY` (or equivalent) guard in the `if num_dups < Self::NUM_DUPS_THRESHOLD` branch of `ReceivedCacheEntry::record`, or otherwise bound the total size of `nodes` regardless of which branch is taken, e.g. by checking capacity before any `entry(node).or_default()` call.

### Proof of Concept
A unit test analogous to the existing `test_received_cache` in `gossip/src/received_cache.rs` demonstrates this: call `ReceivedCacheEntry::record` (or `ReceivedCache::record`) with a single `origin`, `num_dups = 0` (or `1`), and 10,000 distinct `node` pubkeys — `entry.nodes.len()` will reach 10,000, well beyond `CAPACITY = 50`, whereas repeating the same test with `num_dups = 2` correctly caps `nodes.len()` at 50 due to the `else if` guard. [4](#0-3) [1](#0-0)

### Citations

**File:** gossip/src/received_cache.rs (L26-35)
```rust
    pub(crate) fn record(&mut self, origin: Pubkey, node: Pubkey, num_dups: usize) {
        match self.0.get_mut(&origin) {
            Some(entry) => entry.record(node, num_dups),
            None => {
                let mut entry = ReceivedCacheEntry::default();
                entry.record(node, num_dups);
                self.0.put(origin, entry);
            }
        }
    }
```

**File:** gossip/src/received_cache.rs (L64-70)
```rust
impl ReceivedCacheEntry {
    // Limit how big the cache can get if it is spammed
    // with old messages with random pubkeys.
    const CAPACITY: usize = 50;
    // Threshold for the number of duplicates before which a message
    // is counted as timely towards node's score.
    const NUM_DUPS_THRESHOLD: usize = 2;
```

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

**File:** gossip/src/crds_gossip_push.rs (L140-154)
```rust
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
