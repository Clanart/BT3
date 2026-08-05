### Title
Unbounded, unmetered iteration over all stake delegations at every epoch boundary allows permissionless degradation/halt of epoch-transition processing - (`runtime/src/stakes.rs`, `runtime/src/bank.rs`)

### Summary
Analogous to the Cosmos SDK bug where an unmetered `GetAllBalances` scan over an attacker-inflatable reward-pool balance ran in `BeginBlock`, Agave's epoch-boundary transition runs `Stakes::calculate_activated_stake`, which iterates over **every** stake delegation in the validator's `StakesCache` with no size cap and no compute-budget/gas accounting. This routine is invoked unconditionally for every bank that crosses an epoch boundary — the Agave analog of `BeginBlock` — via `Bank::process_new_epoch` → `Bank::compute_new_epoch_caches_and_rewards` → `Stakes::calculate_activated_stake`. Unlike the vote-account set (which the codebase explicitly caps at `MAX_ALPENGLOW_VOTE_ACCOUNTS` via `clone_and_filter_for_vat` before it is used in reward calculation and in the `RewardEpochDelegatedStakesAccount`), the stake-delegation set has no equivalent bound anywhere in the pipeline.

### Finding Description
`Stakes::calculate_activated_stake` (`runtime/src/stakes.rs:434-502`) is passed the *entire* `stake_delegations` vector (collected via `stake_delegations_vec()`, `runtime/src/stakes.rs:689-691`) and does a `par_iter().fold()/reduce()` pass over it to compute activation status and refreshed vote-account delegated stakes: [1](#0-0) 

This is called from `Bank::compute_new_epoch_caches_and_rewards`: [2](#0-1) 

which is invoked unconditionally from `Bank::process_new_epoch` (`runtime/src/bank.rs:1816-1846`), the function that runs whenever a bank crosses an epoch boundary — this is Agave's structural equivalent of a chain's `BeginBlock`: it is mandatory, runs on every validator that processes that slot, and is **not** metered by the transaction compute-budget mechanism (`Bank::compute_budget`), because it isn't part of any transaction's execution.

Critically, the codebase already recognizes this exact class of risk for the *vote-account* set feeding Alpenglow reward computation, and mitigates it there: `unfiltered_distribution_vote_accounts` is explicitly truncated to `MAX_ALPENGLOW_VOTE_ACCOUNTS` before use: [3](#0-2) 

and this bound is asserted/enforced when persisting `RewardEpochDelegatedStakesAccount`: [4](#0-3) 

No equivalent bound exists for `stake_delegations` itself. The comment in `runtime/src/stakes.rs:662-675` even flags that the underlying HAMT structure has "poor" iteration performance, and the maintainers' own epoch-turnover benchmark exercises up to `1_000_000` stake accounts (`runtime/benches/epoch_turnover.rs:37`), acknowledging that this scan scales with the total number of delegations that exist on-chain — yet nothing prevents that number from growing without bound.

Creating a stake delegation only requires locking a rent-exempt reserve plus the minimum delegation amount in a `StakeStateV2::Stake` account (`runtime/src/stake_utils.rs:19-27`, `runtime/src/genesis_utils.rs:606-632`). This capital is not spent — it is recoverable (deactivate + withdraw) — so, just like the reported spam-token deposits (which cost the attacker nothing of lasting value), an attacker can cheaply and repeatedly mint many stake accounts whose only effect on the network is to inflate the size of the unmetered epoch-boundary scan performed by *every* validator, every epoch, indefinitely — the accounts don't even need to remain delegated by the time of the next epoch to have already been counted in that epoch's `calculate_activated_stake` pass, since account creation itself is what grows the `stake_delegations` map that every future epoch boundary must traverse.

### Impact Explanation
`process_new_epoch` is on the critical path for every validator producing/replaying the first slot of a new epoch. If the number of delegations grows large enough that `calculate_activated_stake` (plus the analogous full-set stake-reward calculation in `calculate_stake_rewards_and_commissions`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:780-904`) takes longer than the time budget for producing/replaying that slot, this can cause validators to miss slot deadlines at epoch boundaries network-wide — a consensus-relevant, non-RPC, remote-triggerable degradation/halt condition, matching the reported bug class (permissionless, unmetered, attacker-triggered halt via cheaply grown attacker-controlled state).

### Likelihood Explanation
Medium-to-high in principle: the mitigation already present for vote accounts (`MAX_ALPENGLOW_VOTE_ACCOUNTS` clamp) demonstrates the maintainers are aware unbounded account-count growth in reward-related epoch processing is a real risk, but the fix was applied narrowly to the vote-account path feeding Alpenglow VAT/rewards and not to the underlying `stake_delegations` set traversed in `calculate_activated_stake`/`calculate_stake_rewards_and_commissions`. The attack requires no privileged access — anyone with enough SOL to cover (recoverable) rent-exempt reserves per stake account can create arbitrarily many delegations over time.

### Recommendation
Apply the same bounding strategy already used for vote accounts to the stake-delegation set consumed by `calculate_activated_stake` and the reward-calculation path: cap the number of stake delegations processed per epoch (e.g., by minimum effective stake, similar to `clone_and_filter_for_vat`), or introduce a protocol-level minimum-delegation/rent-exempt threshold high enough to make large-scale delegation-count inflation economically infeasible, and/or amortize this scan across multiple slots (as is already done for reward *distribution*) rather than performing it fully at the epoch boundary.

### Proof of Concept
Conceptually mirrors the reported Cosmos PoC: instead of sending many spam-denom coins to a reward pool address across several blocks and then triggering an unmetered `GetAllBalances` scan in `BeginBlock`, an attacker would:
1. Repeatedly submit `create_account`/`delegate_stake` transactions (`cli/src/stake.rs:1392+`, `stake_instruction::create_account_and_delegate_stake`) to mint a very large number of `StakeStateV2::Stake` accounts, each only requiring the (recoverable) rent-exempt reserve and minimum delegation (`runtime/src/stake_utils.rs:19-27`).
2. Wait for an epoch boundary; every validator's `Bank::process_new_epoch` → `compute_new_epoch_caches_and_rewards` → `Stakes::calculate_activated_stake` (`runtime/src/stakes.rs:434-502`) must then scan the entire inflated `stake_delegations` set, uncapped and outside of any compute-budget accounting, unlike the explicitly bounded vote-account path.

This would need to be validated at scale (e.g., extending the existing `runtime/benches/epoch_turnover.rs` benchmark, which already tests up to 1,000,000 stake accounts) in a Devin session to measure the actual wall-clock cost imposed on epoch-boundary processing relative to slot time, since I cannot execute benchmarks from this environment.

### Citations

**File:** runtime/src/stakes.rs (L434-478)
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
                )
                .reduce(
                    || (StakeActivationStatus::default(), HashMap::default()),
                    |(activation_status_a, delegated_stakes_a),
                     (activation_status_b, delegated_stakes_b)| {
                        (
                            activation_status_a + activation_status_b,
                            merge_delegated_stakes(delegated_stakes_a, delegated_stakes_b),
                        )
                    },
                )
        });
```

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

**File:** runtime/src/bank.rs (L1783-1790)
```rust
        let filtered_distribution_vote_accounts = unfiltered_distribution_vote_accounts
            .clone_and_filter_for_vat(
                MAX_ALPENGLOW_VOTE_ACCOUNTS,
                self.minimum_vote_account_balance_for_vat(),
            );
        if AlpenglowEpochType::is_alpenglow_or_migration_epoch(self, rewarded_epoch) {
            reward_epoch_delegated_stakes.set(self, &filtered_distribution_vote_accounts);
        }
```

**File:** runtime/src/alpenglow_epoch_type.rs (L70-75)
```rust
impl RewardEpochDelegatedStakes {
    pub(crate) fn set(&self, bank: &Bank, distribution_vote_accounts: &VoteAccounts) {
        assert!(
            distribution_vote_accounts.len() <= MAX_ALPENGLOW_VOTE_ACCOUNTS,
            "reward epoch delegated stakes account must be bounded by MAX_ALPENGLOW_VOTE_ACCOUNTS"
        );
```
