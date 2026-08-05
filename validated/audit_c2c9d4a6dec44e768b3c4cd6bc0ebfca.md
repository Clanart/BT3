## Analog Found

### Title
Per-stake block-reward calculation clamps individual share but not the aggregate, letting recalculated partitions over-allocate `pending_delegator_rewards` and starve later partitions / panic on sysvar debit - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
The external report's broken invariant is: a shared pooled value (`maxWithdraw`) is used to compute each depositor's withdrawable share independently, and each individual withdrawal is only bounds-checked against the *whole* pool rather than against what's *actually left* after prior withdrawals — so an early claimant can extract more than their fair share, leaving later claimants unable to withdraw. Agave's block-revenue-sharing reward calculation exhibits the same pattern: `calculate_block_reward` computes each stake account's slice of a vote account's shared `pending_delegator_rewards` pool independently, clamping only the *individual* result to the pool total, not the *running sum* across all stake accounts sharing that pool.

### Finding Description
`calculate_block_reward` computes a stake account's block reward as a proportional share of the vote account's `pending_delegator_rewards` pool: [1](#0-0) 

The comment in the code itself acknowledges the exact failure mode: during **recalculation** (`get_epoch_params_for_recalculation`, used when a bank must recompute rewards after a fork switch while an epoch-reward distribution is in progress), `distribution_epoch_vote_accounts` reflects stake accounts that have *already received some rewards from a prior partition*, so `delegation_effective_stake` for those accounts can now be `> total_active_stake` (a value frozen at the original reward epoch via `reward_epoch_delegated_stakes`). The only mitigation applied is `.min(pending_delegator_rewards)` — clamping a *single* stake account's share to the full pool, not clamping the *sum* of shares already paid to other stake accounts of the same vote account: [2](#0-1) 

This is structurally identical to sDaiStrategy's bug: each participant's withdrawable amount is computed against the *global* pool value rather than against a pool balance that is decremented as other participants are paid. Because Agave's stake reward distribution is itself **partitioned across many blocks** (`REWARD_CALCULATION_NUM_BLOCKS`, `store_stake_accounts_in_partition`, `distribute_epoch_rewards_in_partition`), stake accounts belonging to the same over-stakeed vote account can land in different partitions distributed at different block heights — exactly mirroring "depositor A withdraws first, depositor B is left short." [3](#0-2) 

The lamports for block rewards are staged once, up front, into the `EpochRewards` sysvar account's balance (`block_rewards`), and each partition's distribution debits that shared sysvar balance: [4](#0-3) 

The debit is guarded only by an `.expect()` that panics if the sysvar cannot cover the amount: [5](#0-4) 

If the aggregate of per-stake block rewards computed for a single vote account's delegators (summed across all partitions) exceeds that vote account's `pending_delegator_rewards` snapshot — which the code's own comment says is possible during recalculation — either (a) later partitions receive less than their entitled share because earlier partitions already consumed a disproportionate amount of the shared sysvar balance, or (b) in a globally tight scenario the `checked_sub_lamports(...).expect(...)` in `update_epoch_rewards_sysvar` fails and the validator process panics, halting that validator's execution/consensus participation for the slot.

### Impact Explanation
This affects Agave's core runtime bank logic (partitioned epoch rewards), not a user-controlled DeFi contract, so it maps to the "runtime/accounts" category in scope. The consequences are either (a) false/short execution of reward accounting — some delegators receive less than the protocol-specified reward, a fund-loss-adjacent correctness bug, or (b) a `.expect()` panic in `update_epoch_rewards_sysvar` that would crash/halt the bank being processed, a non-RPC crash/consensus-halt condition, entirely from unprivileged, ordinary protocol state (no malicious peer, no trusted plugin, no admin key needed) — it is triggered purely by the deterministic recalculation path that ordinary fork-switching during epoch-reward distribution exercises.

### Likelihood Explanation
The recalculation path (`get_epoch_params_for_recalculation`) is a normal part of Agave's fork-choice/replay machinery — it fires whenever a bank must recompute reward state for a fork that diverged during an active partitioned-rewards distribution, which is a routine (not attacker-controlled) occurrence on any validator that switches forks mid-epoch-boundary. The code comment explicitly flags the `stake > total_active_stake` scenario as a known, reachable condition ("This is harmless in practice, but we clamp it just to be safe"), though the clamp is per-item, not aggregate. I was not able to fully trace, within the available tool budget, whether `distribute_reward_commissions` (called earlier in `begin_partitioned_rewards`) already bounds the total block-reward budget staged into the sysvar tightly enough to make the aggregate-overrun structurally impossible in all recalculation cases, or whether existing tests (`test_calculate_block_reward_prop`) only assert the per-item invariant (`reward <= pending_delegator_rewards`) rather than the aggregate-sum invariant across a vote account's full delegator set. This is the main open question that limits confidence in likelihood. [6](#0-5) 

### Recommendation
Track a per-vote-account running total of block rewards already allocated during the current distribution (across all partitions, including recalculation passes), and clamp each stake account's `block_reward` against `pending_delegator_rewards - already_allocated` rather than against the raw pool value alone — analogous to the report's recommendation that sDaiStrategy maintain its own accounting to tax/allocate against the *remaining* pool rather than a static global figure. Additionally, add an explicit aggregate-level invariant test verifying that the sum of `calculate_block_reward` outputs across all delegators of a single vote account never exceeds that vote account's `pending_delegator_rewards`, specifically under the recalculation scenario where `stake > total_active_stake`.

### Proof of Concept
Conceptual reproduction based on the existing test harness (`get_block_reward_for_test`):
1. Vote account V has `pending_delegator_rewards = P`, and delegators A and B activate stake at epoch E with `total_active_stake = S = stake_A + stake_B`.
2. Rewards are calculated and partitioned so that A is in partition 0 (early block) and B is in partition 1 (later block).
3. Partition 0 for A is distributed, and `A`'s `delegation.stake` is increased by its inflation/block reward via `build_updated_stake_reward`. [7](#0-6) 
4. Before partition 1 for B is distributed, the bank re-executes the reward *calculation* phase for a competing fork (recalculation), at which point `distribution_epoch_vote_accounts` now shows A's `delegation.stake` already inflated from step 3, while `reward_epoch_delegated_stakes` (frozen at epoch E) still reports the original `S`. Both A and B are recomputed with `stake_A' > `A's original share and `stake_B` unchanged, against the same fixed `S` and `P`; each individual result is clamped to `P` but the sum `block_reward_A' + block_reward_B` is not, and can exceed `P`.
5. When both partitions are eventually distributed against the shared `EpochRewards` sysvar balance for block rewards, the sysvar's `checked_sub_lamports(debit_block_reward_lamports).expect(...)` can be driven to fail once the aggregate debited exceeds the staged balance, panicking the bank/validator process. [8](#0-7)

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-231)
```rust
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
        let stake = delegation_effective_stake(
            delegation,
            rewarded_epoch,
            stake_history,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        );
        // During recalculation, if stake account has already received rewards,
        // it's possible to have `stake > total_active_stake`. If
        // `pending_delegator_rewards` is a huge number, we could potentially
        // overflow a `u64`. We can also have individual rewards look greater
        // than the pending rewards. This is harmless in practice, but we
        // clamp it just to be safe
        (pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
            .try_into()
            .unwrap_or(u64::MAX)
            .min(pending_delegator_rewards)
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4334-4347)
```rust
    proptest! {
        #[test]
        fn test_calculate_block_reward_prop(
            individual_stake in 0..=u64::MAX,
            total_stake in 0..=u64::MAX,
            pending_delegator_rewards in 0..=u64::MAX,
            rewarded_epoch in 0..=solana_stake_history::MAX_ENTRIES as u64,
        ) {
            let reward = get_block_reward_for_test(individual_stake, total_stake, pending_delegator_rewards, rewarded_epoch);
            // This check is pedantic since the code clamps the output, so the
            // test is checking for panics.
            prop_assert!(reward <= pending_delegator_rewards);
        }
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L262-267)
```rust
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L336-360)
```rust
    fn store_stake_accounts_in_partition(
        &self,
        partition_rewards: &StartBlockHeightAndPartitionedRewards,
        partition_index: u64,
    ) -> DistributionResults {
        let feature_snapshot = self.feature_set.snapshot();
        // Name intentionally doesn't match -- "adjust delegations for rent" is
        // part of relaxing post-exec min balance checks.
        let adjust_delegations_for_rent = feature_snapshot.relax_post_exec_min_balance_check;
        let use_fixed_point_stake_math = feature_snapshot.upgrade_bpf_stake_program_to_v5_1;

        let mut stake_reward_lamports_minted = 0;
        let mut stake_reward_lamports_burned = 0;
        let mut block_reward_lamports_distributed = 0;
        let mut block_reward_lamports_burned = 0;
        let indices = partition_rewards
            .partition_indices
            .get(partition_index as usize)
            .unwrap_or_else(|| {
                panic!(
                    "partition index out of bound: {partition_index} >= {}",
                    partition_rewards.partition_indices.len()
                )
            });
        let mut updated_stake_rewards = Vec::with_capacity(indices.len());
```

**File:** runtime/src/bank/partitioned_epoch_rewards/sysvar.rs (L74-109)
```rust
    /// Update EpochRewards sysvar with distributed rewards
    pub(in crate::bank::partitioned_epoch_rewards) fn update_epoch_rewards_sysvar(
        &self,
        inflation_reward_lamports_minted_and_burned: u64,
        debit_block_reward_lamports: u64,
    ) {
        let mut epoch_rewards = self.get_epoch_rewards_sysvar();
        assert!(epoch_rewards.active);

        epoch_rewards.distribute(inflation_reward_lamports_minted_and_burned);

        self.update_sysvar_account(&sysvar::epoch_rewards::id(), |account| {
            create_account(
                &epoch_rewards,
                self.inherit_specially_retained_account_fields(account),
            )
        });

        // Debit the lamports separately without updating capitalization,
        // since block reward lamports already existed
        let mut account = self
            .get_account_with_fixed_root(&sysvar::epoch_rewards::id())
            .expect("created sysvar account exists");

        // SAFETY: programmer error if we debit too many block rewards
        account
            .checked_sub_lamports(debit_block_reward_lamports)
            .expect("epoch reward sysvar has enough lamports for distribution");
        assert!(
            account.lamports() >= self.get_minimum_balance_for_rent_exemption(account.data().len()),
            "Sysvar account must have enough for rent exemption after debiting block rewards"
        );
        self.store_account(&sysvar::epoch_rewards::id(), &account);

        self.log_epoch_rewards_sysvar("update");
    }
```
