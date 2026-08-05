## Title
Epoch-rewards sysvar is never funded for block-revenue-sharing payouts, causing `update_epoch_rewards_sysvar` to panic (or, absent the panic, delegators receive minted lamports with no offsetting debit) — (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`, `runtime/src/bank/partitioned_epoch_rewards/sysvar.rs`)

### Summary
The reported bug's broken invariant is: a reward-claim path credits a recipient with lamports without verifying/reserving that a real, funded balance backs the credit, so the ledger becomes internally inconsistent (claimed > actually escrowed). The closest Agave analog is in the SIMD-0123 block-revenue-sharing reward path: `Bank::begin_partitioned_rewards` creates the `EpochRewards` sysvar with the `block_rewards` argument hard-coded to `0`, while the later distribution step (`distribute_epoch_rewards_in_partition` → `update_epoch_rewards_sysvar`) unconditionally tries to debit `block_reward_lamports_distributed + block_reward_lamports_burned` lamports from that same sysvar account. The sysvar is never credited with the funds it is later required to pay out of.

### Finding Description
`calculate_block_reward` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:173-232`) computes a per-delegator `block_reward` from a vote account's `pending_delegator_rewards` field, and `build_updated_stake_reward` (`distribution.rs:239-267`) unconditionally credits that `block_reward` to the staker's account via `checked_add_lamports`, with `distribute_epoch_rewards_in_partition` (`distribution.rs:173-224`) *not* increasing capitalization for block rewards (comment: "since block reward lamports already existed") — instead it calls `update_epoch_rewards_sysvar(..., block_reward_lamports_distributed + block_reward_lamports_burned)`, which debits that amount from the `EpochRewards` sysvar account: [1](#0-0) 

This implies the sysvar account is expected to be the escrow/reserve that funds block-reward payouts (analogous to the `ChefIncentivesController`'s RDNT reserve in the external report). However, the only call site that creates this sysvar, `begin_partitioned_rewards`, passes the literal `0` for the `block_rewards` parameter instead of a computed total: [2](#0-1) 

Compare this to the unit tests, which explicitly compute and pass a non-zero `block_rewards` sum when calling `create_epoch_rewards_sysvar` directly: [3](#0-2) 

`create_epoch_rewards_sysvar` itself documents that block-reward lamports are added to the sysvar "since block reward lamports already existed" (i.e., it assumes the caller supplies the pre-existing pool of lamports to be moved into the sysvar as escrow): [4](#0-3) 

Because `begin_partitioned_rewards` never sums the `block_reward` field across `PartitionedStakeRewards` and always passes `0`, the sysvar account is created/topped-up with zero block-reward lamports even when `block_revenue_sharing` is active and vote accounts have non-zero `pending_delegator_rewards`. When distribution later runs and any `block_reward_lamports_distributed`/`burned` is non-zero, `update_epoch_rewards_sysvar`'s `checked_sub_lamports(debit_block_reward_lamports).expect(...)` will underflow and panic, because the sysvar was never funded for that debit: [5](#0-4) 

I could not find, within the code retrieved, any other code path that (a) sums per-delegation `block_reward` amounts into a bank-level total before `create_epoch_rewards_sysvar` is called, (b) decrements the source vote account's actual lamport balance or its `pending_delegator_rewards` field at distribution time, or (c) resets `pending_delegator_rewards` after a payout. All writes to `pending_delegator_rewards` I found are in `add_pending_delegator_rewards` (deposit path only) and test helpers: [6](#0-5) 

This mirrors the report's core invariant break: a reward amount is computed and credited to recipients (stake accounts, via `checked_add_lamports`) with no verified, decremented source of funds — the "reserve" (sysvar escrow, or the vote account's own balance/`pending_delegator_rewards` counter) is never actually debited by the corresponding amount, so the total lamports credited to stakers is unbacked by any decrease elsewhere in the ledger. Whether this manifests as an immediate `.expect()` panic (a crash) or, if that panic is somehow bypassed/refactored away, as silent capitalization inflation (stakers receive real lamports that were never subtracted from any account, corrupting `Bank::capitalization` and total token supply) — either outcome corresponds to the "insolvency"/incorrect-accounting failure mode in the external report.

### Impact Explanation
If `block_revenue_sharing` (SIMD-0123) is active and any validator has non-zero `pending_delegator_rewards` with active delegated stake, the very first `distribute_epoch_rewards_in_partition` call that has a non-zero `block_reward_lamports_distributed`/`burned` will hit the `.expect()` panic in `update_epoch_rewards_sysvar` because the sysvar was funded with `0` block rewards at creation time. Because reward distribution runs deterministically as part of normal epoch-boundary bank processing on every validator, this is not a targeted attack but a network-wide, deterministic panic — i.e., a consensus-halting crash affecting all validators simultaneously once the feature is active and the condition is met. This satisfies the "consensus halt" / "false execution/rooting" impact category. If the `.expect()` were absent, the mis-accounting would instead permanently inflate the effective token supply held in stake accounts without any corresponding capitalization accounting or vote-account debit, corrupting reward accounting fleet-wide (fund creation from nothing).

### Likelihood Explanation
This triggers deterministically and requires no adversarial input — only the coexistence of the `block_revenue_sharing` feature being active, at least one validator receiving non-zero `pending_delegator_rewards` via `DepositDelegatorRewards`, and one epoch boundary reward distribution. Given SIMD-0123 is a real, feature-gated part of this codebase and its tests explicitly exercise a non-zero `block_rewards` argument (showing the intended, but not implemented, wiring), the missing computation in `begin_partitioned_rewards` looks like an unfinished/incorrect integration rather than a hypothetical edge case; it would likely be caught quickly in any environment where the feature is enabled with real deposits, but as coded it represents a hard, reproducible defect.

### Recommendation
- In `begin_partitioned_rewards`, compute the total block-reward lamports across all `PartitionedStakeRewards` entries (sum of `PartitionedStakeReward::block_reward`) and pass that sum — not `0` — into `create_epoch_rewards_sysvar`, so the sysvar account is actually pre-funded with the escrowed block-reward pool before any distribution debits it.
- At the point block rewards are computed/deposited (`deposit_delegator_rewards`/vote account), ensure `pending_delegator_rewards` is decremented (or the vote account's lamports moved into the reward-distribution reserve) exactly once per rewarded amount, so repeated epochs cannot double-count the same pending balance.
- Add a hard, non-panicking guard (return an error / skip distribution) rather than `.expect()` when the sysvar cannot cover the block-reward debit, consistent with how the referenced Radiant fix converted a silent pause into an explicit revert.
- Add an integration test that runs a full `begin_partitioned_rewards` → `distribute_partitioned_epoch_rewards` cycle with `block_revenue_sharing` enabled and non-zero deposited `pending_delegator_rewards`, verifying the sysvar's lamport balance never underflows and that vote-account/pending-reward state is consistently decremented.

### Proof of Concept
1. Enable feature `block_revenue_sharing` (SIMD-0123) together with its prerequisites (`commission_rate_in_basis_points`, `custom_commission_collector`) on a test bank.
2. Have a validator's identity call `DepositDelegatorRewards` on its vote account to set `pending_delegator_rewards > 0`, as exercised in `vote_processor.rs`'s `test_deposit_delegator_rewards` (`programs/vote/src/vote_processor.rs:4841-5217`).
3. Ensure the validator has active delegated stake so `calculate_block_reward` (`calculation.rs:173-232`) returns a non-zero `block_reward` for at least one `PartitionedStakeReward`.
4. Advance the bank across the epoch boundary so `begin_partitioned_rewards` runs; observe it calls `create_epoch_rewards_sysvar(..., 0)` (`calculation.rs:276-283`) — the sysvar account is not credited with the pending block-reward lamports.
5. Continue distribution so `distribute_epoch_rewards_in_partition` computes non-zero `block_reward_lamports_distributed` (`distribution.rs:173-224`) and calls `update_epoch_rewards_sysvar` (`sysvar.rs:74-109`), which will panic on `account.checked_sub_lamports(debit_block_reward_lamports).expect(...)` (`sysvar.rs:98-101`) because the sysvar's lamport balance never received the corresponding deposit.

Note: I was unable to fully verify, from the retrieved index slices alone, whether some other, not-yet-located code path supplies the correct block-reward total to `create_epoch_rewards_sysvar` under different call sites or feature-gated branches (e.g., a snapshot-restore/recalculation path). If such a path exists and correctly sums block rewards, this reduces to a narrower gap covering only the `begin_partitioned_rewards` entry point; I recommend a Devin session with full-repository access to grep all call sites of `create_epoch_rewards_sysvar` and confirm whether any other caller supplies a non-zero `block_rewards` value before concluding the exact blast radius.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/sysvar.rs (L58-72)
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

        self.log_epoch_rewards_sysvar("create");
    }
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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L276-283)
```rust
        self.create_epoch_rewards_sysvar(
            distributed_lamports + distributed_to_incinerator_lamports + burned_lamports,
            distribution_starting_block_height,
            num_partitions,
            point_value,
            0, // block_rewards
        );

```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L587-606)
```rust
        let block_rewards = stake_rewards
            .iter()
            .map(|stake_reward| stake_reward.block_reward)
            .sum::<u64>();
        populate_starting_stake_accounts_from_stake_rewards(
            &bank,
            &bank.rent_collector.rent,
            &stake_rewards,
        );

        bank.create_epoch_rewards_sysvar(
            0,
            42,
            num_partitions,
            &PointValue {
                rewards: inflation_rewards,
                points: total_points,
            },
            block_rewards,
        );
```

**File:** programs/vote/src/vote_state/handler.rs (L196-209)
```rust
    pub(crate) fn add_pending_delegator_rewards(
        &mut self,
        amount: u64,
    ) -> Result<(), InstructionError> {
        match &mut self.target_state {
            TargetVoteState::V4(v4) => {
                v4.pending_delegator_rewards = v4
                    .pending_delegator_rewards
                    .checked_add(amount)
                    .ok_or(InstructionError::ArithmeticOverflow)?;
                Ok(())
            }
        }
    }
```
