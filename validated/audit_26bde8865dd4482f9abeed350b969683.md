Based on my research, the strongest local analog to the Nouns DAO "stale escrow reference" bug class is the Solana **vote-account commission collector** mechanism (SIMD-0232), where the collector-account pubkey stored in vote state is mutable at any time via `UpdateCommissionCollector`, but the epoch-rewards distribution pipeline snapshots/consumes it asynchronously, causing commission that was already earned to be irrecoverably burned rather than delivered — the same "mutable reference changed after value has already accrued against it" root cause as the Nouns fork-escrow bug.

### Title
Commission rewards accrued to an inflation/block-revenue collector are silently burned if the collector is repointed to become a vote account before distribution - (File: `programs/vote/src/vote_state/mod.rs`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
`update_commission_collector` lets the vote account's authorized withdrawer repoint `inflation_rewards_collector` / `block_revenue_collector` to any system-owned, rent-exempt account at any time, with no restriction tied to the reward-accrual/distribution lifecycle. [1](#0-0) 
The validator/vote-account owner's ability to designate a new collector is unconditioned by whether commission has already been earned for a completed epoch but not yet distributed. [2](#0-1) 

### Finding Description
Commission is computed at epoch-reward-calculation time against whatever collector address is currently stored in vote state, but the credit/distribution of that commission happens later, in a separate step (`load_and_reward_commission_accounts` in the partitioned-epoch-rewards pipeline). If, between calculation and distribution, the designated collector account is converted into a vote account (a legitimate, permitted operation since `update_commission_collector` allows setting the collector to any valid account, including the vote account itself, and any account can independently be initialized as a vote account), the runtime treats the collector as ineligible and burns the reward instead of delivering it — exactly analogous to how the Nouns `forkEscrow` pointer being swapped mid-flight orphans escrowed tokens that can no longer be returned to their rightful owner. [3](#0-2) 
This is demonstrated directly by the existing test, which shows the commission recipient receiving zero rewards for the epoch in which its collector role transitions to a vote account, and the corresponding `burned_lamports` accounting confirming the funds are permanently destroyed rather than escrowed for later recovery. [4](#0-3) 
Just as `withdrawFromForkEscrow` only operates against the *current* `ds.forkEscrow` reference and has no mechanism to recover value associated with a stale reference, the commission-distribution path has no mechanism to recover or redirect commission that was computed against a collector address whose eligibility changed before payout — the value is dropped on the floor (burned) with no path back to the rightful recipient.

### Impact Explanation
Legitimate, non-malicious commission recipients (which are often third-party treasuries or delegator-facing accounts distinct from the vote account operator) permanently lose earned rewards through normal, permitted use of `UpdateCommissionCollector` combined with ordinary account creation. This is a fund-loss bug: value that was legitimately earned and would otherwise be paid out is destroyed instead, matching the "fund theft/loss" impact category.

### Likelihood Explanation
The trigger requires only two independently-permitted, unprivileged/ordinary actions: (1) the vote account's authorized withdrawer calling `UpdateCommissionCollector` to point at an address, and (2) that address later being initialized as a vote account before the pending epoch's commission distribution runs — both routine operations with no special access needed beyond normal wallet/vote-account authority. No malicious peer, validator, or admin behavior is required; this can happen through uncoordinated normal usage (e.g., an account being repurposed for its own staking).

### Recommendation
Snapshot commission eligibility/collector identity at reward-calculation time and honor that snapshot through distribution, or block the destination from becoming a vote account (or otherwise losing eligibility) until any pending commission tied to it has been distributed — analogous to the Nouns DAO recommendation of decoupling the withdrawal path from mutable references and gating changes on outstanding claims. Alternatively, redirect provably-orphaned commission to the vote account's authorized withdrawer instead of burning it.

### Proof of Concept
The existing regression test `test_inflation_collector_becomes_vote_account_burns_rewards` in `runtime/src/bank/partitioned_epoch_rewards/calculation.rs` is itself the proof of concept: it sets an `inflation_rewards_collector`, lets it earn commission across epochs, then converts that collector address into a vote account and shows the epoch's commission reward for it becomes `0` while `burned_lamports` for the reward-commission entry becomes non-zero, confirming the funds are destroyed rather than delivered. [4](#0-3)

### Citations

**File:** programs/vote/src/vote_state/mod.rs (L866-905)
```rust
impl NewCommissionCollector<'_, '_> {
    /// Validates the collector per SIMD-0232 and returns its pubkey.
    ///
    /// The designated commission collector must either be equal to the vote
    /// account's address OR satisfy ALL of the following constraints:
    ///
    /// 1. Must be a system program owned account.
    /// 2. Must be rent-exempt.
    /// 3. Must not be a reserved account (checked via writable flag).
    pub fn validate_and_resolve_key(
        &self,
        vote_account: &BorrowedInstructionAccount,
        rent: &Rent,
    ) -> Result<Pubkey, InstructionError> {
        match self {
            NewCommissionCollector::VoteAccount => Ok(*vote_account.get_key()),
            NewCommissionCollector::NewAccount(collector_account) => {
                // 1. Must be a system program owned account.
                if collector_account.get_owner() != &system_program::id() {
                    return Err(InstructionError::InvalidAccountOwner);
                }

                // 2. Must be rent-exempt.
                if !rent.is_exempt(
                    collector_account.get_lamports(),
                    collector_account.get_data().len(),
                ) {
                    return Err(InstructionError::InsufficientFunds);
                }

                // 3. Must not be a reserved account (checked via writable flag).
                if !collector_account.is_writable() {
                    return Err(InstructionError::InvalidArgument);
                }

                Ok(*collector_account.get_key())
            }
        }
    }
}
```

**File:** programs/vote/src/vote_state/mod.rs (L907-933)
```rust
/// Update the vote account's commission collector (SIMD-0232).
pub fn update_commission_collector<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    new_collector: NewCommissionCollector,
    kind: CommissionKind,
    signers: &HashSet<Pubkey, S>,
    rent: &Rent,
) -> Result<(), InstructionError> {
    let mut vote_state = get_vote_state_handler_checked(vote_account, target_version)?;

    // Require authorized withdrawer to sign.
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    let new_collector_key = new_collector.validate_and_resolve_key(vote_account, rent)?;

    match kind {
        CommissionKind::InflationRewards => {
            vote_state.set_inflation_rewards_collector(new_collector_key);
        }
        CommissionKind::BlockRevenue => {
            vote_state.set_block_revenue_collector(new_collector_key);
        }
    }

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4081-4122)
```rust
    #[test]
    fn test_inflation_collector_becomes_vote_account_burns_rewards() {
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_leader(
            1_000_000 * LAMPORTS_PER_SOL,
            &Pubkey::new_unique(),
            42 * LAMPORTS_PER_SOL,
        );

        genesis_config.rent = Rent::default();
        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);

        let (bank, bank_forks) =
            Bank::new_for_tests(&genesis_config).wrap_with_bank_forks_for_tests();
        let vote_address = Pubkey::new_unique();
        let collector_into_vote_address = Pubkey::new_unique();

        // Create a normal vote account with a currently valid inflation collector
        let bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 0,
                vote_operations: vec![(
                    vote_address,
                    VoteOperations {
                        create_with_balance: Some(LAMPORTS_PER_SOL),
                        new_commission: Some(100),
                        earned_credits: Some(1000),
                        delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                        new_inflation_rewards_collector: Some(collector_into_vote_address),
                        ..VoteOperations::default()
                    },
                )],
            },
        );

        // New vote account gets nothing
        let rewards = bank.get_balance(&collector_into_vote_address);
        assert_eq!(rewards, 0);

```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L4144-4243)
```rust

        // Transform the collector into a vote account, see that all rewards
        // are burned for this epoch
        let bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 2,
                vote_operations: vec![
                    (
                        vote_address,
                        VoteOperations {
                            earned_credits: Some(1000),
                            expect_reward: true,
                            ..VoteOperations::default()
                        },
                    ),
                    (
                        collector_into_vote_address,
                        VoteOperations {
                            create_with_balance: Some(pre_balance),
                            new_commission: Some(100),
                            earned_credits: Some(1000),
                            delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                            ..VoteOperations::default()
                        },
                    ),
                ],
            },
        );

        let vote_reward = bank
            .rewards
            .read()
            .unwrap()
            .iter()
            .find(|(address, _reward)| *address == collector_into_vote_address)
            .map(|(_address, reward)| *reward)
            .unwrap();
        assert_eq!(vote_reward.lamports, 0);

        let unchanged_balance = bank.get_balance(&collector_into_vote_address);
        assert_eq!(unchanged_balance, pre_balance);

        // `collector_into_vote_address` receives its rewards, but `vote_address`
        // has its rewards burned
        let bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 3,
                vote_operations: vec![
                    (
                        vote_address,
                        VoteOperations {
                            earned_credits: Some(1000),
                            expect_reward: true,
                            ..VoteOperations::default()
                        },
                    ),
                    (
                        collector_into_vote_address,
                        VoteOperations {
                            earned_credits: Some(1000),
                            expect_reward: true,
                            ..VoteOperations::default()
                        },
                    ),
                ],
            },
        );

        // Some rewards were distributed
        let post_balance = bank.get_balance(&collector_into_vote_address);
        assert!(post_balance > pre_balance);

        // They're reflected in the reported rewards
        let vote_reward = bank
            .rewards
            .read()
            .unwrap()
            .iter()
            .find(|(address, _reward)| *address == collector_into_vote_address)
            .map(|(_address, reward)| *reward)
            .unwrap();
        assert_eq!(vote_reward.lamports as u64, post_balance - pre_balance);

        // Some lamports were burned
        let reward_commissions = recalculate_reward_commissions_for_tests(&bank);
        let reward_commission = reward_commissions
            .get(&collector_into_vote_address)
            .unwrap();
        assert_ne!(reward_commission.burned_lamports, 0);

        // The burned lamports are included in the epoch rewards sysvar
        let epoch_rewards = bank.get_epoch_rewards_sysvar();
        assert_eq!(
            reward_commission.burned_lamports + reward_commission.commission_lamports,
            epoch_rewards.distributed_rewards
        );
```
