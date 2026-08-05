## Analog Found

The Linea report's core pattern — **a hash used as a dedup key can be non-unique, causing legitimate/new data to be falsely treated as "already submitted" and dropped** — has a direct structural analog in Agave's shred deduplication logic, which uses a Bloom-filter-style probabilistic hash structure (`Deduper`) as the "already seen" cardinality for shreds flowing through the sigverify and retransmit pipelines.

### Title
Bloom-filter shred deduplication false positives can silently drop legitimate, unique shreds - (`turbine/src/sigverify_shreds.rs`, `turbine/src/retransmit_stage.rs`, `perf/src/deduper.rs`)

### Summary
Agave's shred pipeline uses `Deduper<K, T>`, a k-hash Bloom-style filter, as the sole "have we seen this data" check for shreds in both `spawn_shred_sigverify`/`run_shred_sigverify` and `RetransmitStage`. Like the Linea `compressedData` hash used as the dedup key in a mapping, this filter uses a keyed hash of shred bytes (or `ShredId`) as the cardinality for "already processed." Because it is a probabilistic filter rather than an exact-match structure, it has an inherent, non-zero collision (false-positive) rate. A false positive causes a genuinely new/unique shred to be marked as duplicate and dropped, exactly mirroring how a colliding `dataHash` in Linea blocks otherwise-valid, distinct data from being accepted.

