## Analog to H-7 (BlueBerryBank premature liquidation from stale underlying value)

The underlying pattern in H-7 is: a quantity is snapshotted/stored once, real state moves on (interest accrues), and a later solvency-style check compares the stale stored value against a freshly computed value — the mismatch is never reconciled and breaks an invariant that the code otherwise assumes always holds. The closest analog I can find in this Agave snapshot is in the partitioned epoch-rewards distribution path, where a stake account's `delegation.stake` is snapshotted at epoch-boundary reward *calculation* time, and that snapshot is later `assert_eq!`-checked against the *live* value of the same account at *distribution* time — without accounting for legitimate stake-account mutations (Split/Merge/etc.) that can occur in between.

### Title
Stale reward-calculation snapshot of `delegation.stake` vs. live stake account state causes a distribution-time `assert_eq!` panic - (File: runtime/src/bank/partitioned_epoch_rewards/distribution.rs)

### Summary
`build_updated_stake_reward` stores an epoch-boundary-computed `new_stake.delegation.stake` (baked into `PartitionedStakeReward` when `adjust_delegations_for_rent` is inactive) and later, at actual distribution time (which happens over many blocks, well after the epoch boundary), recomputes `expected_delegation` from the *live* stake account read out of the current `StakesCache`. It then hard-asserts the two must be equal. Any legitimate account mutation to `delegation.stake` between calculation and distribution (e.g. a `Split`/`Merge` performed by the stake owner) breaks that equality and panics the validator deterministically.

