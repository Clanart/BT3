### Title
Partition-boundary "block reward" double-debit from the shared `EpochRewards` sysvar pot on recalculation - (File: `runtime/src/bank/partitioned_epoch_rewards/sysvar.rs`)

### Summary
`update_epoch_rewards_sysvar()` treats the `EpochRewards` sysvar account as a single, shared lamport "pot" that funds *every* per-partition block-reward payout across an entire epoch's distribution window, decrementing it via `checked_sub_lamports(...).expect(...)`. Like the Gondi `settleWithBuyout()` bug — where a fee meant to be paid by one specific buyer was instead silently drawn from the contract's shared balance, at the expense of unrelated concurrent auctions — this Agave mechanism pays out block rewards for one partition/stake-account set from a pot that is implicitly shared with all other partitions still pending distribution in the same epoch. If the block-reward amount computed for a partition at distribution time doesn't match what was budgeted for it at calculation time (which can happen because `calculate_block_reward()` is re-derived from the *live* `stakes_cache`/`distribution_epoch_vote_accounts` rather than a fixed snapshot), the debit either silently starves later partitions of their rightful share, or overshoots the pot and panics via `.expect(...)`.

### Finding Description
`create_epoch_rewards_sysvar()` funds the `sysvar::epoch_rewards::id()` account with a `block_rewards` pool at the start of an epoch's reward cycle [1](#0-0) . Each subsequent block during the distribution window calls `distribute_epoch_rewards_in_partition()`, which computes `block_reward_lamports_distributed + block_reward_lamports_burned` for that partition and calls `update_epoch_rewards_sysvar()` to debit exactly that amount from the pot [2](#0-1) .

The debit itself has no bound to the specific partition's fair share — it simply subtracts from whatever is currently in the shared account: [3](#0-2) 

