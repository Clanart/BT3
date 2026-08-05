Based on the available evidence, I found a strong structural analog to the reported bug class within Agave's epoch-rewards distribution code, though I was unable to confirm within the indexed portion of the codebase whether the Stake program enforces any active-epoch-rewards lock on `Withdraw`/`Deactivate`/`Split`/`Merge` instructions (no matches were found under `programs/stake*` for `EpochRewards` or the individual instruction handlers, which may be a limitation of the index rather than proof that no such guard exists).

### Title
Calculated stake/inflation rewards are silently burned instead of credited when a stake account changes state between the calculation and distribution blocks - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
Agave computes per-epoch stake rewards once (at the "calculation" block) and then pays them out over many subsequent blocks (the "distribution" partition window). If, by the time a given stake account's partition is processed, that account can no longer be found in the live `StakesCache` in the exact delegated form expected — or the reward add overflows, or `set_state` fails — the code path in `build_updated_stake_reward` returns a `DistributionError`, and the already-computed reward for that account is added to `stake_reward_lamports_burned`/`block_reward_lamports_burned` and simply dropped rather than credited to the owner.

### Finding Description
`store_stake_accounts_in_partition` iterates the partition's `PartitionedStakeReward` entries and calls `Bank::build_updated_stake_reward`, which looks the stake account up in the current `stakes_cache_accounts` snapshot: [1](#0-0) 

If the account is missing, `DistributionError::AccountNotFound` is returned. In `store_stake_accounts_in_partition`, any error causes the pre-computed `stake_reward_amount`/`block_reward_amount` to be added to the burned counters instead of being paid out: [2](#0-1) 

The function-level doc comment explicitly assumes this can't happen: *"Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned."* [3](#0-2) 

The invariant this relies on is that the Stake program refuses to let a staker withdraw, deactivate-and-close, split, or merge a stake account while epoch rewards are "active" (i.e., between the calculation block and the end of the distribution window). I searched for this guard under `programs/stake*` for any reference to `EpochRewards`/`epoch_rewards` and found none in the indexed portion of the codebase. Note there is a separate `recalculate_partitioned_rewards_if_active`/`recalculate_stake_rewards` path used after snapshot restore that recomputes rewards from the live `StakesCache`, which suggests the system is aware that stake state can legitimately change across the distribution window and tries to recover pending amounts in that specific case — but the per-block `store_stake_accounts_in_partition` path itself has no such recovery; a lookup miss there is treated as a hard burn.

In `distribute_epoch_rewards_in_partition`, only `stake_reward_lamports_minted` is added to capitalization; `stake_reward_lamports_burned` is never added, meaning the amount is genuinely never credited to the owner or to anyone else — it is a pure loss of a reward the protocol had already committed to (the reward was already subtracted from the `EpochRewards` sysvar's total distributable pool via `update_epoch_rewards_sysvar(stake_reward_lamports_minted + stake_reward_lamports_burned, ...)`): [4](#0-3) 

This mirrors the Datatrust bug exactly at the conceptual level: a reward that has already accrued to a specific owner's position is destroyed by a "removal" event (account closure/merge/split) that races the payout mechanism, with no fallback path to return the value to the rightful owner in the per-partition distribution flow.

### Impact Explanation
If a staker's account is closed, merged, split, or otherwise removed from the live stake-delegation set between reward calculation and their assigned distribution block/partition, the SOL reward they were already entitled to for that epoch is permanently destroyed rather than paid to them (fund loss for an unprivileged, legitimate protocol participant). Since `stake_reward_lamports_burned` is excluded from the capitalization increase, this is a genuine, unrecoverable loss of expected value, not merely a deferred credit.

### Likelihood Explanation
Whether this path is reachable in practice depends entirely on whether the Stake program instruction handlers (`Withdraw`, `Deactivate`, `Split`, `Merge`, account closure) check the `EpochRewards` sysvar's `active` flag and reject stake mutations during the distribution window. I could not locate such a check anywhere under `programs/stake*` in the indexed code, which is the exact mechanism the `store_stake_accounts_in_partition` doc comment assumes exists. Because I cannot fully rule in or rule out the existence of this guard from the available index (it may exist in code that wasn't returned by search, e.g. via a shared "reserved account keys" / "vote-and-stake restrictions" mechanism I did not locate), I present this with moderate confidence rather than certainty — a Devin session with full filesystem access would be needed to definitively confirm or refute whether any stake instruction is blocked while `EpochRewards.active == true`.

### Recommendation
- Short term: Explicitly verify (via `programs/stake` instruction processors) that all stake-mutating instructions (`Withdraw`, `Deactivate`, `Split`, `Merge`, account closes) check `EpochRewards::active` from the sysvar and are rejected while active, closing the gap the distribution code currently only assumes.
- If no such enforcement exists, add it, or alternatively make `build_updated_stake_reward`'s `AccountNotFound`/`UnableToSetState` cases fall back to crediting the reward to a recoverable location (e.g., re-added to the pool for the next epoch, or credited to the account's last-known owner via a system-level transfer) instead of unconditionally burning it.
- Long term: add an invariant test/fuzzer that concurrently mutates stake accounts (close/split/merge) during the reward-distribution block range and asserts `stake_reward_lamports_burned == 0` for all legitimately-active accounts, to continuously validate the assumption stated in the code comment.

