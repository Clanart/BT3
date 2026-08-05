## Title
Unbounded, unpartitioned iteration over all stake delegations at every epoch boundary can stall block production - ([File: runtime/src/stakes.rs], [File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs])

### Summary
The report describes a SKALE bug class: an unprivileged actor can inflate a global collection (slashes) that every account update must iterate over in full, risking a block-gas-limit halt; the project later mitigated this by aggregating/partitioning the work. Agave has the structurally identical pattern at the reward/epoch-boundary layer: every epoch transition forces the bank to iterate serially/in-parallel over the **entire** set of stake delegations network-wide — a collection that is permissionlessly, cheaply, and unboundedly grow-able (any account can create a stake account for the price of rent-exemption). Unlike the *distribution* of rewards, which Agave already partitions across many blocks (`begin_partitioned_rewards`/`distribute_partitioned_epoch_rewards`) specifically to avoid exactly this class of bug, the *calculation* phase that runs once, synchronously, at the epoch boundary is not partitioned.

### Finding Description
At the start of every new epoch, `Bank::process_new_epoch` calls `compute_new_epoch_caches_and_rewards`, which collects **all** stake delegations via `stakes.stake_delegations_vec()` and passes them through:
- `Stakes::calculate_activated_stake` [1](#0-0) , which does a `par_iter().fold().reduce()` over every stake delegation in the network to compute stake-history/activation and delegated stakes, and
- `calculate_reward_points_partitioned` and `calculate_stake_rewards_and_commissions` [2](#0-1) [3](#0-2) , which iterate over the **same full, unfiltered `stake_delegations` vector** to compute reward points and stake rewards for every single delegation.

Crucially, Agave already recognized and partially fixed this exact bug class for vote accounts: `clone_and_filter_for_vat` caps the number of *vote accounts* considered in Alpenglow reward calc at `MAX_ALPENGLOW_VOTE_ACCOUNTS` per SIMD-0357 [4](#0-3) , and reward *distribution* (writing updated stake accounts back to the ledger) is explicitly partitioned across multiple blocks via `store_stake_accounts_in_partition` for the same stated reason ("For N stake delegations, where N is >1,000,000...") [5](#0-4) .

However, no equivalent cap or partitioning exists for the number of **stake delegations** processed in `calculate_activated_stake`, `calculate_reward_points_partitioned`, and `calculate_stake_rewards_and_commissions` — these still run over the complete, unbounded stake-delegation set in a single synchronous call at the epoch boundary, exactly mirroring the SKALE report's core complaint: "a huge number of items forces an all-or-nothing pass" and "there are two separate pipelines for iterating," one of which (calculation) was never merged/partitioned even after the other (distribution) was.

### Impact Explanation
Creating a stake account and delegating a minimal amount is cheap and fully unprivileged — it only costs rent-exemption lamports (get_minimum_delegation floor is on the order of 1 SOL per `runtime/src/stake_utils.rs`, and rent-exempt minimum for account creation is a few thousand lamports). An attacker (or organic growth) could create a very large number of small stake accounts. Because `calculate_activated_stake` and the reward-points/reward-calculation pass iterate the full delegation list synchronously at every epoch boundary for every validator on the network, growth of this collection directly and deterministically increases epoch-boundary processing time for all validators simultaneously. If this processing time grows to exceed the time budget available before the next slot must be produced, it can delay or stall block production network-wide at every epoch boundary — a non-RPC, unprivileged-triggered availability/consensus-halt risk, not merely a localized RPC slowdown. This is the direct analog of the SKALE report's "block gas limit halt" concern, translated to Agave's per-epoch compute budget instead of a per-transaction gas budget.

### Likelihood Explanation
Likelihood is moderate: stake account creation is permissionless and inexpensive, and the affected code paths are unconditionally executed once per epoch for every validator (not opt-in, not filtered). The mitigating factors are (1) the calculation is parallelized with rayon across all cores, which raises the practical delegation count needed to cause a noticeable stall, and (2) growing the stake delegation set to network-disruptive size requires locking up capital across many accounts, which is a real but non-trivial cost barrier compared to SKALE's zero-cost "signal" spam. Because the same team already treated an analogous vote-account count explosion as serious enough to warrant a hard cap (`MAX_ALPENGLOW_VOTE_ACCOUNTS`) and treated stake-reward *distribution* as serious enough to warrant partitioning, the underlying calculation path being left uncapped and unpartitioned is a genuine, currently-unaddressed gap rather than a purely theoretical concern.

### Recommendation
Apply the same two remedies the SKALE report recommended and that Agave already applies elsewhere in this exact code path:
1. Bound the number of stake delegations considered in `calculate_activated_stake`/`calculate_reward_points_partitioned`/`calculate_stake_rewards_and_commissions`, analogous to the `MAX_ALPENGLOW_VOTE_ACCOUNTS` cap already applied to vote accounts via `clone_and_filter_for_vat`.
2. Alternatively/additionally, partition or incrementally schedule the *calculation* pass the same way the *distribution* pass is already partitioned across blocks, so a single epoch boundary never has to process an unbounded delegation set synchronously.

### Proof of Concept
Not independently reproducible from static analysis alone — verifying an actual stall requires benchmarking `calculate_activated_stake` / `calculate_stake_rewards_and_commissions` wall-clock time against slot-time budgets for very large (e.g., multi-million) synthetic stake-delegation counts on real validator hardware, which is outside the scope of local code inspection. The code-level evidence establishing the unbounded, unpartitioned iteration is cited above from `runtime/src/stakes.rs` and `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`.

### Citations

**File:** runtime/src/stakes.rs (L434-466)
```rust
    pub(crate) fn calculate_activated_stake(
        &self,
        next_epoch: Epoch,
        thread_pool: &ThreadPool,
        new_rate_activation_epoch: Option<Epoch>,
        stake_delegations: &[(&Pubkey, &StakeAccount)],
        use_fixed_point_stake_math: bool,
    ) -> (
        StakeHistory,
        VoteAccounts,
        DelegatedStakes,
        RewardEpochDelegatedStakes,
    ) {
        // Wrap up the prev epoch by adding new stake history entry for the
        // prev epoch.
        let (stake_history_entry, effective_delegated_stakes) = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .fold(
                    || (StakeActivationStatus::default(), HashMap::default()),
                    |(acc, mut delegated_stakes), (_stake_pubkey, stake_account)| {
                        let delegation = stake_account.delegation();
                        let activation_status = delegation_activation_status(
                            delegation,
                            self.epoch,
                            &self.stake_history,
                            new_rate_activation_epoch,
                            use_fixed_point_stake_math,
                        );
                        *delegated_stakes.entry(delegation.voter_pubkey).or_default() +=
                            activation_status.effective;
                        (acc + activation_status, delegated_stakes)
                    },
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L803-820)
```rust
        let mut measure_redeem_rewards = Measure::start("redeem-rewards");
        // For N stake delegations, where N is >1,000,000, we produce:
        // * N stake rewards,
        // * M reward commission accounts, where M is a number of stake nodes.
        //   Currently, way smaller number than 1,000,000. And we can expect it
        //   to always be significantly smaller than number of delegations.
        //
        // Producing the stake reward with rayon triggers a lot of
        // (re)allocations. To avoid that, we allocate it at the start and
        // pass `stake_rewards.spare_capacity_mut()` as one of iterators.
        let stake_delegations_len = stake_delegations.len();
        let mut stake_rewards = PartitionedStakeRewards::with_capacity(stake_delegations_len);
        let rewards_accumulator: RewardsAccumulator = thread_pool.install(|| {
            stake_delegations
                .par_iter()
                .zip(&mut stake_rewards.spare_capacity_mut()[..stake_delegations_len])
                .with_min_len(500)
                .filter_map(|((stake_pubkey, stake_account), reward_ref)| {
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L978-1002)
```rust
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
