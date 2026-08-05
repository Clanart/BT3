## Title
Stale-snapshot Reward Injection via Stake-Account Pubkey Reuse During Partitioned Epoch-Reward Distribution - (File: `runtime/src/bank/partitioned_epoch_rewards/distribution.rs`)

### Summary
Agave's inflation-reward pipeline splits reward payout into two phases: `calculation` (a single epoch-boundary block that snapshots every delegated `Stake` and computes a `PartitionedStakeReward` for it) and `distribution` (spread over up to 10% of the epoch's slots, where each partition is applied to whatever is *currently* in `StakesCache` for that `stake_pubkey`). Between calculation and the (possibly much later) distribution block for a given account's partition, ordinary unprivileged stake-program instructions (`Deactivate`, `Withdraw`, `Initialize`/`DelegateStake`) can fully close and re-open a stake account at the same pubkey. `build_updated_stake_reward` blindly looks up the stake account by pubkey in the live `StakesCache` and merges the stale, pre-computed `Stake`/reward snapshot into whatever it finds there, without re-validating that the account still corresponds to the delegation that earned the reward.

### Finding Description
`calculate_stake_rewards_and_commissions()` builds a `PartitionedStakeReward` per delegation using a `Stake` snapshot taken at the epoch-boundary block [1](#0-0) . This snapshot (`inflation.stake`) is stored and only actually applied to accounts many blocks later, when `distribute_partitioned_epoch_rewards` reaches that stake's partition [2](#0-1) .

At distribution time, `build_updated_stake_reward` re-fetches the account purely by `stake_pubkey` from the *live* `StakesCache`: [3](#0-2) 

It then overwrites the account's `Stake` state with the *old* snapshot (`partitioned_stake_reward.inflation.stake`), merely clamping the delegation amount to available lamports when `adjust_delegations_for_rent` is active: [4](#0-3) 

Nothing in this path checks that the delegation currently sitting at `stake_pubkey` is the *same* delegation lineage that earned the reward (e.g. by comparing `activation_epoch`, `voter_pubkey`, or a monotonically-increasing "delegation generation" marker). A user who controls the stake-account keypair can, after the calculation block but before their partition is processed:
1. Deactivate and fully `Withdraw` the stake account (this is a normal, unprivileged instruction; it zeroes lamports and returns the account to system-owned/uninitialized), closing out the delegation that earned the pending reward.
2. Re-create a stake account at the *identical* pubkey (`CreateAccount` + `Initialize` + `DelegateStake`) with a new, unrelated delegation (different validator, different amount, different activation epoch).
3. Wait for the reward-distribution block for that partition index to land. `build_updated_stake_reward` finds the newly created account, credits it with the original `stake_reward` lamports (`account.checked_add_lamports(...)`), and stamps its `Stake` state with a mash-up of the OLD reward-bearing snapshot's delegation fields and the NEW account's lamports.

Because this happens deterministically inside `Bank::store_stake_accounts_in_partition` (called from every validator replaying the same block), the exploit is fully within the "broken invariant → attacker primitive" pattern from the reported class: reward accounting is decoupled from the live state of the account that is credited, exactly like the DeFi report's core defect where `withdraw()` used stale `totalStaked`/`totalShares` instead of the up-to-date value.

When the `adjust_delegations_for_rent` feature flag (`relax_post_exec_min_balance_check`) is *not* active, the alternate branch performs a hard invariant check instead of a clamp: [5](#0-4) 
If the reused account's newly-set `Stake.delegation.stake` (the field that gets overwritten by the stale snapshot) does not equal `stake.delegation.stake + stake_reward` (computed from the *live* account before the overwrite), this `assert_eq!` panics. Since every validator executes the identical state-transition deterministically, an attacker can engineer this mismatch (simply by delegating a different amount to the reused pubkey before their reward turn) and crash the entire fleet of validators that have this feature disabled — i.e. force a deterministic panic in `store_stake_accounts_in_partition`, which is invoked unconditionally from block processing.

### Impact Explanation
- Fund-accounting corruption / theft-adjacent effect: lamports minted for the original (already-closed) delegation's reward are injected into an account whose current delegation no longer represents that stake, inflating `capitalization` in a way disconnected from the actual delegation that produced the votes/credits (`stake_reward_lamports_minted` still gets added to `self.capitalization`, see `distribute_epoch_rewards_in_partition` at [6](#0-5) ), and the delegation-lineage metadata (`voter_pubkey`, `activation_epoch`, `deactivation_epoch`) is silently clobbered on the reused account.
- Deterministic-panic path: with `adjust_delegations_for_rent` disabled, the hard `assert_eq!` is directly attacker-triggerable and causes a validator crash — a network-wide, non-malicious-peer-required denial of service / consensus-halt condition, since every replaying validator hits the same panic on the same block.

### Likelihood Explanation
The reward-distribution window intentionally spans up to 10% of an epoch's slots (`MAX_FACTOR_OF_REWARD_BLOCKS_IN_EPOCH`, see `get_reward_distribution_num_blocks` at [7](#0-6) ), giving an attacker ample time (potentially thousands of slots) to deactivate/withdraw/re-delegate a stake account they control before their partition is serviced. No malicious validator, gossip peer, or privileged role is required — only ordinary Stake-program instructions signed by the account owner, and knowledge of which partition/block their pubkey falls into (computed deterministically from `parent_blockhash`, which is public). The `assert_eq!` crash path requires the `relax_post_exec_min_balance_check` feature to be inactive; whether this is the case on any live cluster is not verified locally, so the DoS variant's applicability is uncertain without confirming that feature's activation status.

### Recommendation
- Bind each `PartitionedStakeReward` to a unique delegation identity (e.g., include `activation_epoch`/a per-delegation nonce, or the exact `Stake` struct hash) captured at calculation time, and verify at distribution time that the live account's `Stake` still matches that identity before merging in the stale snapshot; if it doesn't match, treat it like `AccountNotFound` (burn the reward) rather than merging into an unrelated delegation.
- Replace the hard `assert_eq!` in the non-`adjust_delegations_for_rent` branch with a graceful `DistributionError` return so a state mismatch cannot panic the validator.
- Consider preventing `Withdraw`/re-`Initialize` of the exact same pubkey while a pending reward for that pubkey is still un-distributed, or snapshot-lock accounts with outstanding partitioned rewards similarly to how `pending_delegator_rewards` blocks vote-account `Withdraw` in `programs/vote/src/vote_state/mod.rs` ( [8](#0-7) ) — that existing SIMD-0123 guard is the model already used elsewhere in the codebase and should be mirrored for stake accounts.

### Proof of Concept
1. Delegate stake account `S` (keypair controlled by attacker) to validator `V1` and let it earn credits for the rewarded epoch.
2. At the epoch boundary, the calculation phase computes and stores a `PartitionedStakeReward` for `S` with some `stake_reward > 0`, to be applied in partition `k` (a later block) — see `calculate_stake_rewards_and_commissions` / `hash_rewards_into_partitions`.
3. Before block height reaches `distribution_starting_block_height + k`, attacker submits, in order: `Deactivate` (if needed), `Withdraw` (drains `S` to zero, closing it back to system-owned), then `CreateAccount`/`Initialize`/`DelegateStake` to re-open `S` with a new delegation to `V2` funded with a small amount just above rent-exemption.
4. When block height reaches the target partition, `store_stake_accounts_in_partition` → `build_updated_stake_reward` finds `S` (the new delegation) in `stakes_cache_accounts`, credits it with the old `stake_reward`, and overwrites its `Stake` with the stale delegation-plus-reward snapshot (clamped to current lamports) — the attacker receives free lamports on an account whose principal was already fully withdrawn, and (with the rent-adjustment feature off) can instead be tuned to trigger the `assert_eq!` panic in the same function, crashing the block-producing/replaying validator.

Note: I was not able to locally confirm the current activation status of the `relax_post_exec_min_balance_check` / `adjust_delegations_for_rent` feature flag on any specific network, nor find an existing guard in the stake program that blocks `Withdraw`/re-delegation of a pubkey with an outstanding partitioned reward (unlike the analogous `pending_delegator_rewards` guard on vote-account `Withdraw`). Confirming this would require running the code or a Devin session with access to feature-set activation state and the stake-program instruction handlers in full.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L744-749)
```rust
            Ok((stake_reward, commission_lamports, stake)) => {
                let inflation = InflationReward {
                    stake,
                    stake_reward,
                    commission_bps: (!custom_commission_collector).then_some(commission_bps),
                };
```

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L90-149)
```rust
        let height = self.block_height();
        if height < distribution_starting_block_height {
            return;
        }

        if let EpochRewardPhase::Calculation(status) = &status {
            // epoch rewards have not been partitioned yet, so partition them now
            // This should happen only once immediately on the first rewards distribution block, after reward calculation block.
            let epoch_rewards_sysvar = self.get_epoch_rewards_sysvar();
            let (partition_indices, partition_us) = measure_us!({
                epoch_rewards_hasher::hash_rewards_into_partitions(
                    &status.all_stake_rewards,
                    &epoch_rewards_sysvar.parent_blockhash,
                    epoch_rewards_sysvar.num_partitions as usize,
                )
            });

            // update epoch reward status to distribution phase
            self.set_epoch_reward_status_distribution(
                distribution_starting_block_height,
                Arc::clone(&status.all_stake_rewards),
                partition_indices,
            );

            datapoint_info!(
                "epoch-rewards-status-update",
                ("slot", self.slot(), i64),
                ("block_height", height, i64),
                ("partition_us", partition_us, i64),
                (
                    "distribution_starting_block_height",
                    distribution_starting_block_height,
                    i64
                ),
            );
        }

        let EpochRewardStatus::Active(EpochRewardPhase::Distribution(partition_rewards)) =
            &self.epoch_reward_status
        else {
            // We should never get here.
            unreachable!(
                "epoch rewards status is not in distribution phase, but we are trying to \
                 distribute rewards"
            );
        };

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L180-204)
```rust
        let pre_capitalization = self.capitalization();
        let (
            DistributionResults {
                stake_reward_lamports_minted,
                stake_reward_lamports_burned,
                block_reward_lamports_distributed,
                block_reward_lamports_burned,
                updated_stake_rewards,
            },
            store_stake_accounts_us,
        ) = measure_us!(self.store_stake_accounts_in_partition(partition_rewards, partition_index));

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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L249-297)
```rust
        let stake_account = stakes_cache_accounts
            .get(&partitioned_stake_reward.stake_pubkey)
            .ok_or(DistributionError::AccountNotFound)?
            .clone();

        let (mut account, stake_state): (AccountSharedData, StakeStateV2) = stake_account.into();
        let StakeStateV2::Stake(meta, stake, flags) = stake_state else {
            // StakesCache only stores accounts where StakeStateV2::delegation().is_some()
            unreachable!(
                "StakesCache entry {:?} failed StakeStateV2 deserialization",
                partitioned_stake_reward.stake_pubkey
            )
        };
        account
            .checked_add_lamports(partitioned_stake_reward.inflation.stake_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;
        account
            .checked_add_lamports(partitioned_stake_reward.block_reward)
            .map_err(|_| DistributionError::ArithmeticOverflow)?;

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
        account
            .set_state(&StakeStateV2::Stake(meta, new_stake, flags))
            .map_err(|_| DistributionError::UnableToSetState)?;
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

**File:** programs/vote/src/vote_state/mod.rs (L1084-1092)
```rust
    // Always zero until SIMD-0123 is activated.
    let pending_delegator_rewards = vote_state.pending_delegator_rewards();

    if remaining_balance == 0 {
        // SIMD-0123: vote account cannot be closed if
        // pending_delegator_rewards > 0.
        if pending_delegator_rewards > 0 {
            return Err(InstructionError::InsufficientFunds);
        }
```