### Finding Description
Reward calculation happens once per epoch boundary from a `stake_delegations` vector snapshotted from `StakesCache` at that instant: [1](#0-0) 

The resulting `PartitionedStakeReward` entries (containing `inflation.stake`, i.e. the post-reward `Stake` computed from that snapshot) are stored in `self.epoch_reward_status` and consumed much later, block by block, throughout the distribution phase: [2](#0-1) 

At actual distribution time, `store_stake_accounts_in_partition` re-reads the account from the **current, live** `StakesCache` (not the epoch-boundary snapshot): [3](#0-2) 

`build_updated_stake_reward` then compares the live `stake.delegation.stake` plus the epoch-boundary-computed reward against the epoch-boundary-computed `new_stake.delegation.stake`, and panics on mismatch when `adjust_delegations_for_rent` is not active: [4](#0-3) 

The `adjust_delegations_for_rent` flag is gated by the `relax_post_exec_min_balance_check` feature: [5](#0-4) 

When that feature is inactive (which is the normal state until/unless it is activated cluster-wide), the `else` branch's `assert_eq!` is the only guard against drift between the epoch-boundary snapshot and the live value — and nothing in the stake program prevents the account owner from performing a `Split`, `Merge`, `Withdraw`, or other operation that changes `delegation.stake` on their own stake account between the epoch-boundary reward calculation and the (up to hundreds of blocks later) distribution of that specific partition. `StakesCache::check_and_store` synchronously reflects any such transaction into the live cache immediately, exactly the value later read by `build_updated_stake_reward`: [6](#0-5) 

This is structurally the same defect class as H-7: a value computed and stored at one point in time (`pos.underlyingAmount` / `new_stake.delegation.stake`) is later compared to, or substituted for, a value that should reflect subsequent real changes to the position, and no reconciliation step exists for the unprivileged, ordinary-user-driven case where the position changes in between.

### Impact Explanation
The `assert_eq!` failure is a Rust panic inside bank/block processing that runs deterministically for every validator processing the same slot with the same on-chain state (stake-account mutations are itself consensus state, replicated identically to every node). This is not an isolated, per-node crash triggered by a malicious peer — it is triggered by ordinary, permissionless owner activity on a stake account (Split/Merge/Withdraw) landing in a block during the reward-distribution window of the *same* epoch. Because every honest validator executes the identical deterministic bank logic over the identical state, this manifests as a simultaneous panic across the cluster — a consensus halt, which is within the valid impact set (false execution/rooting/acceptance, consensus halt).

### Likelihood Explanation
Reachability depends on: (1) `adjust_delegations_for_rent` (the `relax_post_exec_min_balance_check` feature) being inactive at the time — which is the default/pre-activation state for a newly introduced feature flag, as suggested by the code comments noting the flag's name intentionally doesn't match its purpose; and (2) a stake owner performing a routine Split/Merge/Withdraw on their own stake account within the multi-block distribution window of the same epoch in which they are due a reward. Both conditions are ordinary, unprivileged, and require no coordination or malicious intent — stake owners routinely split/merge/withdraw stake for delegation management, cold-storage moves, or exchange withdrawals, and the distribution window spans a meaningful fraction of an epoch. I could not fully verify from the indexed code alone whether the stake program processor for `Split`/`Merge` places any additional restriction tied to `EpochRewardStatus` (no matches were found for `epoch_reward_status` searched from the stake program side), so I cannot rule out an unindexed guard elsewhere; this should be verified directly against the stake program instruction processors before treating likelihood as fully confirmed.

### Recommendation
Do not hard-assert equality between the epoch-boundary-computed post-reward delegation and the live delegation read at distribution time. Instead, when `adjust_delegations_for_rent` is inactive, either (a) fall back to computing the reward delta relative to the *live* `delegation.stake` (i.e., `live_delegation.saturating_add(reward)`) rather than asserting against the stale snapshotted value, or (b) detect a live/snapshot mismatch and gracefully skip/adjust that entry (mirroring what the `adjust_delegations_for_rent = true` branch already does for the rent-driven case) instead of panicking.

### Proof of Concept
Conceptual reproduction (cannot be executed in this read-only environment, but derivable directly from the cited code paths):
1. At an epoch boundary, `compute_new_epoch_caches_and_rewards` snapshots stake delegations and calculates a `PartitionedStakeReward` for stake account `S` with `inflation.stake.delegation.stake = D0 + reward`.
2. Before `S`'s partition is processed at distribution time (still within the same epoch, with `adjust_delegations_for_rent` inactive), the owner of `S` submits a normal `Split` instruction that reduces `S`'s live `delegation.stake` to `D1 < D0`. `StakesCache::check_and_store` (`runtime/src/stakes.rs:620-660`) updates the cache immediately.
3. When `store_stake_accounts_in_partition`/`build_updated_stake_reward` processes `S`'s partition, it computes `expected_delegation = D1 + reward`, compares against the stored `new_stake.delegation.stake = D0 + reward` (`distribution.rs:284-294`), finds them unequal, and the `assert_eq!` panics — crashing bank processing for that slot on every validator that reaches it.

### Citations

**File:** runtime/src/bank.rs (L1762-1778)
```rust
        let stakes = self.stakes_cache.stakes();
        let stake_delegations = stakes.stake_delegations_vec();
        let (
            (
                stake_history,
                unfiltered_distribution_vote_accounts,
                delegated_stakes,
                reward_epoch_delegated_stakes,
            ),
            calculate_activated_stake_time_us,
        ) = measure_us!(stakes.calculate_activated_stake(
            self.epoch(),
            thread_pool,
            self.new_warmup_cooldown_rate_epoch(),
            &stake_delegations,
            self.use_fixed_point_stake_math(),
        ));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L744-750)
```rust
            Ok((stake_reward, commission_lamports, stake)) => {
                let inflation = InflationReward {
                    stake,
                    stake_reward,
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                };
                let (commission_pubkey, is_vote_account) = if custom_commission_collector {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L269-294)
```rust
        let mut new_stake = partitioned_stake_reward.inflation.stake;
        if adjust_delegations_for_rent {
            let minimum_balance = rent.minimum_balance(account.data().len());
            // The rewarded epoch is right before the distribution epoch
            let rewarded_epoch = distribution_epoch.saturating_sub(1);
            // The entry in `partitioned_stake_reward` contains the rewards,
            // calculated during the calculation phase
            let delegation_with_rewards = new_stake.delegation.stake;
            adjust_delegation_for_rent(
                &mut new_stake.delegation,
                rewarded_epoch,
                delegation_with_rewards,
                account.lamports(),
                minimum_balance,
            );
        } else {
            let expected_delegation = stake
                .delegation
                .stake
                .saturating_add(partitioned_stake_reward.inflation.stake_reward);
            assert_eq!(
                expected_delegation, new_stake.delegation.stake,
                "stake reward delegation must be consistent with the updated stake account \
                 lamport balance"
            );
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L341-345)
```rust
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L360-366)
```rust
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
        let stakes_cache = self.stakes_cache.stakes();
        let stakes_cache_accounts = stakes_cache.stake_delegations();
        let stake_history = stakes_cache.history();
        let new_warmup_cooldown_rate_epoch = self.new_warmup_cooldown_rate_epoch();
        let rent = &self.rent_collector.rent;
        for index in indices {
```

**File:** runtime/src/stakes.rs (L620-660)
```rust
    fn upsert_stake_delegation(
        &mut self,
        stake_pubkey: Pubkey,
        stake_account: StakeAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        debug_assert_ne!(stake_account.lamports(), 0u64);
        let delegation = stake_account.delegation();
        let voter_pubkey = delegation.voter_pubkey;
        let stake = delegation_effective_stake(
            delegation,
            self.epoch,
            &self.stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        match self.stake_delegations.insert(stake_pubkey, stake_account) {
            None => {
                self.add_delegated_stake(voter_pubkey, stake);
                self.vote_accounts.add_stake(&voter_pubkey, stake);
            }
            Some(old_stake_account) => {
                let old_delegation = old_stake_account.delegation();
                let old_voter_pubkey = old_delegation.voter_pubkey;
                let old_stake = delegation_effective_stake(
                    old_delegation,
                    self.epoch,
                    &self.stake_history,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
                if voter_pubkey != old_voter_pubkey || stake != old_stake {
                    self.sub_delegated_stake(&old_voter_pubkey, old_stake);
                    self.add_delegated_stake(voter_pubkey, stake);
                    self.vote_accounts.sub_stake(&old_voter_pubkey, old_stake);
                    self.vote_accounts.add_stake(&voter_pubkey, stake);
                }
            }
        }
    }
```
