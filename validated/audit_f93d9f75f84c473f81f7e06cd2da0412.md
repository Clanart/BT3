## Analysis

I found a strong Agave analog: the Alpenglow "epoch-start inflation budget" mechanism, which — like the Cap `maxLiquidatable()` bug — fixes a "maximum"/target value from a formula that ignores a later mutation (a burn) to the underlying state, letting the same fixed budget be paid out repeatedly against a shrunken base.

### Title
Alpenglow per-slot vote rewards can mint more inflation than the post-VAT-burn capitalization ceiling allows - ([File: runtime/src/block_component_processor/vote_reward/epoch_inflation_account_state.rs])

### Summary
`EpochInflationAccountState::new_epoch_update_account` freezes `max_possible_validator_reward` for an epoch by calling `Bank::calculate_epoch_inflation_rewards(epoch_start_capitalization + additional_rewards, epoch)` once, at the epoch boundary. Per-slot Alpenglow vote rewards for the whole epoch are then paid against this single frozen ceiling. However, `maybe_burn_vat_from_staked_accounts` (the Validator Admission Ticket / VAT burn, SIMD-0357) runs at the very same epoch boundary and permanently removes lamports from every vote account's balance (and from capitalization indirectly, since the burned lamports move to the incinerator), *after* the reward ceiling for the epoch has already been fixed against the pre-burn capitalization. The reward ceiling is therefore computed on a capitalization value that does not reflect the VAT burn that concurrently occurs, exactly mirroring the Cap Finance bug where `maxLiquidatable()` computes a target using `totalDebt`/`totalDelegation` without folding in the liquidation bonus that will also change state in the same operation.

