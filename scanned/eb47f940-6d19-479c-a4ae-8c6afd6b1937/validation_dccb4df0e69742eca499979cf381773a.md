## Analysis

The bug-class from the external report is: **an unprivileged actor can grow an unbounded set at negligible marginal cost, and that set is then fully iterated in a mandatory hot path with per-item cost, with no cap enforced.**

The strongest Agave analog is the **stake-delegations set** (`Stakes::stake_delegations`), which is unconditionally, fully iterated by every validator at every epoch boundary to calculate activated stake and epoch rewards.

### Supporting evidence

- `stake_delegations` is an `ImblHashMap<Pubkey, StakeAccount>` that is only removed from when an account's lamports reach zero (i.e., fully closed/withdrawn) — a deactivated-but-not-withdrawn stake account (effective stake == 0) stays in the map forever: [1](#0-0) 
- Every epoch, the entire map is collected into a `Vec` and iterated in full via `calculate_activated_stake`/`calculate_delegated_stakes`, an operation whose own doc-comment calls out that iteration performance is poor ("depth-first traversal and jumps") and that collection itself measurably costs ~200ms just for the copy, before the O(N) work even starts: [2](#0-1) 
- `calculate_activated_stake` unconditionally folds/reduces over **every** entry in `stake_delegations`, with no cap on `stake_delegations.len()`: [3](#0-2) 
- `calculate_delegated_stakes` likewise iterates every stake account unconditionally: [4](#0-3) 
- This is invoked synchronously as part of normal epoch-boundary processing on every bank: [5](#0-4) 
- Epoch reward computation likewise iterates the full delegation set and the code explicitly acknowledges million-scale N as a known/expected condition without imposing a bound: [6](#0-5) 
- The only "guard" that exists is a **minimum active delegation amount** (1 SOL) required to keep a delegation counted toward stake — but this only gates *active* stake, not map membership; and it is enforced by `stake_program`, not by the runtime path that iterates the set: [7](#0-6) 

This is structurally identical to the reported issue: cheap, unprivileged growth of a set (creating and then deactivating a rent-exempt stake account costs only the ~0.00228 SOL rent-exempt reserve, not the 1 SOL minimum delegation, since deactivated stake need not remain delegated at that minimum) combined with mandatory, unbounded, per-item iteration of that same set on every validator, every epoch, with no upper bound enforced anywhere in the iteration path.

### Title
Unbounded per-epoch iteration over attacker-growable `Stakes::stake_delegations` set enables validator-wide compute exhaustion at epoch boundaries - (File: `runtime/src/stakes.rs`)

### Summary
`Stakes::stake_delegations` is a HashMap keyed by stake-account pubkey that only shrinks when a stake account is fully closed (`lamports == 0`). Every validator, at every epoch boundary, must collect and fully iterate this entire map (`stake_delegations_vec()`, `calculate_activated_stake()`, `calculate_delegated_stakes()`, and again during reward calculation) with no size cap. An unprivileged user can create many stake accounts, delegate the required minimum, deactivate, and leave them open indefinitely for only the cost of the rent-exempt reserve, permanently inflating the set that every validator must process on every epoch transition.

### Finding Description
`StakesCache::check_and_store` only calls `remove_stake_delegation` when the account's lamports drop to zero, i.e., when the account is fully closed and rent is reclaimed [1](#0-0) . A stake account that has been deactivated (its `Delegation` becomes inactive / effective stake trends to 0) but keeps its rent-exempt balance remains a permanent entry in `stake_delegations`.

At every epoch boundary, `Bank::compute_new_epoch_caches_and_rewards` unconditionally materializes and processes the whole set: [5](#0-4) . `stake_delegations_vec()` itself is documented to be relatively expensive to just collect due to the underlying HAMT's traversal characteristics [2](#0-1) , and `calculate_activated_stake` then does an O(N) parallel fold/reduce over every entry with no bound [8](#0-7) . `calculate_delegated_stakes` does an equivalent unconditional serial iteration [9](#0-8) . Epoch reward computation repeats a similar full-set walk, and the code's own comment acknowledges that N can be "> 1,000,000" without providing a cap [6](#0-5) .

The only economic disincentive in the codebase is the *minimum active delegation* of 1 SOL enforced by the stake program at delegate-time [7](#0-6) . This does not gate map membership: once a stake account is deactivated, it no longer needs to satisfy the minimum-delegation invariant to remain open, so an attacker can create a stake account, delegate the 1 SOL minimum for a single epoch, deactivate, and (optionally) withdraw the delegated 1 SOL back out (leaving only the rent-exempt reserve, ~0.00228 SOL for `StakeStateV2::size_of()`), while the account entry itself persists in `stake_delegations` indefinitely. Nothing in `check_and_store`, `calculate_activated_stake`, or `calculate_delegated_stakes` imposes a maximum count on `stake_delegations`, unlike `MAX_TX_ACCOUNT_LOCKS` for transaction-level account sets.

### Impact Explanation
Growing `stake_delegations` unboundedly increases the fixed per-epoch (roughly every 2-3 days) computational cost that **every** validator on the network must pay synchronously as part of bank epoch-boundary processing — a step that is on the critical path for producing/validating the first block(s) of a new epoch. Because this cost scales with total network-wide account count (not with any single validator's own stake or resources), a single unprivileged attacker willing to pay a small, mostly one-time rent-exempt cost per "ghost" stake account can force this fixed cost to grow for the entire fleet indefinitely, since there is no cap and no way for the honest network to prune inactive delegations besides the attacker voluntarily closing them. At sufficient scale this manifests as a non-RPC remote resource-exhaustion vector affecting the availability/timeliness of epoch-boundary bank processing across the whole validator set, which can degrade or (at scale) threaten consensus liveliness around epoch transitions.

### Likelihood Explanation
Likelihood is moderate: the attack requires no special privilege, only the ability to submit `CreateAccount`/`Delegate`/`Deactivate` stake instructions, and the cost per "ghost" entry is bounded by the stake-account rent-exempt reserve. It is, however, a slow, capital-scaling attack — reaching a scale where the per-epoch iteration cost becomes materially disruptive requires creating and maintaining a very large number of accounts, which costs real (if individually small) SOL and does compete for blockspace/fees to submit. This mirrors the referenced report's own characterization ("known issue with the design," "partially resolved," no full fix) — a real, currently-unmitigated design gap rather than an immediately catastrophic exploit.

### Recommendation
- Impose an explicit upper bound on the number of stake-delegation entries retained per vote account and/or globally (mirroring `MAX_TX_ACCOUNT_LOCKS`-style caps elsewhere in the codebase), rejecting new delegations once the cap is reached.
- Prune/require the minimum-delegation invariant to also apply to *inactive* stake accounts after some grace period, or make deactivation combined with rent-exempt-only balance eligible for lazy pruning rather than requiring an explicit close instruction from the account owner.
- Alternatively/complementarily, make the per-epoch iteration cost independent of dead/zero-effective-stake entries (e.g., maintain a secondary index of only currently-delegated, non-zero-effective-stake accounts) so growth of closed/deactivated accounts does not inflate the hot-path cost.

### Proof of Concept
1. Repeat N times (N large, e.g. hundreds of thousands), each with a fresh keypair:
   - `CreateAccount` a new stake account funded with `rent_exempt_reserve + minimum_delegation` (per `runtime/src/stake_utils.rs::get_minimum_delegation`).
   - `Delegate` it to any active vote account.
   - Wait one epoch for activation, then `Deactivate`.
   - Optionally `Withdraw` the delegated 1 SOL back out, leaving only the rent-exempt reserve locked in the still-open account.
2. None of these accounts are ever removed from `Stakes::stake_delegations`, because `StakesCache::check_and_store` only removes entries when `lamports() == 0` [1](#0-0) .
3. On every subsequent epoch boundary, every validator's `Bank::compute_new_epoch_caches_and_rewards` calls `stakes.stake_delegations_vec()` then `stakes.calculate_activated_stake(...)`, both of which iterate the full, now-inflated set [5](#0-4) [8](#0-7) .
4. Measure epoch-boundary processing time as N grows; because no cap exists, cost scales linearly with attacker-controlled N indefinitely.

### Citations

**File:** runtime/src/stakes.rs (L99-116)
```rust
        // Zero lamport accounts are not stored in accounts-db
        // and so should be removed from cache as well.
        if account.lamports() == 0 {
            if solana_vote_program::check_id(owner) {
                let _old_vote_account = {
                    let mut stakes = self.0.write().unwrap();
                    stakes.remove_vote_account(pubkey)
                };
            } else if stake_program::check_id(owner) {
                let mut stakes = self.0.write().unwrap();
                stakes.remove_stake_delegation(
                    pubkey,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
            }
            return;
        }
```

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

**File:** runtime/src/stakes.rs (L517-539)
```rust
    fn calculate_delegated_stakes(
        stake_delegations: &ImblHashMap<Pubkey, StakeAccount>,
        epoch: Epoch,
        stake_history: &StakeHistory,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) -> DelegatedStakes {
        let mut delegated_stakes = DelegatedStakes::new();
        for stake_account in stake_delegations.values() {
            let delegation = stake_account.delegation();
            let stake = delegation_effective_stake(
                delegation,
                epoch,
                stake_history,
                new_rate_activation_epoch,
                use_fixed_point_stake_math,
            );
            if stake != 0 {
                *delegated_stakes.entry(delegation.voter_pubkey).or_default() += stake;
            }
        }
        delegated_stakes
    }
```

**File:** runtime/src/stakes.rs (L677-691)
```rust
    /// Collects stake delegations into a vector, which then can be used for
    /// parallel iteration with [`rayon`].
    ///
    /// # Performance
    ///
    /// The execution of this method takes ~200ms and it collects elements of
    /// the [`imbl::HashMap`], which is a [hash array mapped trie (HAMT)][hamt],
    /// so that operation involves a depth-first traversal with jumps. However,
    /// it's still a reasonable tradeoff if the caller iterates over these
    /// elements.
    ///
    /// [hamt]: https://en.wikipedia.org/wiki/Hash_array_mapped_trie
    pub(crate) fn stake_delegations_vec(&self) -> Vec<(&Pubkey, &StakeAccount)> {
        self.stake_delegations.iter().collect()
    }
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L803-819)
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
```

**File:** runtime/src/stake_utils.rs (L15-27)
```rust
/// The minimum stake amount that can be delegated, in lamports.
/// When this feature is added, it will be accompanied by an upgrade to the BPF Stake Program.
/// NOTE: This is also used to calculate the minimum balance of a delegated stake account,
/// which is the rent exempt reserve _plus_ the minimum stake delegation.
#[inline(always)]
pub fn get_minimum_delegation(upgrade_bpf_stake_program_to_v5_is_active: bool) -> u64 {
    if upgrade_bpf_stake_program_to_v5_is_active {
        const MINIMUM_DELEGATION_SOL: u64 = 1;
        MINIMUM_DELEGATION_SOL * LAMPORTS_PER_SOL
    } else {
        1
    }
}
```
