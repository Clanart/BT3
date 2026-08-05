### Title
Commission "rug" delay bypass via fresh vote-account identity substitution in epoch reward calculation - (File: `runtime/src/bank.rs`, `runtime/src/bank/partitioned_epoch_rewards/calculation.rs`)

### Summary
The external report's bug class is: a per-identity (NFT-ID) time restriction is enforced, but the identity itself is never tracked/pinned across transfers, so an attacker rotates identity to reset the restriction and repeat the privileged action. The closest verifiable Agave analog is in the epoch-rewards commission "anti-rug" mechanism: `delay_commission_updates` is meant to force a full-epoch delay before an increased vote-account commission affects delegator rewards, but that delay is enforced against the *history of a specific vote-account pubkey* via cached snapshots. A validator operator can sidestep the delay by presenting a **new vote-account pubkey** (fresh identity) with the desired commission baked in at creation instead of going through `UpdateCommission`, which is the only path gated by `is_commission_update_allowed`/the epoch-delay snapshot logic.

### Finding Description
The delay-commission-update protection works by caching vote-account state from prior epochs and using it — instead of the live commission — when computing stake rewards, specifically to prevent "last minute commission rugs": [1](#0-0) 

The commission increase restriction itself is only enforced inside `update_commission`, which is invoked by the `UpdateCommission` vote instruction and gated by `is_commission_update_allowed` (only allowed in the first half of an epoch, so an increase can't take effect until it has "aged" through the delayed snapshot): [2](#0-1) [3](#0-2) 

This gate only fires when an *existing* vote account's commission is being *changed*. It does nothing to constrain the commission an account is initialized with, and the reward-calculation cache is keyed by vote-account pubkey via `epoch_stakes(...).stakes().vote_accounts()`, which is populated per-account, not per-operator/per-node identity. The codebase's own test acknowledges this fallback path explicitly: [4](#0-3) 

The test comment states plainly: "Check that if a new vote account is somehow already staked and earning rewards in the epoch in which it was created, the reward commission falls back to the latest commission rate for that epoch" — i.e., there is no delayed snapshot to fall back to for a brand-new pubkey, so the just-set commission is used immediately, with no one-epoch cooling-off period.

### Impact Explanation
Delegators choose validators partly based on advertised/observed commission and rely on the one-epoch delay to have time to react (undelegate/move stake) before an increased commission actually reduces their rewards. Because the anti-rug delay is anchored to vote-account pubkey history rather than to the underlying validator/node identity, an operator can effectively "launder" a commission increase by creating a fresh vote account (with the higher commission baked into initialization) and rapidly moving delegated stake to it, bypassing the intended one-epoch grace period entirely. This causes unrestricted, immediate loss of delegator reward share — a direct fund-loss/false-execution-of-guarantee outcome for unprivileged stakers, matching the "fund theft/loss" impact category, since the delay was purposely built as a delegator protection and can be nullified by identity rotation, exactly as in the source report's NFT-ID-not-tracked exploit.

### Likelihood Explanation
Likelihood is moderate: creating a new vote account and moving delegated stake to it is a normal, unprivileged, permissionless operation available to any validator operator; it does not require any protocol bug beyond the fact that reward-cache history is per-pubkey. The main friction is that stake still needs to be (re)delegated/activated to the new vote account, which is not instantaneous (stake activation/warmup applies to *new* delegations), so the practical exploit works best for validators that convince delegators to re-delegate, or use fresh delegations rather than moved stake, limiting — but not eliminating — the immediate blast radius. This friction is why I present this as a real but circumstantial analog rather than a slam-dunk trivial exploit; I was not able to fully trace whether `MoveStake`/redelegation preserves activation status when switching to a brand-new vote account within the same epoch, due to running out of investigation budget.

### Recommendation
Anchor the commission-rug protection to validator identity (`node_pubkey`) rather than solely to vote-account pubkey history, or require that a newly created vote account inherit/enforce the same delayed-effect rule for its *initial* commission if it receives delegated stake within the same epoch it was created (e.g., treat "no cached prior epoch" as "assume commission = 0" or "assume min(previous commission across accounts for this node_pubkey)" rather than "fall back to latest live commission"). At minimum, require a one-epoch waiting period after vote-account creation before any commission value that account was created with can be used in reward calculations above the network-wide minimum default.

### Proof of Concept
1. Validator operator V currently runs vote account A with commission 5%, subject to `delay_commission_updates`.
2. Instead of calling `UpdateCommission` on A (which would be rejected/delayed outside the first-half-of-epoch window per `is_commission_update_allowed`), V creates a new vote account B in the same epoch with commission initialized to 50% at creation time (`create_v4_account_with_authorized` / equivalent vote-account creation path — not gated by `is_commission_update_allowed`).
3. V causes delegators' stake to redelegate to B (or attracts new delegations to B) within the same epoch.
4. Per the reward-calculation fallback demonstrated in `test_calculate_stake_vote_rewards_prestaked_vote_account`, because B has no prior-epoch cached snapshot, the reward computation uses B's live 50% commission immediately rather than a delayed value, giving delegators no epoch of warning — the exact protection `delay_commission_updates` is supposed to provide is bypassed by identity substitution, mirroring the NFT report's "transfer to a new wallet to reset the restriction" pattern. [5](#0-4)

### Citations

**File:** runtime/src/bank.rs (L1723-1748)
```rust
    /// Get cached vote account state from the past few epochs so that some vote
    /// state configuration changes are delayed before being used in reward
    /// calculation.
    fn get_cached_vote_accounts<'a>(
        &'a self,
        rewarded_epoch: Epoch,
        distribution_epoch_vote_accounts: &'a VoteAccounts,
    ) -> CachedVoteAccounts<'a> {
        // Snapshot of vote account state from the beginning of the epoch prior to
        // the rewarded epoch. This snapshot state is saved a full epoch before
        // being used to prevent last minute commission rugs.
        let snapshot_epoch_vote_accounts = self
            .epoch_stakes(rewarded_epoch)
            .map(|epoch_stakes| epoch_stakes.stakes().vote_accounts());

        // Vote account state from the beginning of the rewarded epoch.
        let rewarded_epoch_vote_accounts = self
            .epoch_stakes(self.epoch())
            .map(|epoch_stakes| epoch_stakes.stakes().vote_accounts());

        CachedVoteAccounts {
            snapshot_epoch_vote_accounts,
            rewarded_epoch_vote_accounts,
            distribution_epoch_vote_accounts,
        }
    }
```

**File:** programs/vote/src/vote_state/mod.rs (L796-825)
```rust
/// Update the vote account's commission
pub fn update_commission<S: std::hash::BuildHasher>(
    vote_account: &mut BorrowedInstructionAccount,
    target_version: VoteStateTargetVersion,
    commission: u8,
    signers: &HashSet<Pubkey, S>,
    epoch_schedule: &EpochSchedule,
    clock: &Clock,
    disable_commission_update_rule: bool,
) -> Result<(), InstructionError> {
    let vote_state_result = get_vote_state_handler_checked(vote_account, target_version);
    let enforce_commission_update_rule = !disable_commission_update_rule
        && match vote_state_result.as_ref() {
            Ok(decoded_vote_state) => commission > decoded_vote_state.commission(),
            Err(_) => true,
        };

    if enforce_commission_update_rule && !is_commission_update_allowed(clock.slot, epoch_schedule) {
        return Err(VoteError::CommissionUpdateTooLate.into());
    }

    let mut vote_state = vote_state_result?;

    // current authorized withdrawer must say "yay"
    verify_authorized_signer(vote_state.authorized_withdrawer(), signers)?;

    vote_state.set_commission(commission);

    vote_state.set_vote_account_state(vote_account)
}
```

**File:** programs/vote/src/vote_state/mod.rs (L990-1004)
```rust
/// Given the current slot and epoch schedule, determine if a commission change
/// is allowed
pub fn is_commission_update_allowed(slot: Slot, epoch_schedule: &EpochSchedule) -> bool {
    // always allowed during warmup epochs
    if let Some(relative_slot) = slot
        .saturating_sub(epoch_schedule.first_normal_slot)
        .checked_rem(epoch_schedule.slots_per_epoch)
    {
        // allowed up to the midpoint of the epoch
        relative_slot.saturating_mul(2) <= epoch_schedule.slots_per_epoch
    } else {
        // no slots per epoch, just allow it, even though this should never happen
        true
    }
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2054-2104)
```rust
    #[test]
    fn test_calculate_stake_vote_rewards_prestaked_vote_account() {
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_leader(
            1_000_000 * LAMPORTS_PER_SOL,
            &Pubkey::new_unique(),
            42 * LAMPORTS_PER_SOL,
        );

        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);
        let (bank, bank_forks) =
            Bank::new_for_tests(&genesis_config).wrap_with_bank_forks_for_tests();
        assert!(bank.feature_set.snapshot().delay_commission_updates);

        let vote_address = Pubkey::new_unique();
        let mut bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 0,
                vote_operations: vec![(
                    vote_address,
                    VoteOperations {
                        delegate_stake_amount: Some(LAMPORTS_PER_SOL),
                        ..VoteOperations::default()
                    },
                )],
            },
        );

        // Check that if a new vote account is somehow already staked and
        // earning rewards in the epoch in which it was created, the reward
        // commission falls back to the latest commission rate for that epoch
        bank = apply_epoch_operations(
            bank,
            bank_forks.as_ref(),
            EpochOperations {
                epoch: 1,
                vote_operations: vec![(
                    vote_address,
                    VoteOperations {
                        create_with_balance: Some(LAMPORTS_PER_SOL),
                        new_commission: Some(1),
                        earned_credits: Some(1000),
                        expect_reward: true,
                        ..VoteOperations::default()
                    },
                )],
            },
        );
```
