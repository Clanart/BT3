### Title
Read-only accounts cache evictor can loop indefinitely when eviction sample size exceeds cache population - (`accounts-db/src/read_only_accounts_cache.rs`)

### Summary
The `MultiFeeDistribution.withdraw` bug is a loop whose exit/progress condition (`earnedAmount == 0`) can be true forever because the index that should advance the loop is never incremented on that branch. `ReadOnlyAccountsCache::evict` in `accounts-db/src/read_only_accounts_cache.rs` has the same broken-invariant shape: an inner `while remaining_samples > 0` loop assumes that random sampling will always make forward progress toward the sample size it needs, but if the cache does not actually contain `evict_sample_size` elements, the sampling can repeatedly select shards that contribute nothing, `remaining_samples` never reaches `0`, and the loop never proceeds to pick and evict a victim key. The code even carries a comment acknowledging this exact risk.

### Finding Description
`evict()` is the core of the background cache evictor: [1](#0-0) 

The outer loop `while data_size.load(...) > target_data_size` runs until enough bytes have been reclaimed. On each outer iteration it tries to gather `evict_sample_size` candidate keys via the inner loop:

```
let mut remaining_samples = evict_sample_size;
while remaining_samples > 0 {
    let shard = cache.shards().choose(rng)...;
    let shard = shard.read();
    for (key, entry) in shard.iter().choose_multiple(rng, remaining_samples) { ... }
    remaining_samples = remaining_samples.saturating_sub(1); // per successfully examined entry
}
let key = key_to_evict.expect("eviction sample should not be empty");
```

The comment directly above this loop states the invariant that is relied upon and admits it is not enforced:

> "NOTE: This can loop indefinitely if the cache is misconfigured and when we get here there aren't at least `evict_sample_size` elements. We could break the loop on `cache.is_empty()` but calling `is_empty()` and `len()` on a dashmap is very expensive... So, avoid paying that cost and assume that when eviction triggers the cache contains enough items."

This is structurally identical to the reported bug: the loop's progress variable (`earnedAmount`/`remaining_samples`) is expected to change every iteration, but under a specific, reachable state (too few live entries relative to `evict_sample_size`, e.g. because the live set consists of a small number of very large accounts pushing `data_size` above `max_data_size_hi`) the loop keeps re-sampling shards without ever satisfying its termination condition, exactly mirroring the `while (true) { if (earnedAmount == 0) continue; }` pattern from the report — a loop guard that never gets closer to being false because the value it depends on isn't advanced/consumed correctly.

Unlike `do_load()`'s retry loop in `accounts_db.rs`, which has an explicit `num_acceptable_failed_iterations`/`load_limit` bound guarding against unbounded retries, `evict()` has no such bound and the code authors explicitly opted out of the cheap safety check (`is_empty()`), leaving the “assume enough items” invariant unchecked at runtime: [2](#0-1) 

### Impact Explanation
The evictor thread (`solAcctReadCache`) is a background service, not tied to a single request. If it enters the indefinite-spin state described in the comment, it will busy-loop indefinitely, consuming a CPU core in a tight retry cycle and never releasing memory back below `max_data_size_lo`. Because `data_size` can only grow while the evictor is stuck (new stores keep incrementing it, see `store_with_timestamp`), the read-only cache’s memory footprint is effectively unbounded once this state is entered, which can degrade validator performance or lead to memory exhaustion over time. This does not directly cause fund loss or consensus divergence, but it is a legitimate non-RPC resource-exhaustion/degradation vector inside the validator's own accounts-db subsystem.

### Likelihood Explanation
Triggering requires the cache to reach a state where `data_size > max_data_size_hi` while the number of live entries in the cache is smaller than `evict_sample_size`. This is plausible if the cache is populated primarily with a small number of very large accounts (the cache stores things like executable/program accounts which can be large), since `data_size` is based on byte size while the sampling logic is based on entry *count*. The exact defaults for `max_data_size_lo`/`max_data_size_hi`/`evict_sample_size`/`num_shards` were not available/verifiable in this session (index truncation), so I cannot confirm with certainty that default production configuration makes this trivially reachable — this is the key uncertainty in this finding. The code comment itself confirms the authors consider this a real, if presumably rare/"misconfigured", scenario rather than a purely theoretical one.

### Recommendation
Add an explicit, cheap termination guard to the inner sampling loop, e.g., track total entries actually observed across all shard passes (or cap iterations) and break out to evict the best candidate found so far (or skip eviction and log/backoff) once it is clear that `evict_sample_size` cannot be satisfied, rather than relying on an unchecked assumption that enough entries always exist. This mirrors the report's own recommended fix pattern of ensuring the loop's progress condition (here: the running tally of observed candidates) cannot stall.

### Proof of Concept
Conceptual repro path (exact reachability of production defaults not fully verified with local tools in this session):
1. Configure/observe a `ReadOnlyAccountsCache` where `evict_sample_size` is larger than the number of entries that can realistically populate a shard set at low cache occupancy.
2. Drive the cache to hold only a handful of very large accounts (e.g., large program/executable accounts) such that `data_size` exceeds `max_data_size_hi` while `cache_len < evict_sample_size`.
3. The evictor thread's `evict()` inner loop keeps calling `cache.shards().choose(rng)` and `shard.iter().choose_multiple(rng, remaining_samples)`; when it repeatedly hits shards with too few entries, `remaining_samples` cannot reach `0`, so `key_to_evict` is never set, and the outer eviction loop never advances — matching the report's "loop never ends because loop counter/condition never changes" pattern. [1](#0-0)

### Citations

**File:** accounts-db/src/read_only_accounts_cache.rs (L355-409)
```rust
    fn evict<R>(
        target_data_size: usize,
        data_size: &AtomicUsize,
        cache_len: &AtomicUsize,
        evict_sample_size: usize,
        cache: &DashMap<ReadOnlyCacheKey, ReadOnlyAccountCacheEntry, AHashRandomState>,
        rng: &mut R,
        #[cfg(feature = "dev-context-only-utils")] mut callback: impl FnMut(
            &Pubkey,
            Option<ReadOnlyAccountCacheEntry>,
        ),
    ) -> u64
    where
        R: Rng,
    {
        let mut num_evicts: u64 = 0;
        while data_size.load(Ordering::Relaxed) > target_data_size {
            let mut key_to_evict = None;
            let mut min_update_time = u64::MAX;
            let mut remaining_samples = evict_sample_size;
            // NOTE: This can loop indefinitely if the cache is misconfigured
            // and when we get here there aren't at least `evict_sample_size`
            // elements. We could break the loop on `cache.is_empty()` but
            // calling `is_empty()` and `len()` on a dashmap is very expensive
            // as it requires iterating and locking all the shards. So, avoid
            // paying that cost and assume that when eviction triggers the
            // cache contains enough items.
            while remaining_samples > 0 {
                let shard = cache
                    .shards()
                    .choose(rng)
                    .expect("number of shards should be greater than zero");
                let shard = shard.read();
                for (key, entry) in shard.iter().choose_multiple(rng, remaining_samples) {
                    let last_update_time = entry.get().last_update_time.load(Ordering::Relaxed);
                    if last_update_time < min_update_time {
                        min_update_time = last_update_time;
                        key_to_evict = Some(key.to_owned());
                    }

                    remaining_samples = remaining_samples.saturating_sub(1);
                }
            }

            let key = key_to_evict.expect("eviction sample should not be empty");
            let _entry = Self::do_remove(&key, cache, data_size, cache_len);
            #[cfg(feature = "dev-context-only-utils")]
            {
                #[allow(clippy::used_underscore_binding)]
                callback(&key, _entry);
            }
            num_evicts = num_evicts.saturating_add(1);
        }
        num_evicts
    }
```
