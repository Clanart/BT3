No vulnerability found for this question.

**Reasoning:**

The premise is incorrect. `get_leader_schedule_else_compute` (which does fall through to the expensive `compute_leader_schedule` → `leader_schedule_utils::leader_schedule()`) is a private method that is only invoked internally by `LeaderScheduleCache::next_leader_slot` [1](#0-0) , which is used by validator-internal consumers (`poh_recorder`, `replay_stage`, broadcast/scheduling code, and the `wait-for-restart-window` CLI) — none of which are reachable from a remote unprivileged JSON-RPC call.

The actual public `getLeaderSchedule` RPC handler calls `get_epoch_leader_schedule(epoch)` directly, which is a pure cache read with no compute fallback — if the epoch isn't cached it simply returns `None`: [2](#0-1) 

Likewise, `getSlotLeader` calls the O(1) `bank.leader_id()` [3](#0-2) , and `getSlotLeaders`/`getBlockProduction` call `get_slot_leaders`, which also only performs a cache lookup via `get_epoch_leader_schedule` and returns an `invalid_params` error rather than computing when the schedule is missing: [4](#0-3) 

The cache lookup itself (`get_epoch_leader_schedule`) is a simple `HashMap` read under an `RwLock`, not the O(epoch_stakes) computation described in the question: [5](#0-4) 

So no public RPC endpoint exposes a path where an unprivileged client can force `compute_leader_schedule` to run on every call by choosing an epoch near `max_epoch`. Cache misses on the exposed endpoints degrade gracefully to `None`/error, not to full recomputation. The scenario described in the question does not correspond to a reachable code path.

### Citations

**File:** ledger/src/leader_schedule_cache.rs (L116-121)
```rust
        // Collect leader schedules first so they stay alive for the iterator chain
        let schedules: Vec<_> = (epoch..=max_epoch)
            .map(|epoch| self.get_leader_schedule_else_compute(epoch, bank))
            .while_some()
            .zip(epoch..)
            .collect();
```

**File:** ledger/src/leader_schedule_cache.rs (L187-189)
```rust
    pub fn get_epoch_leader_schedule(&self, epoch: Epoch) -> Option<Arc<LeaderSchedule>> {
        self.cached_schedules.read().unwrap().0.get(&epoch).cloned()
    }
```

**File:** rpc/src/rpc.rs (L990-993)
```rust
    fn get_slot_leader(&self, config: RpcContextConfig) -> Result<String> {
        let bank = self.get_bank_with_config(config)?;
        Ok(bank.leader_id().to_string())
    }
```

**File:** rpc/src/rpc.rs (L995-1022)
```rust
    fn get_slot_leaders(
        &self,
        commitment: Option<CommitmentConfig>,
        start_slot: Slot,
        limit: usize,
    ) -> Result<Vec<Pubkey>> {
        let bank = self.bank(commitment);

        let (mut epoch, mut slot_index) =
            bank.epoch_schedule().get_epoch_and_slot_index(start_slot);

        let mut slot_leaders = Vec::with_capacity(limit);
        while slot_leaders.len() < limit {
            if let Some(leader_schedule) =
                self.leader_schedule_cache.get_epoch_leader_schedule(epoch)
            {
                slot_leaders.extend(
                    leader_schedule
                        .get_slot_leaders()
                        .map(|slot_leader| slot_leader.id)
                        .skip(slot_index as usize)
                        .take(limit.saturating_sub(slot_leaders.len())),
                );
            } else {
                return Err(Error::invalid_params(format!(
                    "Invalid slot range: leader schedule for epoch {epoch} is unavailable"
                )));
            }
```

**File:** rpc/src/rpc.rs (L2981-2984)
```rust
            Ok(meta
                .leader_schedule_cache
                .get_epoch_leader_schedule(epoch)
                .map(|leader_schedule| {
```
