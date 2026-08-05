Based on my research, I found a strong analog in the Alpenglow block-reward distribution path, which mirrors the "totalSupply()==0 causes rewards to be permanently stuck" pattern from the report.

### Title
Alpenglow block-reward accrual loses `pending_delegator_rewards` forever when a validator has zero active delegated stake at reward time - (File: runtime/src/bank/partitioned_epoch_rewards/calculation.rs)

### Summary
`calculate_block_reward()` computes each stake delegation's share of a vote account's `pending_delegator_rewards` pool as `pending_delegator_rewards * stake / total_active_stake`, exactly mirroring the reported `rewardRate / totalSupply` accrual pattern. When `total_active_stake == 0` for the reward epoch, the function returns `0` for every delegation, and there is no code path that redistributes, defers, or refunds that vote account's accumulated `pending_delegator_rewards` for the skipped epoch.

### Finding Description
`calculate_block_reward` fetches the denominator from `reward_epoch_delegated_stakes.delegated_stakes` (the total active stake delegated to that vote account for the rewarded epoch) and explicitly special-cases zero: [1](#0-0) 

This is structurally identical to `rewardPerToken()`'s `if (totalSupply() == 0) return rewardPerTokenStored;` guard in the report: the numerator (`pending_delegator_rewards`, the accrued lamport pool) is never zeroed or reduced, but the denominator being zero means the accrual loop that is supposed to consume it (`block_reward` computation feeding `block_reward_lamports_distributed`) skips entirely for that epoch.

The pool value itself, `pending_delegator_rewards`, is stored directly on the vote account and only mutated by `add_pending_delegator_rewards` (called from `deposit_delegator_rewards`, a CPI-only path used by block-revenue-sharing/MEV deposits): [2](#0-1) 

There is no corresponding decrement of `pending_delegator_rewards` anywhere in the reward-calculation or distribution code (`calculate_block_reward`, `distribute_epoch_rewards_in_partition`, `store_stake_accounts_in_partition`) that I could find — the field is only ever read via `vote_state.pending_delegator_rewards()` when computing each delegation's share, never subtracted by the amount actually paid out: [3](#0-2) 

If a vote account has `pending_delegator_rewards > 0` but `total_active_stake == 0` for the rewarded epoch (e.g., all delegators have deactivated/withdrawn their stake, or the delegated-stake snapshot for that epoch records zero for this voter — `unwrap_or(0)` on a missing map entry), every one of that vote account's stake delegations computes `block_reward = 0` for that epoch. Because `pending_delegator_rewards` is not cleared/reduced when this happens, the reward-per-epoch attribution for that period is permanently lost for anyone who was staked during that window — funds that were already deposited into the vote account (and thus already backed by real lamports sitting in the vote account) are never attributed to any delegator's `block_reward` and there is no recovery mechanism (no withdraw-and-redistribute, no rollover token bookkeeping tied to the specific epoch's shortfall). This is the same "undistributed rewards get silently orphaned when the denominator is zero" defect as the C4 report, adapted to Agave's block-revenue-sharing (SIMD-0123) reward accrual.

### Impact Explanation
Delegators to a vote account lose their proportional share of already-deposited block-revenue-sharing rewards for any epoch in which the vote account's tracked active delegated stake is zero, even though the lamports back the `pending_delegator_rewards` figure and sit in the vote account. This is a fund-loss/fund-misattribution bug consistent with "Medium" severity in the original report — no attacker action is required, and no privileged/malicious actor assumption is needed; it triggers from ordinary state transitions (e.g., transient full-deactivation of stake, or a missing entry in the epoch's delegated-stake snapshot).

### Likelihood Explanation
This requires `AlpenglowEpochType::Alpenglow`/`MigrationEpoch` to be active (block-revenue-sharing feature `block_revenue_sharing`), and a vote account to have nonzero `pending_delegator_rewards` while its `reward_epoch_delegated_stakes` snapshot shows `0` (or is missing an entry) for that voter's total active stake in the rewarded epoch. This can plausibly occur transiently for newly created/re-delegated vote accounts, or vote accounts whose delegated stake fully cools down/deactivates right at an epoch boundary, since `get()... .unwrap_or(0)` silently treats "no snapshot entry" the same as "zero total stake."

### Recommendation
When `total_active_stake == 0` for a vote account with `pending_delegator_rewards > 0`, do not silently drop the period's attribution: either (a) retain the reward in `pending_delegator_rewards` (do not treat it as "consumed" for that epoch, and re-attempt attribution once stake becomes active) with an explicit accounting so it's never double counted, or (b) explicitly track and refund/redistribute the amount, verified against an invariant such as `sum(block_reward across stakes) == pending_delegator_rewards` (bounded/consumed), rather than allowing `0` to flow through unaccounted.

### Proof of Concept
The existing unit test already demonstrates the exact zero-denominator branch, showing `get_block_reward_for_test(0, 0, 0, 0) == 0` and more generally that any nonzero `pending_delegator_rewards` combined with `total_stake == 0` yields `0` reward with no side effect that reduces `pending_delegator_rewards`: [4](#0-3) [5](#0-4) 
Extending this harness to set `pending_delegator_rewards = 1_000_000` with `total_stake = 0` and asserting the vote account's `pending_delegator_rewards` field remains unchanged after the epoch's reward cycle would confirm the loss (the code path to clear/decrement it does not exist in `distribution.rs`, only lamports transferred to the vote account via `deposit_delegator_rewards` are read, never subtracted).

**Note on uncertainty**: I was not able to fully trace whether a separate, un-indexed code path (e.g., in `vote_reward.rs`'s `RewardState`/`FinalCertState` handling, or block-revenue distribution logic outside `partitioned_epoch_rewards/`) subsequently reconciles or decrements `pending_delegator_rewards` after block rewards are distributed, since parts of `runtime/src/block_component_processor/vote_reward*.rs` were only partially visible in the index. If such a reconciliation exists elsewhere and correctly rolls unclaimed amounts forward, this finding would be mitigated. Given the size limits on the codebase index, I'd recommend a Devin session with full repo access to confirm whether `pending_delegator_rewards` is ever decremented independent of the `block_reward` calculation described above.

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

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4246-4318)
```rust
    fn get_block_reward_for_test(
        individual_stake: u64,
        total_stake: u64,
        pending_delegator_rewards: u64,
        rewarded_epoch: u64,
    ) -> u64 {
        let voter_pubkey = Pubkey::new_unique();
        let vote_account = {
            let identity = Keypair::new();
            let bls_keypair =
                BLSKeypair::derive_from_signer(&identity, BLS_KEYPAIR_DERIVE_SEED).unwrap();
            let (bls_pubkey, bls_pop) = create_bls_proof_of_possession(&voter_pubkey, &bls_keypair);
            let vote_init = VoteInitV2 {
                node_pubkey: identity.pubkey(),
                authorized_voter: identity.pubkey(),
                authorized_voter_bls_pubkey: bls_pubkey,
                authorized_voter_bls_proof_of_possession: bls_pop,
                ..VoteInitV2::default()
            };
            let mut vote_state = VoteStateV4::new(
                &vote_init,
                &voter_pubkey,
                &identity.pubkey(),
                &Clock::default(),
            );
            vote_state.pending_delegator_rewards = pending_delegator_rewards;
            let mut account = solana_account::AccountSharedData::new(
                1_000_000_000,
                VoteStateV4::size_of(),
                &solana_vote_program::id(),
            );
            account
                .serialize_data(&VoteStateVersions::new_v4(vote_state))
                .unwrap();
            VoteAccount::try_from(account).unwrap()
        };

        let vote_accounts = [(voter_pubkey, (total_stake, vote_account))]
            .into_iter()
            .collect();
        let ag_epoch_type = AlpenglowEpochType::Alpenglow {
            migration_epoch: 0,
            reward_epoch_delegated_stakes: RewardEpochDelegatedStakes {
                epoch: rewarded_epoch,
                delegated_stakes: [(voter_pubkey, total_stake)].into_iter().collect(),
            },
        };

        let delegation = Delegation {
            voter_pubkey,
            stake: individual_stake,
            activation_epoch: u64::MAX, // boostrap stake so it's fully active
            ..Default::default()
        };

        let mut stake_history = StakeHistory::default();
        for epoch in 0..=rewarded_epoch {
            stake_history.add(epoch, StakeHistoryEntry::with_effective(total_stake));
        }

        let use_fixed_point_stake_math = true;
        let new_warmup_cooldown_rate_epoch = Some(0);

        calculate_block_reward(
            rewarded_epoch,
            &delegation,
            &stake_history,
            &vote_accounts,
            &ag_epoch_type,
            new_warmup_cooldown_rate_epoch,
            use_fixed_point_stake_math,
        )
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4320-4332)
```rust
    #[test]
    fn test_calculate_block_reward_specific() {
        // get nothing
        assert_eq!(get_block_reward_for_test(0, 0, 0, 0), 0);
        // get everything
        assert_eq!(get_block_reward_for_test(1, 1, 1, 0), 1);
        // individual stake higher than block reward, capped
        assert_eq!(get_block_reward_for_test(2, 1, 1, 0), 1);
        // not truncated
        assert_eq!(get_block_reward_for_test(1, 10, 10, 0), 1);
        // truncated
        assert_eq!(get_block_reward_for_test(1, 10, 9, 0), 0);
    }
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

**File:** runtime/src/bank/partitioned_epoch_rewards/distribution.rs (L380-408)
```rust
            let stake_pubkey = partitioned_stake_reward.stake_pubkey;
            let stake_reward_amount = partitioned_stake_reward.inflation.stake_reward;
            let block_reward_amount = partitioned_stake_reward.block_reward;

            match Self::build_updated_stake_reward(
                self.epoch,
                stake_history,
                new_warmup_cooldown_rate_epoch,
                stakes_cache_accounts,
                partitioned_stake_reward,
                rent,
                adjust_delegations_for_rent,
                use_fixed_point_stake_math,
            ) {
                Ok(stake_reward) => {
                    stake_reward_lamports_minted += stake_reward_amount;
                    block_reward_lamports_distributed += block_reward_amount;
                    updated_stake_rewards.push(stake_reward);
                }
                Err(err) => {
                    error!(
                        "bank::distribution::store_stake_accounts_in_partition() failed for \
                         {stake_pubkey}, {stake_reward_amount} lamports burned: {err:?}"
                    );
                    stake_reward_lamports_burned += stake_reward_amount;
                    block_reward_lamports_burned += block_reward_amount;
                }
            }
        }
```
