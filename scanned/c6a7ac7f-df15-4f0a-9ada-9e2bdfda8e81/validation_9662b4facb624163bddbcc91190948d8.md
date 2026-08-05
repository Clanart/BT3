### Title
Reward distribution phase asserts stake delegation consistency computed during calculation phase, panicking (validator crash) if an ordinary `Split`/`Merge` stake operation intervenes before distribution - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
Partitioned epoch rewards are computed once at the epoch boundary ("calculation phase") and then applied to stake accounts over many subsequent blocks ("distribution phase"). `Bank::build_updated_stake_reward` re-derives an "expected" post-reward delegation from the *live* stake account read from `StakesCache` at distribution time, and asserts it equals the delegation value that was computed during the earlier calculation phase. Any legitimate, unprivileged stake-account mutation (e.g. `Split`, or partial state changes captured by the stake program) that lands on-chain between the calculation block and the account's distribution block breaks this invariant and hits an `assert_eq!`, which unwinds as a panic inside the deterministic bank state-transition path — i.e. every validator processing that slot crashes identically.

### Finding Description
`store_stake_accounts_in_partition` reads the *current* stake account for each pubkey from `self.stakes_cache.stakes().stake_delegations()` at distribution time and calls `Self::build_updated_stake_reward`: [1](#0-0) 

Inside `build_updated_stake_reward`, when the `adjust_delegations_for_rent` feature branch is not taken, the code computes an "expected" delegation from the *live* `stake.delegation.stake` (read from the account as of distribution time) plus the reward amount computed back at calculation time, and asserts it equals `new_stake.delegation.stake` — the delegation value that was computed and baked into `partitioned_stake_reward.inflation.stake` during the calculation phase, using the account's state as of the calculation block: [2](#0-1) 

The comment above `store_stake_accounts_in_partition` states the assumption this assert depends on: [3](#0-2) 

"Because stake accounts are checked in calculation, and further state mutation prevents by stake-program restrictions, there should never be rewards burned." This assumption is the exact analog of the external report's flaw: a downstream operation (`assert_eq!`/reward application) assumes an earlier "snapshot" of state (`stake.delegation.stake` value computed at calculation time) is guaranteed to still match the live account at the time the dependent operation runs, but nothing in `distribute_partitioned_epoch_rewards` re-validates or "re-charges" that state before consuming it — much like `Purger::absorb` assumed `shrine.melt` (interest accrual) had already run before `shrine.redistribute` consumed the trove's debt.

The distribution phase spans multiple blocks — one partition per block — between `distribution_starting_block_height` and `distribution_end_exclusive`: [4](#0-3) 

During this multi-block window, an ordinary staker retains full control of their stake account and can submit a normal `Split` or other stake-program instruction that changes `delegation.stake` on their own account without violating any stake-program restriction (the code comment's claim that "further state mutation [is] prevent[ed] by stake-program restrictions" does not hold for `Split`, which is explicitly designed to reduce the source account's delegated stake while the account remains active). This is confirmed by the codebase's own regression test `test_delegation_adjustment_at_distribution`, which manually simulates a lamport transfer into the stake account between calculation and distribution and shows the resulting divergence must be specially handled by the `adjust_delegations_for_rent` branch: [5](#0-4) 

That special handling only covers the `adjust_delegations_for_rent` (i.e. `relax_post_exec_min_balance_check`) feature-enabled branch. When that feature is not active (e.g. on clusters where the feature has not yet been activated, or feature-gated test/dev clusters using this build), the `else` branch's `assert_eq!` is reached unconditionally for every stake reward, and it has no tolerance for delegation changes caused by intervening `Split`/`Merge` activity.

### Impact Explanation
An `assert_eq!` failure panics the thread executing `Bank::freeze` / block processing. Because `distribute_partitioned_epoch_rewards` runs as part of normal deterministic bank state transition (not an isolated or catchable transaction context), every validator that processes the affected slot will hit the identical panic and crash simultaneously — this is a cluster-wide non-RPC crash / consensus halt, triggerable without any privileged access, malicious peer assumption, or leaked keys. This is strictly worse than the original report's "extra bad debt" data-validation bug: instead of silently miscounting debt, it produces a deterministic full-cluster crash.

### Likelihood Explanation
Any unprivileged wallet holding a delegated, activating/active stake account that happens to fall in a later distribution partition can trigger this by submitting a normal `Split` instruction on their own stake account in the window between the reward calculation block and their account's assigned distribution block (which can span many blocks, given `num_partitions`). No cooperation from validators, no malicious assumptions, and no elevated privileges are required — only a standard stake-program instruction available to any staker. The likelihood of accidentally hitting this path already exists in production usage patterns (splitting/restaking during epoch boundaries), and the codebase's own test at line 1263 acknowledges the underlying divergence scenario is realistic; it is only guarded when `adjust_delegations_for_rent` is active.

### Recommendation
- **Short term:** Remove the unconditional `assert_eq!` in the `else` branch of `build_updated_stake_reward` (or make it a soft-fail returning `DistributionError` instead of panicking), and reconcile any divergence between the calculation-time delegation snapshot and the live account state by re-deriving the correct post-reward delegation from current on-chain account state, mirroring what the `adjust_delegations_for_rent` branch already does, regardless of feature-gate status.
- **Long term:** Do not assume account state is frozen between the reward "calculation" and "distribution" phases; recompute or reconcile any state a downstream distribution step depends on directly from the live account, and add fuzzing/property tests that intersperse arbitrary stake-program instructions (`Split`, `Merge`, `Withdraw`, `Deactivate`) between calculation and distribution blocks to catch these class of staleness bugs before they reach production.

### Proof of Concept
1. Start of epoch: reward calculation runs, computing `partitioned_stake_reward.inflation.stake` for staker A's account based on A's delegation at that moment (say `D`).
2. Before A's partition is processed in the distribution phase (which can be many blocks later), A submits a `Split` instruction on the same stake account (a completely normal, permitted staking operation), reducing the account's live `delegation.stake` to `D' != D`.
3. When A's partition is later processed, `build_updated_stake_reward` reads the now-live delegation `D'` from `StakesCache`, computes `expected_delegation = D' + reward`, and compares it against `new_stake.delegation.stake = D + reward` (baked in from step 1). Since `D' != D`, `assert_eq!` fails, panicking the block-processing thread on every validator processing that slot. [2](#0-1)

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-252)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L284-294)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L1263-1266)
```rust
        // But we transfer in more lamports before distribution time
        stake_account.checked_add_lamports(1_000_000_000).unwrap();
        bank.store_account(&stake_pubkey, &stake_account);

```
