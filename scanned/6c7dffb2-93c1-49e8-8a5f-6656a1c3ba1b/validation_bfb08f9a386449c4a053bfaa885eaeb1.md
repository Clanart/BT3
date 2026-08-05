No vulnerability found for this question.

**Reasoning:**

The premise that an unprivileged attacker can flood `shred_deduper` in `retransmit_stage.rs` with arbitrary "distinct headers purely to consume capacity" does not hold, because shreds never reach `retransmit_shred` / `shred_deduper.dedup()` unless they first pass through `run_shred_sigverify` in `sigverify_shreds.rs`, which:

1. Applies its own upstream `Deduper` to discard exact duplicates before signature verification [1](#0-0) .
2. Calls `verify_packets` and `maybe_verify_and_resign_packet`, which verify the shred's leader signature against the actual slot leader from `leader_schedule_cache`/`bank_forks`, discarding anything that fails [2](#0-1) .
3. Only shreds surviving that verification are forwarded via `retransmit_sender` into the channel that `retransmit_stage.rs::retransmit` drains and passes to `retransmit_shred` [3](#0-2) .

Since `ShredDeduper::dedup` hashes the `ShredCommonHeader` bytes (signature, slot, index, type) [4](#0-3) , an attacker would need many distinct, validly-signed headers for slots `>= root_bank.slot()` to actually consume bloom-filter capacity — that requires forging a real slot leader's signature (blocked by the sigverify stage) or actually being the slot leader (a privileged/leader capability, explicitly out of scope per "Reject malicious peer/node/validator assumptions").

Additionally, the deduper capacity (`DEDUPER_NUM_BITS = 637_534_199`, ~76MB, false-positive threshold 0.001) supports roughly 20M unique entries before `maybe_reset` triggers a reset every up to 5 minutes [5](#0-4) , and the design explicitly accounts for false-positive saturation via periodic resets [6](#0-5) . This is a known, intentional bloom-filter tradeoff (documented in-code) rather than an exploitable defect, and requires leader-level shred production volume to meaningfully saturate — which is excluded by the review scope.

Because the attack path fundamentally depends on forging signatures (already rejected by existing checks) or assuming a malicious/leader validator (excluded scope), this does not constitute a valid unprivileged vulnerability.

### Citations

**File:** turbine/src/sigverify_shreds.rs (L187-200)
```rust
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
    });
```

**File:** turbine/src/sigverify_shreds.rs (L205-238)
```rust
    verify_packets(
        thread_pool,
        &keypair.pubkey(),
        &working_bank,
        leader_schedule_cache,
        shred_buffer,
        cache,
    );
    stats.num_discards_post += count_discards(shred_buffer);
    // Verify retransmitter's signature, and resign shreds
    // Merkle root as the retransmitter node.
    let resign_start = Instant::now();
    thread_pool.install(|| {
        shred_buffer
            .par_iter_mut()
            .flatten()
            .filter(|packet| !packet.meta().discard())
            .for_each(|mut packet| {
                if maybe_verify_and_resign_packet(
                    &mut packet,
                    &root_bank,
                    &working_bank,
                    cluster_info,
                    leader_schedule_cache,
                    cluster_nodes_cache,
                    stats,
                    keypair,
                )
                .is_err()
                {
                    packet.meta_mut().set_discard(true);
                }
            })
    });
```

**File:** turbine/src/sigverify_shreds.rs (L264-273)
```rust
    // Repaired shreds are not retransmitted.
    stats.num_retransmit_shreds += shreds.len();
    if let Err(send_err) = retransmit_sender.try_send(shreds.clone()) {
        match send_err {
            crossbeam_channel::TrySendError::Full(v) => {
                stats.num_retransmit_stage_overflow_shreds += v.len();
            }
            _ => unreachable!("EvictingSender holds on to both ends of the channel"),
        }
    }
```

**File:** turbine/src/retransmit_stage.rs (L53-56)
```rust
const MAX_DUPLICATE_COUNT: usize = 2;
const DEDUPER_FALSE_POSITIVE_RATE: f64 = 0.001;
const DEDUPER_NUM_BITS: u64 = 637_534_199; // 76MB
const DEDUPER_RESET_CYCLE: Duration = Duration::from_secs(5 * 60);
```

**File:** turbine/src/retransmit_stage.rs (L242-257)
```rust
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

**File:** perf/src/deduper.rs (L82-95)
```rust
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