### Finding Description
`Deduper::dedup` hashes the input with `K` independent keyed hash functions into a shared bit array and returns `true` ("duplicate") if all corresponding bits are already set: [1](#0-0) 

This structure is instantiated with a fixed size and a target false-positive rate, and is used to gate whether a shred packet gets discarded during sigverify: [2](#0-1) [3](#0-2) 

The code itself documents that this is a real, acknowledged risk for shred repair: [4](#0-3) 

The same pattern recurs in `RetransmitStage`'s `ShredDeduper`, which uses a header-bytes filter plus a `(ShredId, duplicate_index)` filter (tolerating up to `MAX_DUPLICATE_COUNT = 2` legitimate re-observations) before deciding whether to retransmit a shred downstream in turbine: [5](#0-4) [6](#0-5) 

The filter is only reset when its measured false-positive rate crosses a configured threshold (`0.001`) or a fixed time window elapses: [7](#0-6) [8](#0-7) 

The corrupted value is the shared bit-array state inside `Deduper` (`self.bits`): once bits corresponding to a *different* item's hash outputs happen to already be set (via prior insertions from unrelated shreds), a brand-new, entirely legitimate shred hashes to the same set bits and is wrongly classified as `already seen`, causing it to be discarded (`packet.meta_mut().set_discard(true)`) before signature verification/forwarding ever happens.

### Impact Explanation
When a false positive occurs for a genuinely new shred at the sigverify stage, that shred is discarded and never verified or forwarded, and per the code's own comment this can block that node from completing/repairing a block "until the deduper is reset after `DEDUPER_RESET_CYCLE`" (up to 5 minutes). At the `RetransmitStage`, a false positive on the header filter or the `ShredId` filter causes that node to skip retransmitting a shred to its downstream turbine fan-out, which can delay shred propagation to that subtree of the network until repair kicks in. This is degradation/self-DoS of a single client's shred processing pipeline (shreds/repair path), not a cluster-wide consensus break, fund-loss, or false-acceptance bug — it is bounded in scope and self-healing by design (auto-reset on FPR threshold or timer).

### Likelihood Explanation
Existing guards (fixed target false-positive rate of 0.1%, periodic/threshold-triggered resets, and the `MAX_DUPLICATE_COUNT` tolerance in `ShredDeduper`) bound the practical impact and make it self-limiting. An attacker cannot deliberately target a specific victim shred for collision because the hash keys (`random_states`) are private, per-process, and rotated on every reset — this is unlike Linea's issue where an attacker with full control over `compressedData` content could deliberately choose bytes to force a specific hash collision. Here, the only lever available to a remote attacker is flooding enough distinct packets/shreds to push the shared filter toward saturation (tens of millions of items within the reset window per the documented capacity constants), which raises the ambient false-positive rate up to the engineered 0.1% ceiling before the structure self-resets. This is a real, accepted design trade-off (explicitly called out in the source comments) rather than a novel unmitigated vulnerability, so likelihood of meaningful, sustained impact is low.

### Recommendation
No code change is proposed here since the risk is already documented and bounded by design (analogous to Linea's own "acknowledged, not resolved" stance). If tighter guarantees are desired, consider exact-match dedup keys (e.g., full shred ID + content digest in a bounded exact map) for the correctness-critical repair path instead of a probabilistic filter, or shortening the reset cycle / lowering the false-positive threshold to reduce the worst-case window of degraded shred propagation.

### Proof of Concept
Not applicable as a concrete exploit — the collision mechanism is probabilistic and keyed by private per-process random state, so no deterministic, low-cost trigger exists; the existing test `test_dedup_false_positive` in `perf/src/deduper.rs` demonstrates the intrinsic (non-adversarial) false-positive behavior of the structure: [9](#0-8) 

Given the strict "Valid Impact" bar (fund theft/loss, false execution/rooting/acceptance, consensus halt, non-RPC remote exhaustion/crash, or single-client degradation) and that this is a bounded, self-healing, already-understood design trade-off rather than an exploitable, attacker-controlled collision path, this analog is presented for completeness but does not clearly meet a high-severity bar on its own.

### Citations

**File:** perf/src/deduper.rs (L57-95)
```rust
    fn false_positive_rate(&self) -> f64 {
        let popcount = self.popcount.load(Ordering::Relaxed);
        let ones_ratio = popcount.min(self.num_bits) as f64 / self.num_bits as f64;
        ones_ratio.powi(K as i32)
    }

    /// Reset is not synchronized with concurrent `dedup()` calls. A caller can
    /// see an inconsistent snapshot across the old/new hash state and the
    /// cleared/refilled bitset, but that is acceptable because reset already
    /// starts a fresh deduplication window.
    fn reset<R: Rng>(&self, rng: &mut R) {
        for bits in &self.bits {
            bits.store(0, Ordering::Relaxed);
        }
        self.popcount.store(0, Ordering::Relaxed);
        self.state.store(Arc::new(DeduperGeneration::new(rng)));
    }

    /// Resets the Deduper if either it is older than the reset_cycle or it is
    /// saturated enough that false positive rate exceeds specified threshold.
    ///
    /// This is not intended to be run in parallel with other resets, only in
    /// parallel with `dedup()` calls.
    ///
    /// Returns true if the deduper was saturated.
    pub fn maybe_reset<R: Rng>(
        &self,
        rng: &mut R,
        false_positive_rate: f64,
        reset_cycle: Duration,
    ) -> bool {
        assert!(0.0 < false_positive_rate && false_positive_rate < 1.0);
        let _reset_guard = self.reset_guard.lock().unwrap();
        let saturated = self.false_positive_rate() >= false_positive_rate;
        if saturated || self.state.load().started_at.elapsed() >= reset_cycle {
            self.reset(rng);
        }
        saturated
    }
```

**File:** perf/src/deduper.rs (L97-114)
```rust
    // Returns true if the data is duplicate.
    #[must_use]
    #[allow(clippy::arithmetic_side_effects)]
    pub fn dedup(&self, data: &T) -> bool {
        let mut out = true;
        let state = self.state.load();
        for random_state in state.random_states.iter() {
            let hash: u64 = random_state.hash_one(data) % self.num_bits;
            let index = (hash >> 6) as usize;
            let mask: u64 = 1u64 << (hash & 63);
            let old = self.bits[index].fetch_or(mask, Ordering::Relaxed);
            if old & mask == 0u64 {
                self.popcount.fetch_add(1, Ordering::Relaxed);
                out = false;
            }
        }
        out
    }
```

**File:** perf/src/deduper.rs (L259-272)
```rust
    #[test]
    fn test_dedup_false_positive() {
        let mut rng = rand::rng();
        let filter = Deduper::<2, [u8]>::new(&mut rng, NUM_BITS);
        let mut discard = 0;
        for i in 0..10 {
            let mut batches =
                to_packet_batches(&(0..1024).map(|_| test_tx()).collect::<Vec<_>>(), 128);
            discard += dedup_packets_and_count_discards(&filter, &mut batches) as usize;
            debug!("false positive rate: {}/{}", discard, i * 1024);
        }
        //allow for 1 false positive even if extremely unlikely
        assert!(discard < 2);
    }
```

**File:** turbine/src/sigverify_shreds.rs (L47-49)
```rust
const DEDUPER_FALSE_POSITIVE_RATE: f64 = 0.001;
const DEDUPER_NUM_BITS: u64 = 637_534_199; // 76MB
const DEDUPER_RESET_CYCLE: Duration = Duration::from_secs(5 * 60);
```

**File:** turbine/src/sigverify_shreds.rs (L174-184)
```rust
    // Repair shreds include a randomly generated u32 nonce, so it does not
    // make sense to deduplicate the entire packet payload (i.e. they are not
    // duplicate of any other packet.data(..)).
    // If the nonce is excluded from the deduper then false positives might
    // prevent us from repairing a block until the deduper is reset after
    // DEDUPER_RESET_CYCLE. A workaround is to also repair "coding" shreds to
    // add some redundancy but that is not implemented at the moment.
    // Because the repair nonce is already verified in shred-fetch-stage we can
    // exclude repair shreds from the deduper, but we still need to pass the
    // repair shred to the deduper to filter out duplicates from the turbine
    // path once a shred is repaired.
```

**File:** turbine/src/sigverify_shreds.rs (L186-199)
```rust
    // after the shred payload, but have to exclude them here from the deduper.
    stats.num_duplicates += thread_pool.install(|| {
        shred_buffer
            .par_iter_mut()
            .flatten()
            .filter(|packet| {
                !packet.meta().discard()
                    && shred::wire::get_shred(packet.as_ref())
                        .map(|shred| deduper.dedup(shred))
                        .unwrap_or(true)
                    && !packet.meta().repair()
            })
            .map(|mut packet| packet.meta_mut().set_discard(true))
            .count()
```

**File:** turbine/src/retransmit_stage.rs (L53-56)
```rust
const MAX_DUPLICATE_COUNT: usize = 2;
const DEDUPER_FALSE_POSITIVE_RATE: f64 = 0.001;
const DEDUPER_NUM_BITS: u64 = 637_534_199; // 76MB
const DEDUPER_RESET_CYCLE: Duration = Duration::from_secs(5 * 60);
```

**File:** turbine/src/retransmit_stage.rs (L240-257)
```rust
    // Returns true if the shred is duplicate and should be discarded.
    #[must_use]
    fn dedup(&self, key: ShredId, shred: &[u8], max_duplicate_count: usize) -> bool {
        // Shreds in the retransmit stage:
        //   * don't have repair nonce (repaired shreds are not retransmitted).
        //   * are already resigned by this node as the retransmitter.
        //   * have their leader's signature verified.
        // Therefore in order to dedup shreds, it suffices to compare:
        //    (signature, slot, shred-index, shred-type)
        // Because ShredCommonHeader already includes all of the above tuple,
        // the rest of the payload can be skipped.
        // In order to detect duplicate blocks across cluster, we retransmit
        // max_duplicate_count different shreds for each ShredId.
        shred::layout::get_common_header_bytes(shred)
            .map(|header| self.deduper.dedup(header))
            .unwrap_or(true)
            || (0..max_duplicate_count).all(|i| self.shred_id_filter.dedup(&(key, i)))
    }
```

**File:** turbine/src/retransmit_stage.rs (L504-510)
```rust
    let key = shred::layout::get_shred_id(shred.as_ref())?;
    if key.slot() < root_bank.slot()
        || shred_deduper.dedup(key, shred.as_ref(), MAX_DUPLICATE_COUNT)
    {
        stats.num_shreds_skipped.fetch_add(1, Ordering::Relaxed);
        return None;
    }
```
