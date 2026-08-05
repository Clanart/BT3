## Title
Unbounded per-epoch stake-delegation reward calculation can inflate epoch-boundary slot processing time - ([File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

## Summary
The Canto report describes an admin-triggered function (`setPeriodSize`) that iterates over an attacker-inflatable, unbounded array (`allPairs`), so it can be permanently DoS'd once the array grows past the block gas limit. The Agave analog is the epoch-boundary rewards calculation, which iterates over the *entire* set of stake delegations known to the runtime (`Stakes::stake_delegations_vec()`), with no upper bound on the number of stake accounts an unprivileged user can create via the stake program. Unlike vote accounts, which are hard-capped by `MAX_ALPENGLOW_VOTE_ACCOUNTS` [1](#0-0)  and the VAT-filtering logic [2](#0-1) , there is no analogous cap on the number of stake delegations processed by `calculate_rewards_for_partitioning`.

## Finding Description
At every epoch boundary, `process_new_epoch` calls `compute_new_epoch_caches_and_rewards`, which pulls the full list of stake delegations from the stakes cache and passes it, unbounded, into the reward-calculation pipeline: [3](#0-2) 

That vector, `stake_delegations`, is sized by however many delegated stake accounts currently exist network-wide, with no truncation or filtering analogous to `clone_and_filter_for_vat` for vote accounts. It flows into `calculate_reward_points_partitioned`, which does a `par_iter().map(...).sum()` over every delegation [4](#0-3) , and into `calculate_stake_rewards_and_commissions`, which similarly runs `redeem_delegation_rewards` for every stake delegation via `par_iter()` [5](#0-4) . The code itself acknowledges the scale concern: [6](#0-5) 

Crucially, this *calculation* phase runs synchronously as part of a single bank's `process_new_epoch` — it is not partitioned across multiple slots. Only the subsequent *distribution* phase (crediting the already-computed rewards to stake accounts) is explicitly chunked across blocks via `get_reward_distribution_num_blocks`, which caps the credit interval to 10% of the epoch's slots [7](#0-6) . No equivalent partitioning exists for the calculation step that must complete inside the single epoch-boundary bank.

Stake accounts are created permissionlessly by any user via the stake program at the cost of the account's rent-exemption minimum (a few thousand lamports for a ~200 byte account), with no protocol-level cap on the total count of delegations that can exist. This mirrors the Canto bug's "unbounded loop that depends on storage values populated by unprivileged callers" pattern — `allPairs`/`createPair` there maps to the stake-delegations set/stake-account creation here, and `setPeriodSize`'s full-array loop maps to `calculate_reward_points_partitioned`/`calculate_stake_rewards_and_commissions`.

## Impact Explanation
Rather than causing a discrete reverting transaction (Solana's rewards calculation is not a fee-metered transaction and cannot simply "fail" the way `setPeriodSize` reverts), an inflated stake-delegation set increases the wall-clock cost of the single epoch-boundary bank's freeze/hash computation across *every* validator simultaneously (since all validators must independently and deterministically compute the same rewards to agree on the resulting bank hash). If the number of active delegations grows large enough that this synchronous, non-partitioned pass exceeds the available epoch-boundary processing budget, it can degrade block timing at every epoch transition network-wide, which falls under the accepted "non-RPC remote exhaustion / consensus degradation" impact category, since the condition is triggered purely by an unprivileged, permissionless action (creating stake accounts) with no malicious validator/admin assumption required.

## Likelihood Explanation
Likelihood is constrained by the real-world cost of the primitive: creating N stake accounts costs N times the stake account's rent-exemption minimum (plus whatever minimum delegation size the stake program enforces, though this repo snapshot did not let me confirm a specific minimum-delegation-size feature gate's exact enforced value from the index alone). This is directly analogous to the judged rationale in the Canto report itself — "this would cost an infinite amount of Canto if orchestrated by a single user" — which is why that report was kept at Medium rather than High. The Agave analog inherits the same economic mitigant: it is technically unbounded in the code, but practically bounded by the linear cost of account creation, and further bounded by rayon parallelism reducing wall-clock cost per delegation. I could not verify from the index alone what the exact current minimum-delegation-size enforcement is, or what production-scale delegation counts look like, which limits precise quantification of how close current mainnet-scale accounts are to a problematic threshold.

## Recommendation
Cap or bound the number of stake delegations considered during the synchronous reward-calculation phase the same way vote accounts are bounded via `clone_and_filter_for_vat`/`MAX_ALPENGLOW_VOTE_ACCOUNTS`, or partition the calculation phase (not just the distribution phase) across multiple blocks so that no single bank's freeze is responsible for an unbounded amount of work. Alternatively, enforce/raise the protocol-level minimum stake delegation size so that the maximum number of concurrently active delegations is bounded by total network stake divided by minimum delegation, giving a hard ceiling on `stake_delegations.len()`.

## Proof of Concept
Not executable from static analysis alone. Conceptually: an attacker repeatedly creates new stake accounts (each funded at the rent-exemption minimum) and delegates minimal stake to any vote account, growing `Stakes::stake_delegations_vec()` without protocol-enforced bound. At the next epoch boundary, every validator's `process_new_epoch` → `compute_new_epoch_caches_and_rewards` → `calculate_rewards_for_partitioning` must iterate the full, inflated delegation set synchronously within that epoch-boundary bank, as shown at [3](#0-2)  and [5](#0-4) , without the block-spanning partitioning that protects the later distribution phase [7](#0-6) .

### Citations

**File:** runtime/src/alpenglow_epoch_type.rs (L34-42)
```rust
/// The off-curve account where we store the bounded reward-epoch delegated stake
/// denominators used for non-Tower reward recalculation after snapshot restore.
static REWARD_EPOCH_DELEGATED_STAKES_ACCOUNT: LazyLock<Pubkey> = LazyLock::new(|| {
    let (pubkey, _) = Pubkey::find_program_address(
        &[b"reward_epoch_delegated_stakes"],
        &agave_feature_set::alpenglow::id(),
    );
    pubkey
});
```

**File:** vote/src/vote_account.rs (L212-244)
```rust
    pub fn clone_and_filter_for_vat(
        &self,
        max_vote_accounts: usize,
        minimum_vote_account_balance: u64,
    ) -> VoteAccounts {
        assert!(max_vote_accounts > 0, "max_vote_accounts must be > 0");
        let capacity = max_vote_accounts.min(self.vote_accounts.len());
        let mut entries_to_sort: Vec<(&Pubkey, &VoteAccount, u64)> = Vec::with_capacity(capacity);
        for (pubkey, (stake, vote_account)) in self.vote_accounts.iter() {
            let has_bls = vote_account
                .vote_state_view()
                .bls_pubkey_compressed()
                .is_some();
            let has_stake = *stake != 0u64;
            let has_balance = vote_account.lamports() >= minimum_vote_account_balance;

            if !has_bls || !has_stake || !has_balance {
                continue;
            }
            entries_to_sort.push((pubkey, vote_account, *stake));
        }

        let valid_len = entries_to_sort.len();
        if entries_to_sort.len() > max_vote_accounts {
            // Find the cutoff stake using partial sort (more efficient than full sort).
            let (_, cutoff_entry, _) =
                entries_to_sort.select_nth_unstable_by(max_vote_accounts, |a, b| b.2.cmp(&a.2));
            let floor_stake = cutoff_entry.2;

            // Per SIMD 357, we remove all vote accounts with stake smaller or equal to
            // the first truncated one.
            entries_to_sort.retain(|(_, _, stake)| *stake > floor_stake);
        }
```

**File:** runtime/src/bank.rs (L1762-1803)
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
        debug_assert_eq!(reward_epoch_delegated_stakes.epoch, rewarded_epoch);

        // Apply stake rewards and commission using the VAT-filtered distribution
        // vote-account snapshot.
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
        }
        let cached_vote_accounts =
            self.get_cached_vote_accounts(rewarded_epoch, &filtered_distribution_vote_accounts);
        let (rewards_calculation, update_rewards_with_thread_pool_time_us) =
            measure_us!(self.calculate_rewards(
                &stake_history,
                stake_delegations,
                cached_vote_accounts,
                rewarded_epoch,
                reward_epoch_delegated_stakes,
                reward_calc_tracer,
                thread_pool,
                rewards_metrics,
            ));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L804-812)
```rust
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
        //
        // Producing the stake reward with rayon triggers a lot of
        // (re)allocations. To avoid that, we allocate it at the start and
        // pass `stake_rewards.spare_capacity_mut()` as one of iterators.
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L813-850)
```rust
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
                    let block_reward = if block_revenue_sharing {
                        calculate_block_reward(
                            rewarded_epoch,
                            stake_account.delegation(),
                            stake_history,
                            cached_vote_accounts.distribution_epoch_vote_accounts,
                            ag_epoch_type,
                            new_warmup_cooldown_rate_epoch,
                            use_fixed_point_stake_math,
                        )
                    } else {
                        0
                    };
                    let maybe_reward_record = self.redeem_delegation_rewards(
                        rewarded_epoch,
                        stake_pubkey,
                        stake_account,
                        &point_value,
                        stake_history,
                        &cached_vote_accounts,
                        reward_calc_tracer.as_ref(),
                        new_warmup_cooldown_rate_epoch,
                        delay_commission_updates,
                        commission_rate_in_basis_points,
                        adjust_delegations_for_rent,
                        ag_epoch_type,
                        custom_commission_collector,
                        use_fixed_point_stake_math,
                    );

```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L977-1002)
```rust
        let use_fixed_point_stake_math = self.use_fixed_point_stake_math();
        let (points, measure_us) = measure_us!(thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .map(|(_stake_pubkey, stake_account)| {
                    let vote_pubkey = stake_account.delegation().voter_pubkey;

                    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey)
                    else {
                        return 0;
                    };
                    if vote_account.owner() != &solana_vote_program {
                        return 0;
                    }

                    calculate_points_for_tower(
                        stake_account.stake_state(),
                        DelegatedVoteState::from(vote_account.vote_state_view()),
                        stake_history,
                        new_warmup_cooldown_rate_epoch,
                        use_fixed_point_stake_math,
                    )
                    .unwrap_or(0)
                })
                .sum::<u128>()
        }));
```

**File:** runtime/src/bank/partitioned_epoch_rewards/mod.rs (L408-428)
```rust
    /// Calculate the number of blocks required to distribute rewards to all stake accounts.
    pub(super) fn get_reward_distribution_num_blocks(
        &self,
        rewards: &PartitionedStakeRewards,
    ) -> u64 {
        let total_stake_accounts = rewards.num_rewards();
        if self.epoch_schedule.warmup && self.epoch < self.first_normal_epoch() {
            1
        } else {
            const MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH: u64 = 10;
            let num_chunks = total_stake_accounts
                .div_ceil(self.partitioned_rewards_stake_account_stores_per_block() as usize)
                as u64;

            // Limit the reward credit interval to 10% of the total number of slots in a epoch
            num_chunks.clamp(
                1,
                (self.epoch_schedule.slots_per_epoch / MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH).max(1),
            )
        }
    }
```
