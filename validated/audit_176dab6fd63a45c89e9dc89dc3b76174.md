## Title
Stake-hopping into `pending_delegator_rewards` lets an attacker capture undeserved SIMD-0123 block-revenue rewards without warmup delay - (File: `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
Solana's SIMD-0123 block-revenue-sharing mechanism accumulates a per-vote-account pool, `pending_delegator_rewards`, that is deposited over time (e.g. via `DepositDelegatorRewards`) and later paid out to that vote account's delegators, weighted by each delegator's stake at the reward-epoch boundary. Because a *fully-active* stake account can be redelegated to a different vote account without going through warmup/cooldown, an attacker can move already-active stake onto a vote account that has a large accumulated `pending_delegator_rewards` balance just before the epoch-boundary snapshot, collect a share of the reward pool proportional to that stake, and move away again — exactly analogous to the Burve "deposit right before compounding, withdraw right after" fee-sniping pattern in the source report.

### Finding Description
`calculate_block_reward()` distributes a vote account's `pending_delegator_rewards` to each delegator proportionally to `stake / total_active_stake`, where both `stake` and `total_active_stake` are taken from a snapshot fixed at the reward-epoch boundary (`RewardEpochDelegatedStakes`): [1](#0-0) 

`pending_delegator_rewards` itself is a persistent pool inside `VoteStateV4` that accumulates lamports (deposited via `DepositDelegatorRewards`) until it is drawn down by `calculate_block_reward`: [2](#0-1) [3](#0-2) 

The weighting snapshot (`reward_epoch_delegated_stakes`) is computed once per epoch transition from the *current* `StakesCache` state, using each delegation's activation status as of the ending epoch: [4](#0-3) 

Critically, `upsert_stake_delegation` (invoked whenever a stake account's delegation changes, e.g., re-delegating to a new vote account) recomputes the delegated stake for the *new* voter using `delegation_effective_stake` at the *current* epoch. If the redelegated stake account was already fully activated (not itself in warmup), its `activation_epoch` is unchanged by a vote-account switch, so `delegation_effective_stake` immediately returns the **full** stake amount for the new voter with no warmup delay: [5](#0-4) 

This is explicitly demonstrated by `test_stakes_change_delegate`, which shows a fully-active stake account instantly transferring its *entire* effective stake weight to a new vote account the moment the delegation is switched, without any warmup period: [6](#0-5) 

Combining these facts: an attacker holding a large, fully-activated stake account can, in the last slot of the current reward epoch, redelegate that stake to a vote account `V` that has accumulated a large `pending_delegator_rewards` balance (from block-revenue deposits earned by *other* long-standing delegators over prior epochs/slots). Because the redelegation is instantaneous (no warmup), the attacker's full stake is counted in `V`'s `RewardEpochDelegatedStakes` snapshot taken at the epoch boundary, entitling the attacker to a proportional share of `V`'s entire `pending_delegator_rewards` pool — despite having contributed zero blocks/participation towards earning it. After the reward is paid out in the following epoch's partitioned distribution, the attacker can redelegate away again (also instant), completing the sandwich.

This is the direct analog of the Burve bug: a shared, slowly-accumulated reward pool (`pending_delegator_rewards` ≈ Burve's stuck/unconverted fee balance) is paid out based on a point-in-time deposit/weight snapshot (the epoch-boundary stake snapshot ≈ Burve's `collectAndCalcCompound()` trigger), and an attacker can time their "deposit" (stake redelegation) to arrive immediately before that snapshot and withdraw immediately after, capturing value that should have accrued to the pool's long-term contributors.

The existing guard the code carefully implements — `test_recalculate_alpenglow_rewards_after_partial_distribution_uses_original_denominator` — only protects against re-running the calculation *after* a snapshot restore mid-distribution; it does not address the initial, one-time epoch-boundary snapshot itself being gamed by an instantaneous stake redelegation performed by an ordinary, unprivileged user just before the epoch boundary.

### Impact Explanation
This allows theft of value that other delegators earned: `pending_delegator_rewards` represents block revenue that should be split among stakers who backed a validator while it produced blocks and revenue. An attacker with sufficient already-active stake (which they can freely move between validators at no cost or delay) can extract a share of another vote account's accumulated reward pool without having borne any of the risk/opportunity cost of delegating to that validator during the period the rewards were earned. This is an unprivileged fund-diversion vector directly affecting the fairness/integrity of stake rewards distribution in `runtime` — the same "false distribution/acceptance of value" class as the Burve finding.

### Likelihood Explanation
Likelihood depends on (a) `pending_delegator_rewards` balances being non-trivial for some vote accounts (plausible under active SIMD-0123 block-revenue sharing) and (b) the redelegation being truly warmup-free when switching an already fully-active delegation, as shown by `upsert_stake_delegation` and `test_stakes_change_delegate`. Since large stakers can move stake between validators at will (this is a normal, permitted operation with no signature from the vote account owner required beyond the stake authority), the barrier to executing this timing attack is low — it only requires knowing when the epoch boundary will land and having a large amount of already-active, unlocked stake.

### Recommendation
Weight `pending_delegator_rewards` payouts by a delegator's *time-integrated* participation with the specific vote account (e.g., require a minimum holding period for the delegation-to-voter pairing before it counts toward that voter's `pending_delegator_rewards` distribution), or snapshot delegator-to-voter assignment at the start of the accrual period rather than only at the reward-epoch boundary, so that a last-moment redelegation cannot claim a share of rewards it did not help earn.

### Proof of Concept
1. Validator `V`'s vote account accumulates `pending_delegator_rewards = X` over several epochs (via `DepositDelegatorRewards`), owed to `V`'s long-standing delegators.
2. Attacker holds a large, fully-active stake account `S` delegated to an unrelated vote account with near-zero pending rewards.
3. Immediately before the epoch boundary (last slot of the current epoch), the attacker submits `Delegate(S, V)`. Per `upsert_stake_delegation`/`delegation_effective_stake`, `S`'s full stake amount instantly counts toward `V` with no warmup, because `S`'s `activation_epoch` did not change.
4. At the epoch boundary, `compute_new_epoch_caches_and_rewards` snapshots `RewardEpochDelegatedStakes` for `V`, including the attacker's freshly redelegated full-weight stake — see `runtime/src/stakes.rs:434-502` and `runtime/src/bank.rs:1750-1814`.
5. `calculate_block_reward()` computes the attacker's share of `V`'s `pending_delegator_rewards` as `pending_delegator_rewards * attacker_stake / total_active_stake` — see `runtime/src/bank/partitioned_epoch_rewards/calculation.rs:213-231`. Because `attacker_stake` is now large relative to `V`'s pre-existing delegator base, the attacker captures a disproportionate share of `X`.
6. After the partitioned reward is credited in the following epoch, the attacker redelegates `S` away again (also instant, no cooldown for a stake account not being deactivated), completing the "sandwich" and repeating against another vote account with an accrued pool.

### Citations

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L188-231)
```rust
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

**File:** programs/vote/src/vote_state/handler.rs (L190-209)
```rust
    pub(crate) fn pending_delegator_rewards(&self) -> u64 {
        match &self.target_state {
            TargetVoteState::V4(v4) => v4.pending_delegator_rewards,
        }
    }

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

**File:** programs/vote/src/vote_state/mod.rs (L936-988)
```rust
pub fn deposit_delegator_rewards<S: std::hash::BuildHasher>(
    invoke_context: &mut InvokeContext,
    vote_account_index: IndexOfAccount,
    sender_account_index: IndexOfAccount,
    deposit: u64,
    signers: &HashSet<Pubkey, S>,
) -> Result<(), InstructionError> {
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;

    let vote_address = *instruction_context.get_key_of_instruction_account(vote_account_index)?;
    let source_address =
        *instruction_context.get_key_of_instruction_account(sender_account_index)?;

    // Source account must sign the transfer.
    verify_authorized_signer(&source_address, signers)?;

    // SIMD-0123 states we must validate the vote account deserializes to a v4
    // *before* attempting CPI, then update the `pending_delegator_rewards`
    // field *last*.
    // We can deserialize it, and hold onto the deserialized payload in-memory.
    // This way, we can drop the account borrow but avoid re-deserializing
    // later, since we know only lamports will change.
    let mut vote_state = {
        let vote_account =
            instruction_context.try_borrow_instruction_account(vote_account_index)?;

        // Can't use `get_vote_state_handler_checked`, since it will convert
        // the underlying vote state to v4.
        // SIMD-0123 requires an *initialized v4*.
        let versioned = VoteStateVersions::deserialize(vote_account.get_data())?;
        if let VoteStateVersions::V4(vote_state_v4) = versioned {
            Ok(VoteStateHandler::new_v4(*vote_state_v4))
        } else {
            Err(InstructionError::InvalidAccountData)
        }
    }?;

    // CPI to System: Transfer from sender to vote account.
    invoke_context.native_invoke_signed(
        system_instruction::transfer(&source_address, &vote_address, deposit),
        &[],
    )?;

    // Update `pending_delegator_rewards`.
    let transaction_context = &invoke_context.transaction_context;
    let instruction_context = transaction_context.get_current_instruction_context()?;
    let mut vote_account =
        instruction_context.try_borrow_instruction_account(vote_account_index)?;

    vote_state.add_pending_delegator_rewards(deposit)?;
    vote_state.set_vote_account_state(&mut vote_account)
}
```

**File:** runtime/src/stakes.rs (L434-502)
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
        let mut stake_history = self.stake_history.clone();
        stake_history.add(self.epoch, stake_history_entry);
        // Refresh the stake distribution of vote accounts for the next epoch,
        // using new stake history.
        let (vote_accounts, delegated_stakes) = refresh_vote_accounts(
            thread_pool,
            next_epoch,
            &self.vote_accounts,
            stake_delegations,
            &stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        let reward_epoch_delegated_stakes = RewardEpochDelegatedStakes {
            epoch: self.epoch,
            delegated_stakes: effective_delegated_stakes,
        };
        (
            stake_history,
            vote_accounts,
            delegated_stakes,
            reward_epoch_delegated_stakes,
        )
    }
```

**File:** runtime/src/stakes.rs (L620-660)
```rust
    fn upsert_stake_delegation(
        &mut self,
        stake_pubkey: Pubkey,
        stake_account: StakeAccount,
        new_rate_activation_epoch: Option<Epoch>,
        use_fixed_point_stake_math: bool,
    ) {
        debug_assert_ne!(stake_account.lamports(), 0u64);
        let delegation = stake_account.delegation();
        let voter_pubkey = delegation.voter_pubkey;
        let stake = delegation_effective_stake(
            delegation,
            self.epoch,
            &self.stake_history,
            new_rate_activation_epoch,
            use_fixed_point_stake_math,
        );
        match self.stake_delegations.insert(stake_pubkey, stake_account) {
            None => {
                self.add_delegated_stake(voter_pubkey, stake);
                self.vote_accounts.add_stake(&voter_pubkey, stake);
            }
            Some(old_stake_account) => {
                let old_delegation = old_stake_account.delegation();
                let old_voter_pubkey = old_delegation.voter_pubkey;
                let old_stake = delegation_effective_stake(
                    old_delegation,
                    self.epoch,
                    &self.stake_history,
                    new_rate_activation_epoch,
                    use_fixed_point_stake_math,
                );
                if voter_pubkey != old_voter_pubkey || stake != old_stake {
                    self.sub_delegated_stake(&old_voter_pubkey, old_stake);
                    self.add_delegated_stake(voter_pubkey, stake);
                    self.vote_accounts.sub_stake(&old_voter_pubkey, old_stake);
                    self.vote_accounts.add_stake(&voter_pubkey, stake);
                }
            }
        }
    }
```

**File:** runtime/src/stakes.rs (L1080-1136)
```rust
    #[test]
    fn test_stakes_change_delegate() {
        let stakes_cache = StakesCache::new(Stakes {
            epoch: 4,
            ..Stakes::default()
        });
        let rent = Rent::default();

        let ((vote_pubkey, vote_account), (stake_pubkey, stake_account)) =
            create_staked_node_accounts(10, &rent);

        let ((vote_pubkey2, vote_account2), (_stake_pubkey2, stake_account2)) =
            create_staked_node_accounts(10, &rent);

        stakes_cache.check_and_store(&vote_pubkey, &vote_account, None, true);
        stakes_cache.check_and_store(&vote_pubkey2, &vote_account2, None, true);

        // delegates to vote_pubkey
        stakes_cache.check_and_store(&stake_pubkey, &stake_account, None, true);

        let stake = stake_account
            .deserialize_data::<StakeStateV2>()
            .unwrap()
            .stake()
            .unwrap();

        {
            let stakes = stakes_cache.stakes();
            let vote_accounts = stakes.vote_accounts();
            assert!(vote_accounts.get(&vote_pubkey).is_some());
            let expected_stake =
                effective_stake(&stake, stakes.epoch, &stakes.stake_history, None, true);
            assert_eq!(
                vote_accounts.get_delegated_stake(&vote_pubkey),
                expected_stake
            );
            assert!(vote_accounts.get(&vote_pubkey2).is_some());
            assert_eq!(vote_accounts.get_delegated_stake(&vote_pubkey2), 0);
        }

        // delegates to vote_pubkey2
        stakes_cache.check_and_store(&stake_pubkey, &stake_account2, None, true);

        {
            let stakes = stakes_cache.stakes();
            let vote_accounts = stakes.vote_accounts();
            assert!(vote_accounts.get(&vote_pubkey).is_some());
            assert_eq!(vote_accounts.get_delegated_stake(&vote_pubkey), 0);
            assert!(vote_accounts.get(&vote_pubkey2).is_some());
            let expected_stake =
                effective_stake(&stake, stakes.epoch, &stakes.stake_history, None, true);
            assert_eq!(
                vote_accounts.get_delegated_stake(&vote_pubkey2),
                expected_stake
            );
        }
    }
```