### Proof of Concept
Conceptual PoC (not runnable without full stake-program source, which wasn't available in the index):
1. At epoch boundary, `calculate_rewards_for_partitioning` computes a `PartitionedStakeReward` for stake account `S` with a non-zero `stake_reward`/`block_reward`.
2. Rewards enter the distribution phase and are spread across many blocks via `partition_indices` (see `hash_rewards_into_partitions`), so `S`'s payout may be scheduled several blocks after calculation: [5](#0-4) 
3. If, before `S`'s specific partition block is reached, the stake owner (or any transaction) manages to remove/alter `S`'s state in `StakesCache` such that `stakes_cache_accounts.get(&S)` no longer returns the expected delegation (e.g., account closed via `Withdraw`, or state changed via `Split`/`Merge`) — assuming the Stake program does not block this while `EpochRewards.active` — then when `S`'s partition is processed, `build_updated_stake_reward` returns `AccountNotFound`.
4. `store_stake_accounts_in_partition` adds `S`'s reward amount to `stake_reward_lamports_burned`, which is never added to capitalization or paid to anyone: [6](#0-5) 
5. Confirming whether step 3's precondition (no stake-program-level block) actually holds requires reading the Stake program instruction processors directly, which were not present in the search index — this should be verified in a full-repository session.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L137-149)
```rust
        let distribution_end_exclusive =
            distribution_starting_block_height + partition_rewards.partition_indices.len() as u64;

        assert!(
            self.epoch_schedule.get_slots_in_epoch(self.epoch)
                > partition_rewards.partition_indices.len() as u64
        );

        if height >= distribution_starting_block_height && height < distribution_end_exclusive {
            let partition_index = height - distribution_starting_block_height;

            self.distribute_epoch_rewards_in_partition(partition_rewards, partition_index);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L192-204)
```rust
        // increase total capitalization by the distributed rewards
        self.capitalization
            .fetch_add(stake_reward_lamports_minted, Relaxed);

        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);

        // decrease distributed capital from epoch rewards sysvar
        self.update_epoch_rewards_sysvar(
            stake_reward_lamports_minted + stake_reward_lamports_burned,
            block_reward_lamports_distributed + block_reward_lamports_burned,
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-252)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L327-335)
```rust
    /// Store stake rewards in partition
    /// Returns DistributionResults containing the sum of all the rewards
    /// stored, the sum of all rewards burned, and the updated StakeRewards.
    /// Because stake accounts are checked in calculation, and further state
    /// mutation prevents by stake-program restrictions, there should never be
    /// rewards burned.
    ///
    /// Note: even if staker's reward is 0, the stake account still needs to be
    /// stored because credits observed has changed
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L393-407)
```rust
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
            }
```
