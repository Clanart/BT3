## Title
Cache-stampede in `LeaderScheduleCache::get_leader_schedule_else_compute` allows a single low-rate client to multiply leader-schedule computation cost via concurrent requests for the same uncached epoch - (File: `ledger/src/leader_schedule_cache.rs`)

### Summary
`get_leader_schedule_else_compute` performs an unsynchronized read-check-then-compute sequence: it reads the cache, and on a miss calls `compute_leader_schedule`, which unconditionally runs the full `leader_schedule_utils::leader_schedule(epoch, bank)` computation *before* taking the write lock to insert the result. The lock is only used to deduplicate the *insertion*, not the *computation*. Multiple threads racing on the same not-yet-cached epoch therefore each perform the full computation redundantly.

### Finding Description [1](#0-0) 

`get_leader_schedule_else_compute` first tries `get_epoch_leader_schedule` (a plain read-lock lookup): [2](#0-1) 

On a miss it calls `compute_leader_schedule`: [3](#0-2) 

The comment on line 212-213 explicitly acknowledges the race ("Check to see if schedule exists in case somebody already inserted in the time we were waiting for the lock") but only addresses correctness of the cache contents via `Entry::Vacant`, not the redundant work performed by `leader_schedule_utils::leader_schedule(epoch, bank)` at line 208, which runs to completion for every racing thread before any lock is even acquired.

The existing unit test `test_thread_race_leader_schedule_cache` spawns 10 threads that all race into the cache for the same slot/epoch and asserts only correctness (a single entry ends up cached), not that redundant computation was avoided: [4](#0-3) 

This confirms that the "many threads race and compute redundantly, but only one wins the insert" behavior is a real, exercised code path — it is simply not tested for CPU cost, only for cache-state correctness.

Because `slot_leader_at_else_compute` (line 170-185) and `next_leader_slot` (line 98-151) both call into this same else-compute path, any public entry point that resolves an epoch's leader schedule for an epoch not already resident in `cached_schedules` is exposed to this stampede pattern whenever hit concurrently.

### Impact Explanation
An unprivileged client that opens multiple concurrent RPC connections and issues repeated requests resolving to the same not-yet-cached epoch (e.g., a freshly rooted epoch, immediately after `set_root` advances `max_epoch`, or any epoch evicted from the bounded `MAX_SCHEDULES = 10` LRU-style cache via `retain_latest`) can cause N concurrent threads to each execute the full `leader_schedule_utils::leader_schedule` computation instead of one. This multiplies CPU cost roughly N-fold for a single logical piece of work, consuming RPC/JSON-RPC worker thread time that would otherwise service other clients' requests, degrading RPC/pubsub responsiveness for others. This matches the "single-client low-rate RPC crash/degradation" impact category, since the number of concurrent connections is separate from the request *rate* — a client can hold many connections open and fire requests simultaneously while staying under a requests-per-second limit measured per connection or per IP.

### Likelihood Explanation
The race window is narrow (it exists only while an epoch's schedule is uncached) but is reliably reachable: cache misses occur for newly rooted epochs, immediately after cache eviction (`MAX_SCHEDULES = 10` cap via `retain_latest`), or for any epoch a validator has not yet been asked to compute. Because there's no per-epoch mutex/single-flight guard, any burst of concurrent requests targeting the same uncached epoch will reliably reproduce N redundant computations — this is deterministic given concurrent access to the same uncached key, not a low-probability timing issue.

### Recommendation
Introduce per-epoch computation de-duplication (a "single-flight" pattern), e.g. by inserting an in-progress marker/future into `cached_schedules` (or a separate `HashMap<Epoch, OnceLock<...>>`/lock-per-epoch) under the write lock *before* running `leader_schedule_utils::leader_schedule`, so that concurrent callers for the same epoch wait on the in-flight computation rather than each independently recomputing it.

### Proof of Concept
Extend the existing `test_thread_race_leader_schedule_cache` test (`ledger/src/leader_schedule_cache.rs`, lines 322-367) to instrument `leader_schedule_utils::leader_schedule` invocation counts (e.g., via an atomic counter wrapping the call, or timing total wall/CPU time), spawn N threads that all call `get_leader_schedule_else_compute`/`slot_leader_at` for the same uncached epoch simultaneously (using the existing barrier via `bounded` channel + `sender.send`), and compare total invocations/CPU time against a baseline of N=1. Because `compute_leader_schedule` (lines 207-222) unconditionally calls `leader_schedule_utils::leader_schedule` before the `Entry::Vacant` check, the instrumented counter would show approximately N calls to the underlying computation for N concurrent racers, versus 1 for a single caller, while `cached_schedules.len()` remains 1 (per the existing assertions at lines 364-366) — demonstrating wasted, unbounded-with-N CPU work despite correct cache-content deduplication.

### Citations

**File:** ledger/src/leader_schedule_cache.rs (L187-189)
```rust
    pub fn get_epoch_leader_schedule(&self, epoch: Epoch) -> Option<Arc<LeaderSchedule>> {
        self.cached_schedules.read().unwrap().0.get(&epoch).cloned()
    }
```

**File:** ledger/src/leader_schedule_cache.rs (L191-205)
```rust
    fn get_leader_schedule_else_compute(
        &self,
        epoch: Epoch,
        bank: &Bank,
    ) -> Option<Arc<LeaderSchedule>> {
        if let Some(ref fixed_schedule) = self.fixed_schedule {
            return Some(fixed_schedule.leader_schedule.clone());
        }
        let epoch_schedule = self.get_epoch_leader_schedule(epoch);
        if epoch_schedule.is_some() {
            epoch_schedule
        } else {
            self.compute_leader_schedule(epoch, bank)
        }
    }
```

**File:** ledger/src/leader_schedule_cache.rs (L207-222)
```rust
    fn compute_leader_schedule(&self, epoch: Epoch, bank: &Bank) -> Option<Arc<LeaderSchedule>> {
        let leader_schedule = leader_schedule_utils::leader_schedule(epoch, bank);
        leader_schedule.map(|leader_schedule| {
            let leader_schedule = Arc::new(leader_schedule);
            let (ref mut cached_schedules, ref mut order) = *self.cached_schedules.write().unwrap();
            // Check to see if schedule exists in case somebody already inserted in the time we were
            // waiting for the lock
            let entry = cached_schedules.entry(epoch);
            if let Entry::Vacant(v) = entry {
                v.insert(leader_schedule.clone());
                order.push_back(epoch);
                Self::retain_latest(cached_schedules, order, self.max_schedules());
            }
            leader_schedule
        })
    }
```

**File:** ledger/src/leader_schedule_cache.rs (L322-367)
```rust
    #[test]
    fn test_thread_race_leader_schedule_cache() {
        let num_runs = 10;
        for _ in 0..num_runs {
            run_thread_race()
        }
    }

    fn run_thread_race() {
        let slots_per_epoch = MINIMUM_SLOTS_PER_EPOCH;
        let epoch_schedule = EpochSchedule::custom(slots_per_epoch, slots_per_epoch / 2, true);
        let GenesisConfigInfo { genesis_config, .. } = create_genesis_config(2);
        let bank = Arc::new(Bank::new_for_tests(&genesis_config));
        let cache = Arc::new(LeaderScheduleCache::new(epoch_schedule, &bank));

        let num_threads = 10;
        let (threads, senders): (Vec<_>, Vec<_>) = (0..num_threads)
            .map(|_| {
                let cache = cache.clone();
                let bank = bank.clone();
                let (sender, receiver) = bounded(1024);
                (
                    Builder::new()
                        .name("test_thread_race_leader_schedule_cache".to_string())
                        .spawn(move || {
                            let _ = receiver.recv();
                            cache.slot_leader_at(bank.slot(), Some(&bank));
                        })
                        .unwrap(),
                    sender,
                )
            })
            .unzip();

        for sender in &senders {
            sender.send(true).unwrap();
        }

        for t in threads.into_iter() {
            t.join().unwrap();
        }

        let (ref cached_schedules, ref order) = *cache.cached_schedules.read().unwrap();
        assert_eq!(cached_schedules.len(), 1);
        assert_eq!(order.len(), 1);
    }
```
