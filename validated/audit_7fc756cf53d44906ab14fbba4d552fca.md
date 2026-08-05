### Title
`begin_partitioned_rewards` funds the `EpochRewards` sysvar with zero block-reward lamports while distribution unconditionally debits real block-reward amounts, causing a deterministic validator panic - (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs` / `runtime/src/bank/partitioned_epoch_rewards/sysvar.rs`)

### Summary
This is the Agave analog of the MasterChef "under-supply" bug: a payout path assumes a pool of funds exists to cover a computed reward amount, but the pool is never actually funded with that amount. In the C4 report, `safeConcurTransfer` silently under-pays users when the MasterChef balance is insufficient. In Agave, the `EpochRewards` sysvar account plays the role of the "pool" that block-reward (block revenue sharing / SIMD-0123) payouts are debited from at distribution time, but the sysvar-creation call in the production entry point `begin_partitioned_rewards` explicitly funds it with `0` lamports for block rewards, while the debit path enforces the invariant with a hard `.expect()` instead of silently truncating — turning what would be silent value loss in the Solidity report into a deterministic panic across the fleet.

### Finding Description
`Bank::begin_partitioned_rewards` (the real per-epoch entry point that creates the `EpochRewards` sysvar for a new reward cycle) calls: [1](#0-0) 

passing a literal `0` for the `block_rewards` parameter, meaning the sysvar account is only funded up to its rent-exempt minimum — no lamports are reserved there to cover the block-reward (staker) side of block revenue sharing.

`create_epoch_rewards_sysvar` stores that `block_rewards` argument directly into the sysvar's lamport balance: [2](#0-1) 

Later, during actual distribution, `distribute_epoch_rewards_in_partition` computes a real, non-zero `block_reward_lamports_distributed` (+ any burned) from `PartitionedStakeReward.block_reward`, which is derived from vote accounts' `pending_delegator_rewards` via `calculate_block_reward`: [3](#0-2) 

and unconditionally attempts to debit that amount from the same `EpochRewards` sysvar account: [4](#0-3) [5](#0-4) 

The debit is guarded only by `.expect("epoch reward sysvar has enough lamports for distribution")` — a hard assumption, not a checked/graceful failure path. Because the sysvar was created with `block_rewards = 0` in the production flow, and I could not locate any other code path that subsequently deposits the staker block-reward budget into the `EpochRewards` sysvar account (the only other lamport movement I found related to block revenue sharing is `deposit_delegator_rewards`, which moves lamports into the *vote account's* own balance via CPI, not into the sysvar), any epoch in which `block_revenue_sharing` is active and stakers have non-zero `block_reward` amounts to be paid would hit `checked_sub_lamports` on an account that never received that balance.

This directly mirrors the reported bug's broken invariant ("assume a pool has enough balance to pay out an amount that was computed independently of the pool's real balance"), except Agave's stricter accounting turns it into a `.expect()`-triggered panic rather than a silent underpayment.

### Impact Explanation
`distribute_partitioned_epoch_rewards` is executed unconditionally by every validator on every block during the reward-distribution phase after an epoch boundary: [6](#0-5) 

Since this code runs deterministically on every replaying node (not just the leader), a panic here would crash every validator that processes the block at the same block height — i.e., a cluster-wide consensus halt rather than a single-node crash. This is unprivileged: no attacker action is required beyond the network having active stake with non-zero `pending_delegator_rewards` (deposited normally via `deposit_delegator_rewards`) once `block_revenue_sharing` is enabled.

### Likelihood Explanation
This path is gated by the `block_revenue_sharing`/SIMD-0123/SIMD-0392 feature flags, which are still under rollout in this codebase (the code repeatedly checks `feature_snapshot.block_revenue_sharing`, and Alpenglow gating). The likelihood is high once the feature is activated, since the sysvar-creation call site with the literal `0` is on the sole production path (`begin_partitioned_rewards`) for starting the reward cycle. I was not able to conclusively verify, within the available search budget, whether some other code path funds the sysvar with the correct block-reward budget before distribution begins (e.g., as part of `distribute_reward_commissions` or the VAT/incinerator burn logic) or whether the debit is actually meant to source funds from the vote account itself and the sysvar debit is purely a bookkeeping error. This uncertainty should be resolved by tracing the full lifecycle of `pending_delegator_rewards` (I could find where it's incremented via `add_pending_delegator_rewards`, but not where/if it is decremented after distribution) and by confirming whether any other call site passes a non-zero `block_rewards` value to `create_epoch_rewards_sysvar` in production.

### Recommendation
- Verify whether the staker block-reward budget is meant to be pre-funded into the `EpochRewards` sysvar before the first distribution partition runs; if so, `begin_partitioned_rewards` must pass the correct aggregate block-reward total (sum of all `PartitionedStakeReward.block_reward`) instead of the literal `0`.
- If instead the design intends for block-reward lamports to be debited from the originating vote accounts' balances (matching where `pending_delegator_rewards` lamports actually live), then `update_epoch_rewards_sysvar`/`build_updated_stake_reward` should debit the corresponding vote account and decrement `pending_delegator_rewards`, rather than debiting the sysvar account.
- Replace the `.expect()` panics in `update_epoch_rewards_sysvar` with a recoverable/checked accounting path (or an invariant assertion enforced earlier, at reward-calculation time) so that any budget mismatch is caught deterministically during calculation rather than causing a fleet-wide panic mid-distribution.

### Proof of Concept
Not executable from static analysis alone within the current investigation. The concrete reproduction steps to confirm on a live/test validator would be:
1. Activate the `block_revenue_sharing` feature (and Alpenglow/migration-epoch state) in a local test cluster.
2. Have delegators call the vote program's `deposit_delegator_rewards` instruction to set a non-zero `pending_delegator_rewards` on a vote account with active stake delegated to it.
3. Advance through an epoch boundary so `begin_partitioned_rewards` runs and creates the `EpochRewards` sysvar (observe it is funded with `block_rewards = 0` per [1](#0-0) ).
4. Advance to the reward distribution blocks and observe whether `update_epoch_rewards_sysvar`'s `checked_sub_lamports(debit_block_reward_lamports).expect(...)` at [7](#0-6)  panics.

Given the remaining uncertainty about whether an intermediate funding step exists elsewhere in the codebase, this should be treated as a concrete lead requiring live/dynamic verification (e.g., via a Devin session with test-cluster access) rather than a fully confirmed exploit.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L173-231)
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L276-282)
```rust
        self.create_epoch_rewards_sysvar(
            distributed_lamports + distributed_to_incinerator_lamports + burned_lamports,
            distribution_starting_block_height,
            num_partitions,
            point_value,
            0, // block_rewards
        );
```

**File:** runtime/src/bank/partitioned_epoch_rewards/sysvar.rs (L58-69)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/sysvar.rs (L92-106)
```rust
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L145-149)
```rust
        if height >= distribution_starting_block_height && height < distribution_end_exclusive {
            let partition_index = height - distribution_starting_block_height;

            self.distribute_epoch_rewards_in_partition(partition_rewards, partition_index);
        }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L196-204)
```rust
        // decrease total capitalization by burned block rewards
        self.capitalization
            .fetch_sub(block_reward_lamports_burned, Relaxed);

        // decrease distributed capital from epoch rewards sysvar
        self.update_epoch_rewards_sysvar(
            stake_reward_lamports_minted + stake_reward_lamports_burned,
            block_reward_lamports_distributed + block_reward_lamports_burned,
        );
```