Critically, the per-account `block_reward` values summed into that debit are not fixed at epoch-calculation time for the whole distribution window. `calculate_block_reward()` is re-invoked during `recalculate_stake_rewards()` (used by `recalculate_partitioned_rewards_if_active`, itself invoked on `Bank::new_from_parent` after a mid-epoch-rewards-distribution warp/restart) using the *current* `stakes_cache` and `distribution_epoch_vote_accounts`, not a static snapshot from `begin_partitioned_rewards` time [4](#0-3) . The code's own comment acknowledges this drift is possible: "*during recalculation, `distribution_epoch_vote_accounts` already includes updated stake activation values ... so we need to use `RewardEpochDelegatedStakes` for the exact values*" [5](#0-4) , and a separate comment on the commission side explicitly warns that recalculated account state "*should NOT be used ever*" for post-balance reporting because it diverges from calculation-time data [6](#0-5) .

This is structurally the same broken invariant as the Gondi finding: a payout meant to be sourced from/attributed to one specific, bounded unit of accounting (one auction's buyer / one partition's pre-computed budget) is instead drawn from an undifferentiated shared balance that other, unrelated units (other auctions / other not-yet-distributed partitions) also depend on. Nothing in `update_epoch_rewards_sysvar` checks that the debit corresponds to the amount originally reserved for *this* partition — it just subtracts from the pot and panics if it can't.

### Impact Explanation
- If a partition's recalculated `block_reward_lamports_distributed + block_reward_lamports_burned` total exceeds what remains in the sysvar's block-reward allocation (e.g., because stake/vote state moved between calculation and a later recalculation, inflating that partition's share), `checked_sub_lamports(...).expect("epoch reward sysvar has enough lamports for distribution")` fires. Because this runs deterministically inside `Bank::freeze`/reward-distribution processing on every replaying validator, it is not a localized crash — it is a network-wide panic on the same slot for every node that reaches this code path, i.e. a consensus/liveness halt rather than a state divergence.
- If instead a partition's share is reduced relative to what other partitions had already been paid, later partitions can be silently under-paid or over-drained by unrelated partitions' distributions, since the pot has no partition-level bookkeeping — analogous to the "other auctions unable to settle due to insufficient balance" impact called out in the source report.

### Likelihood Explanation
This path only activates during `recalculate_partitioned_rewards_if_active`, which fires when a bank is reconstructed mid-distribution (e.g., snapshot restore or warp) rather than on every ordinary epoch boundary, so it is not attacker-triggerable on demand by a normal unprivileged transaction. This significantly limits likelihood versus the original smart-contract bug (which any user could trigger via a normal call). The finding is best read as a structural/architectural analog of the reported bug-class — a shared, undifferentiated balance funding logically-separate payouts with a hard-panic on mismatch — rather than a fully demonstrated, externally-triggerable exploit; I could not find a concrete unprivileged transaction sequence in the reachable code that forces `calculate_block_reward()`'s output to change between calculation and recalculation without a validator restart/warp in the loop, so likelihood should be treated as **low/uncertain** pending further review of exactly which state (stake delegation, vote credits) can shift for already-computed-but-undistributed partitions.

### Recommendation
Track and debit each partition's exact pre-computed block-reward budget from calculation time (store the per-partition allocation instead of re-deriving amounts against a shared running balance), and replace the `.expect()` panic in `update_epoch_rewards_sysvar` with a bounded/saturating accounting path plus an explicit invariant check performed once at calculation time (verifying `sum(all partitions' block_reward) == total block_rewards funded`) rather than discovering a shortfall via a runtime panic during distribution.

### Proof of Concept
Not independently reproducible from static analysis alone: exercising the divergence requires constructing a scenario where a bank is torn down and reconstructed (snapshot restore/warp) in the middle of an active `EpochRewardPhase::Distribution`, such that `recalculate_stake_rewards()` recomputes `calculate_block_reward()` against a `stakes_cache`/`distribution_epoch_vote_accounts` state that differs from the one used in `begin_partitioned_rewards`. The existing test `test_recalculate_stake_rewards` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` (lines 2417-2498, and the related partial-distribution test around lines 2770-2798) already exercises the recalculation path and its "must use the same AG delegated stake denominator" assumption — that assumption is exactly the invariant this finding argues can be violated in a broader class of restart timings, which would need targeted testing/fuzzing across the calculation → recalculation boundary to confirm concretely.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/sysvar.rs (L27-71)
```rust
    pub(in crate::bank) fn create_epoch_rewards_sysvar(
        &self,
        distributed_rewards: u64,
        distribution_starting_block_height: u64,
        num_partitions: u64,
        point_value: &PointValue,
        block_rewards: u64,
    ) {
        assert!(point_value.rewards >= distributed_rewards);

        let parent_blockhash = self.last_blockhash();

        let epoch_rewards = EpochRewards {
            distribution_starting_block_height,
            num_partitions,
            parent_blockhash,
            total_points: point_value.points,
            total_rewards: point_value.rewards,
            distributed_rewards,
            active: true,
        };

        // Do the first store to create the account from scratch, update
        // capitalization if needed, etc
        self.update_sysvar_account(&sysvar::epoch_rewards::id(), |account| {
            create_account(
                &epoch_rewards,
                self.inherit_specially_retained_account_fields(account),
            )
        });

        // Now add the lamports separately without updating capitalization,
        // since block reward lamports already existed
        let mut account = self
            .get_account_with_fixed_root(&sysvar::epoch_rewards::id())
            .expect("created sysvar account exists");

        // SAFETY: block rewards come from existing lamports, which cannot
        // overflow
        account
            .checked_add_lamports(block_rewards)
            .expect("block rewards and sysvar account rent exemption must fit in a u64");
        self.store_account(&sysvar::epoch_rewards::id(), &account);

        self.log_epoch_rewards_sysvar("create");
```

**File:** runtime/src/bank/partitioned_epoch_rewards/sysvar.rs (L75-106)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-232)
```rust
/// Calculates block reward for a stake account based on SIMD-0123
fn calculate_block_reward(
    rewarded_epoch: Epoch,
    delegation: &Delegation,
    stake_history: &StakeHistory,
    distribution_epoch_vote_accounts: &VoteAccounts,
    ag_epoch_type: &AlpenglowEpochType,
    new_warmup_cooldown_rate_epoch: Option<Epoch>,
    use_fixed_point_stake_math: bool,
) -> u64 {
    let vote_pubkey = delegation.voter_pubkey;
    let Some(vote_account) = distribution_epoch_vote_accounts.get(&vote_pubkey) else {
        debug!("could not find vote account {vote_pubkey} in cache");
        return 0;
    };
    let vote_state = vote_account.vote_state_view();
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();
    // NOTE: during recalculation, `distribution_epoch_vote_accounts` already
    // includes updated stake activation values from after the new epoch
    // calculation, so we need to use `RewardEpochDelegatedStakes` for the exact
    // values at the end of the reward epoch.
    let (AlpenglowEpochType::Alpenglow {
        reward_epoch_delegated_stakes,
        ..
    }
    | AlpenglowEpochType::MigrationEpoch {
        reward_epoch_delegated_stakes,
        ..
    }) = ag_epoch_type
    else {
        debug!("Alpenglow must be enabled for block reward calculation");
        return 0;
    };
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
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L1069-1075)
```rust
        // NOTE: the `RewardCommissionAccounts` will NOT have a correct
        // post_lamport amount if the commission account is NOT the vote account,
        // because the commission account is loaded from the current bank, and
        // not the start of the epoch. We don't have a snapshot of all commission
        // accounts from the start of the epoch. For this reason, the
        // `RewardCommissionAccounts` calculated in this function call should
        // NOT be used ever.
```
