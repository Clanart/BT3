Based on the investigation, the strongest analog to the reported bug class exists in Agave's Alpenglow vote-reward accounting, where a reward "budget" is fixed at epoch start without ever being re-validated against the actually available cap (post-burn capitalization), and the code goes on to pay out the pre-committed amount regardless.

### Title
Alpenglow per-epoch inflation reward budget is paid out without re-validating against post-burn capitalization cap - ([File: runtime/src/block_component_processor/vote_reward/epoch_inflation_account_state.rs])

### Summary
`EpochInflationAccountState::new_epoch_update_account` computes and persists `max_possible_validator_reward` once, at the start of an epoch, as the inflation "budget" that will be paid out to validators for that epoch via `calculate_reward` in `vote_reward.rs`. [1](#0-0) 
This mirrors the `GorplesCoin.pendingEmissionPerSecond` defect: a value derived from `capitalization`/supply is computed and later consumed for payout, but unlike the sibling capitalization-based calculation `calculate_epoch_inflation_rewards`, the recorded budget is never re-capped against the *current* capitalization at distribution time, even though capitalization can shrink (VAT burns at epoch boundaries) between when the budget was fixed and when it is paid. [2](#0-1) 

### Finding Description
`calculate_epoch_inflation_rewards(capitalization, epoch)` is the single source of truth for how many lamports inflation is allowed to mint for stake in an epoch, as a function of the "capitalization" (i.e. current supply) at the time it's called. [2](#0-1) 
`EpochInflationAccountState::new_epoch_update_account` calls this once at the start of an epoch and persists the result (`max_possible_validator_reward`) into an off-curve system account, explicitly adding `additional_rewards` (PER — Partitioned Epoch Rewards still to be paid) to the starting capitalization so that early-epoch voters aren't shortchanged relative to later-epoch voters. [3](#0-2) 
The comment on `new_epoch_update_account` explicitly documents that capitalization keeps changing during the epoch (as PER is paid out) and this stored, fixed budget is deliberately used instead of recomputing the ceiling from the live capitalization. [4](#0-3) 

The problem is that capitalization can also *decrease* within/at the epoch boundary — for example, via VAT (vote-account-transfer) burns applied when transitioning epochs — after the budget snapshot was taken but before/while it is paid out via `calculate_reward`. `calculate_reward` blindly consumes `epoch_state.max_possible_validator_reward` to compute per-slot, per-validator payouts, with no re-check against the bank's live capitalization or a "cannot exceed maxSupply-equivalent" ceiling at payout time. [5](#0-4) 

This is directly analogous to the reported bug: `pendingEmission` recomputes and caps against `maxSupply` at the moment of minting, while `pendingEmissionPerSecond` computes the same emission rate but omits the cap, so callers relying on the uncapped function can mint beyond `maxSupply`. Here, `calculate_epoch_inflation_rewards` is the "capped, recomputed-at-use" sibling, but the actual payout path (`EpochInflationAccountState` + `calculate_reward`) uses a pre-fixed value that is never re-derived from, or capped by, the live capitalization at distribution time.

### Impact Explanation
If the fixed epoch-start budget is larger than what the post-burn/updated capitalization would allow, the bank pays out more inflation lamports than `calculate_epoch_inflation_rewards` (the authoritative, capitalization-derived rate function) would currently sanction. This is exactly the scenario the repository's own regression test constructs and asserts as expected behavior: it explicitly shows `recorded_payout > recalculated_ceiling` after a capitalization-reducing burn, and asserts that "every recorded reward lamport must still be paid" — i.e., the code intentionally pays the pre-committed amount rather than the freshly-computed ceiling. [6](#0-5) 
This is over-minting relative to the "current" inflation-rate cap, i.e. the exact broken invariant in the external report (emission exceeding the max/cap derived from current state).

### Likelihood Explanation
This path triggers on every Alpenglow epoch boundary where any capitalization-reducing event (e.g., a VAT burn) occurs between the epoch-start budget snapshot and full reward distribution — a condition the codebase's own test explicitly reproduces, indicating it is a real, reachable, and already-observed code path rather than a hypothetical one. [7](#0-6) 
No attacker/privileged action is required; it is a consequence of normal protocol operation (epoch transition + burn), matching the "unprivileged" scope required.

### Recommendation
At distribution time (inside `calculate_reward` / wherever `max_possible_validator_reward` is consumed), re-derive or clamp the payout against `Bank::calculate_epoch_inflation_rewards` using the *current* capitalization, so the persisted epoch-start budget can only be used as an upper bound intent, not an unconditionally honored floor/ceiling regardless of subsequent capitalization decreases. Alternatively, explicitly document and enforce (via a hard cap, not just a comment) that the persisted budget is authoritative and update `calculate_epoch_inflation_rewards`'s callers/comments to reflect that live-capitalization-based recomputation is intentionally bypassed, and audit whether that design choice is consistent with the intended inflation-supply invariant across burns.

### Proof of Concept
The repository's own test demonstrates the condition: [8](#0-7) 
1. Genesis with Alpenglow vote accounts and one validator; disable slot-time features.
2. Advance the bank into epoch 1; read the epoch-start recorded budget (`recorded_budget`) via `EpochInflationAccountState`.
3. Manually push epoch credits to the validator's vote account matching the largest possible payout (`recorded_payout`) achievable under that budget.
4. Call `bank.freeze()`, which burns VAT to the incinerator, reducing `capitalization()`.
5. Recompute the ceiling using the *current* (post-burn) capitalization via `calculate_epoch_inflation_rewards` — the test asserts `recorded_payout > recalculated_ceiling`, i.e., the amount that will actually be paid out exceeds what the live-capitalization-based cap would allow.
6. Advance to epoch 2 and confirm the full `recorded_payout` (not the lower `recalculated_ceiling`) is distributed — the test explicitly asserts this as "must still be paid."

This confirms the reward-distribution path pays out lamports beyond what the authoritative, capitalization-derived inflation-rate function currently permits, mirroring the "missing max-supply/cap check" defect pattern from the external report.

### Citations

**File:** runtime/src/block_component_processor/vote_reward/epoch_inflation_account_state.rs (L137-160)
```rust
    /// Computes a new version of `Self` for `bank.epoch` and serializes it into accounts in the `bank`.
    ///
    /// At the start of a new epoch, over several slots we pay the inflation rewards from the
    /// previous epoch.  This is called Partitioned Epoch Rewards (PER).  As such, the
    /// capitalization keeps increasing in the first slots of the epoch.  Vote rewards are
    /// calculated as a function of the capitalization and we do not want voting in the initial
    /// slots to earn less rewards than voting in the later rewards.  As such this function is
    /// called with [`additional_rewards`] which should be the total rewards that will
    /// be paid by PER and we use the capitalization from the previous epoch plus this value to
    /// compute the vote rewards.
    pub(crate) fn new_epoch_update_account(
        bank: &Bank,
        epoch_start_capitalization: u64,
        additional_rewards: u64,
    ) {
        let prev = Self::new_from_bank(bank).map(|s| s.current);
        let current = EpochInflationState::new_from_bank(
            bank,
            epoch_start_capitalization,
            additional_rewards,
        );
        let state = Self { prev, current };
        state.set_state(bank);
    }
```

**File:** runtime/src/bank.rs (L2966-2977)
```rust
    /// For a given `capitalization` (total_supply in lamports) and `epoch`, returns the
    /// `epoch inflation rewards` in lamports.
    pub(crate) fn calculate_epoch_inflation_rewards(
        &self,
        capitalization: u64,
        epoch: Epoch,
    ) -> u64 {
        let slot_in_year = self.slot_in_year_for_inflation();
        let validator_rate = self.inflation.read().unwrap().validator(slot_in_year);
        let epoch_duration_in_years = self.epoch_duration_in_years(epoch);
        (validator_rate * capitalization as f64 * epoch_duration_in_years) as u64
    }
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L485-511)
```rust
/// Computes the voting reward in Lamports.
///
/// Returns `(validator rewards, leader rewards)`.
fn calculate_reward(
    epoch_state: &EpochInflationState,
    total_stake_lamports: u64,
    validator_stake_lamports: u64,
) -> (u64, u64) {
    // Rewards are computed as following:
    // per_slot_inflation = epoch_validator_rewards_lamports / slots_per_epoch
    // fractional_stake = validator_stake / total_stake_lamports
    // rewards = fractional_stake * per_slot_inflation
    //
    // The code below is equivalent but changes the order of operations to maintain precision

    let numerator =
        epoch_state.max_possible_validator_reward as u128 * validator_stake_lamports as u128;
    let denominator = epoch_state.slots_per_epoch as u128 * total_stake_lamports as u128;

    // SAFETY: the result should fit in u64 because we do not expect the inflation in a single
    // epoch to exceed u64::MAX.
    let reward_lamports: u64 = (numerator / denominator).try_into().unwrap();
    // As per the Alpenglow SIMD, the rewards are split equally between the validators and the leader.
    let validator_reward_lamports = reward_lamports / 2;
    let leader_reward_lamports = reward_lamports - validator_reward_lamports;
    (validator_reward_lamports, leader_reward_lamports)
}
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2800-2886)
```rust
    #[test]
    fn test_alpenglow_partitioned_rewards_use_epoch_start_budget_after_burn() {
        let validator_keypairs = vec![genesis_utils::ValidatorVoteKeypairs::new_rand()];
        let GenesisConfigInfo {
            mut genesis_config, ..
        } = genesis_utils::create_genesis_config_with_alpenglow_vote_accounts(
            1_000_000_000 * LAMPORTS_PER_SOL,
            &validator_keypairs,
            vec![100 * LAMPORTS_PER_SOL],
        );
        genesis_config.epoch_schedule = EpochSchedule::new(SLOTS_PER_EPOCH);
        let features_to_deactivate = crate::slot_params::slot_time_feature_ids().to_vec();
        deactivate_features(&mut genesis_config, &features_to_deactivate);

        let (bank, bank_forks) =
            Bank::new_for_tests(&genesis_config).wrap_with_bank_forks_for_tests();
        let bank = Bank::new_from_parent_with_bank_forks(
            bank_forks.as_ref(),
            bank,
            SlotLeader::default(),
            SLOTS_PER_EPOCH,
        );
        assert_eq!(bank.epoch(), 1);

        let recorded_budget = EpochInflationAccountState::new_from_bank(&bank)
            .and_then(|state| state.inflation_rewards_for_epoch(bank.epoch()))
            .expect("epoch-start inflation budget must be persisted");
        // Alpenglow rewards are rounded down once per slot, so this is the largest
        // payout that can actually have been recorded during the epoch.
        let recorded_payout = recorded_budget / SLOTS_PER_EPOCH * SLOTS_PER_EPOCH;

        let vote_pubkey = validator_keypairs[0].vote_keypair.pubkey();
        let mut vote_account = bank.get_account(&vote_pubkey).unwrap();
        let VoteStateVersions::V4(mut vote_state) = vote_account
            .deserialize_data::<VoteStateVersions>()
            .unwrap()
        else {
            panic!("unexpected vote state version");
        };
        let last_credits = vote_state
            .epoch_credits
            .last()
            .map(|(_epoch, final_credits, _initial_credits)| *final_credits)
            .unwrap_or_default();
        vote_state
            .epoch_credits
            .push((bank.epoch(), last_credits + recorded_payout, last_credits));
        vote_account
            .serialize_data(&VoteStateVersions::V4(vote_state))
            .unwrap();
        bank.store_account(&vote_pubkey, &vote_account);

        // Freezing burns the VAT transferred to the incinerator at the epoch
        // boundary, reducing capitalization after the reward budget was fixed.
        bank.freeze();
        let recalculated_ceiling =
            bank.calculate_epoch_inflation_rewards(bank.capitalization(), bank.epoch());
        assert!(
            recorded_payout > recalculated_ceiling,
            "the test must reproduce a payout above the post-burn ceiling: \
             recorded_payout={recorded_payout}, recalculated_ceiling={recalculated_ceiling}"
        );

        let bank = Bank::new_from_parent(
            bank,
            SlotLeader::default(),
            SLOTS_PER_EPOCH.saturating_mul(2),
        );
        assert_eq!(bank.epoch(), 2);

        let epoch_rewards = bank.get_epoch_rewards_sysvar();
        let EpochRewardStatus::Active(EpochRewardPhase::Calculation(calculation_status)) =
            &bank.epoch_reward_status
        else {
            panic!("{:?} not active calculation", bank.epoch_reward_status);
        };
        let stake_rewards = calculation_status
            .all_stake_rewards
            .enumerated_rewards_iter()
            .map(|(_index, reward)| reward.inflation.stake_reward)
            .sum::<u64>();
        assert_eq!(epoch_rewards.total_rewards, recorded_budget);
        assert_eq!(
            epoch_rewards.distributed_rewards + stake_rewards,
            recorded_payout,
            "every recorded reward lamport must still be paid"
        );
```