### Finding Description
`calculate_epoch_inflation_rewards(capitalization, epoch)` is the ceiling function for validator inflation rewards, and it is fed a **pre-burn capitalization snapshot** (`epoch_start_capitalization + additional_rewards`) inside `EpochInflationState::new_from_bank`: [1](#0-0) 

That value is persisted for the whole epoch and used every slot to size vote rewards, but `maybe_burn_vat_from_staked_accounts` — which runs from `update_epoch_stakes` at the epoch boundary, burning `vat_to_burn_per_epoch()` lamports out of every distribution vote account into the incinerator — is a completely separate mutation applied around the same boundary: [2](#0-1) 

The repo's own regression test proves the two are inconsistent: it computes `recorded_payout` (what the frozen epoch-start budget will actually pay based on per-slot rounding) and `recalculated_ceiling` (what `calculate_epoch_inflation_rewards` would return if computed *after* the VAT burn reduces capitalization), and explicitly asserts `recorded_payout > recalculated_ceiling`: [3](#0-2) 

The test then asserts that "every recorded reward lamport must still be paid" even though it exceeds the post-burn ceiling: [4](#0-3) 

This is structurally identical to the Cap Finance root cause: a "maximum"/target value (`maxLiquidatable` there, `max_possible_validator_reward` here) is derived from a formula (`targetHealth`/`totalDebt` there, `capitalization` here) that does not account for a second effect applied in the same operation (the liquidation `bonus` there, the VAT `burn` here) that also shrinks the very quantity the formula is meant to bound.

### Impact Explanation
The net effect is that inflation-reward issuance for an epoch is sized against a capitalization figure that is stale relative to the VAT burn that concurrently reduces the real economic base (vote-account balances / effective capitalization). This causes validators to be paid rewards computed from a larger-than-actual base, i.e. more inflation is minted into the network than the intended per-epoch inflation ceiling would allow once the burn is accounted for — a supply/accounting inconsistency at the validator-runtime level (over-issuance), not merely a rounding artifact. Because this is baked into consensus-critical reward calculation code that every validator executes identically, it does not require a malicious actor; it happens deterministically whenever Alpenglow + VAT burn interact with an epoch boundary, so it is a genuine protocol-level miscalculation of the fund/inflation invariant rather than an isolated single-node bug.

### Likelihood Explanation
This will trigger on every Alpenglow epoch transition where VAT burn is active (`feature_snapshot.alpenglow`), since `maybe_burn_vat_from_staked_accounts` runs unconditionally at each epoch boundary once Alpenglow is enabled, and `EpochInflationState::new_from_bank` always computes the ceiling from a capitalization value captured before/without the burn being folded in. The repository's own test is titled `test_alpenglow_partitioned_rewards_use_epoch_start_budget_after_burn` and is written to *demonstrate and accept* `recorded_payout > recalculated_ceiling`, confirming this is a reproducible condition under normal (non-adversarial) operation, not an edge case requiring an attacker.

### Recommendation
Compute (or re-validate) `max_possible_validator_reward` against the capitalization *after* `maybe_burn_vat_from_staked_accounts` has been applied for that epoch boundary, i.e. feed `calculate_epoch_inflation_rewards` a capitalization that already subtracts the VAT burn total, analogous to including the liquidation bonus in Cap's `maxLiquidatable` fix. Alternatively, cap the per-epoch payout at `min(epoch_start_budget, calculate_epoch_inflation_rewards(post_burn_capitalization, epoch))` so the frozen budget can never exceed what the post-burn capitalization actually justifies.

### Proof of Concept
The existing unit test in the codebase is itself the PoC: it creates an Alpenglow bank, records the epoch-start budget via `EpochInflationAccountState::new_from_bank(&bank).inflation_rewards_for_epoch(...)`, computes the maximum per-slot-rounded payout (`recorded_payout`), then calls `bank.freeze()` (which triggers the VAT burn to the incinerator, reducing capitalization) and shows `bank.calculate_epoch_inflation_rewards(bank.capitalization(), bank.epoch())` (the "true" post-burn ceiling) is strictly less than `recorded_payout`, while the subsequent epoch's reward distribution still pays out the full pre-burn `recorded_payout`: [5](#0-4) 

**Caveat:** I could not fully trace `calculate_epoch_inflation_rewards`'s implementation body in `runtime/src/bank.rs` within the available search budget (only its call sites/signature were located, not its full source), so I cannot cite the exact formula internals. The tool-call budget was exhausted before I could pull that function definition directly; a Devin session with full file access should read `runtime/src/bank.rs` around the `calculate_epoch_inflation_rewards` definition to confirm the precise inflation-rate formula and validate the exact fix location.

### Citations

**File:** runtime/src/block_component_processor/vote_reward/epoch_inflation_account_state.rs (L47-62)
```rust
impl EpochInflationState {
    fn new_from_bank(
        bank: &Bank,
        epoch_start_capitalization: u64,
        additional_rewards: u64,
    ) -> Self {
        let max_possible_validator_reward = bank.calculate_epoch_inflation_rewards(
            epoch_start_capitalization + additional_rewards,
            bank.epoch(),
        );
        EpochInflationState {
            max_possible_validator_reward,
            slots_per_epoch: bank.epoch_schedule.slots_per_epoch,
            epoch: bank.epoch(),
        }
    }
```

**File:** runtime/src/bank.rs (L2644-2694)
```rust
    /// Burn the Validator Admission ticket from each vote account if Alpenglow is enabled
    ///
    /// Note: This must ONLY be called after the vote accounts have been filtered (`clone_and_filter_for_vat`)
    /// to the top `MAX_ALPENGLOW_VOTE_ACCOUNTS` that contain enough balance for admission.
    fn maybe_burn_vat_from_staked_accounts(&mut self, epoch_stakes: &VersionedEpochStakes) {
        let feature_snapshot = self.feature_set.snapshot();
        if !feature_snapshot.alpenglow {
            return;
        }

        let vat_to_burn_per_epoch = self.vat_to_burn_per_epoch();
        let vote_accounts = epoch_stakes.stakes().vote_accounts();
        debug_assert!(vote_accounts.len() <= 2000);
        // +1 for the incinerator account
        let mut accounts_to_store: Vec<(Pubkey, AccountSharedData)> =
            Vec::with_capacity(vote_accounts.len() + 1);
        let mut total_vat = 0u64;

        // Vote accounts have already been filtered by clone_and_filter_for_vat to only include
        // accounts with non-zero stake and sufficient balance.
        for (vote_pubkey, _stake) in vote_accounts.delegated_stakes() {
            let mut account = self.get_account(vote_pubkey).unwrap();
            total_vat += vat_to_burn_per_epoch;
            account.set_lamports(
                account
                    .lamports()
                    .checked_sub(vat_to_burn_per_epoch)
                    .expect(
                        "Vote accounts should have already been filtered to contain enough \
                         balance for the VAT",
                    ),
            );
            accounts_to_store.push((*vote_pubkey, account));
        }

        // Per SIMD-0357, transfer collected VAT to the incinerator account.
        let mut incinerator_account = self.get_account(&incinerator::id()).unwrap_or_default();
        incinerator_account.set_lamports(
            incinerator_account
                .lamports()
                .checked_add(total_vat)
                .unwrap(),
        );
        accounts_to_store.push((incinerator::id(), incinerator_account));

        self.store_accounts((self.slot, accounts_to_store.as_slice()), None);
        info!(
            "Transferred total VAT of {total_vat} lamports to incinerator from staked vote \
             accounts"
        );
    }
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L2800-2887)
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
    }
```
