## Title
Reward-per-slot division can produce a value exceeding `u64::MAX`, panicking the bank during Alpenglow per-slot reward accrual - (File: `runtime/src/block_component_processor/vote_reward.rs`)

### Summary
The external report describes a reward-index accumulator (`RewardsDistributor`) that divides emitted rewards by a token's `totalSupply`; when the denominator is dust, the accumulated value overflows a fixed-width integer and permanently DoS's reward accrual. Agave's Alpenglow per-slot validator/leader reward calculation has the analogous shape: it divides a numerator scaled by `validator_stake_lamports` by a denominator scaled by `total_stake_lamports`, and the result is force-cast into `u64` with an `.unwrap()` that will panic rather than saturate if the division's result exceeds `u64::MAX`.

### Finding Description
`calculate_reward` computes per-slot inflation rewards as: [1](#0-0) 

```rust
let numerator =
    epoch_state.max_possible_validator_reward as u128 * validator_stake_lamports as u128;
let denominator = epoch_state.slots_per_epoch as u128 * total_stake_lamports as u128;

// SAFETY: the result should fit in u64 because we do not expect the inflation in a single
// epoch to exceed u64::MAX.
let reward_lamports: u64 = (numerator / denominator).try_into().unwrap();
```

This mirrors the reported bug-class exactly: a reward figure is divided by a "supply-like" denominator (`total_stake_lamports`, analogous to `totalBalance`/`totalSupply` in the report) and the quotient is force-converted into a fixed-size integer with an unchecked/unwrap conversion instead of a saturating or checked cast. The `SAFETY` comment explicitly documents an *assumption* ("we do not expect the inflation in a single epoch to exceed u64::MAX") rather than an enforced invariant — exactly the same class of "should never happen, but the formula does not defensively prevent it" reasoning the report calls out for `RewardsDistributor.sol`.

`total_stake_lamports` is `self.total_stake`, taken from `epoch_stakes.total_stake()` for the relevant epoch: [2](#0-1) 

There is no lower bound / dust-guard check on `total_stake` or on `validator_stake_lamports` before the division, unlike `calculate_block_reward` elsewhere in the codebase which explicitly documents and clamps a similar overflow risk: [3](#0-2) 

```rust
// During recalculation, if stake account has already received rewards,
// it's possible to have `stake > total_active_stake`. If
// `pending_delegator_rewards` is a huge number, we could potentially
// overflow a `u64`. ... This is harmless in practice, but we
// clamp it just to be safe
(pending_delegator_rewards as u128 * stake as u128 / total_active_stake as u128)
    .try_into()
    .unwrap_or(u64::MAX)
    .min(pending_delegator_rewards)
```

That sibling function uses `.unwrap_or(u64::MAX)` (a saturating fallback) precisely because the authors recognized the overflow risk. `calculate_reward` in `vote_reward.rs`, however, uses a bare `.unwrap()`, so if `numerator / denominator > u64::MAX` the process panics instead of saturating.

### Impact Explanation
`calc_vote_rewards_update_vote_states` is invoked from bank/block reward processing to pay out per-slot validator and leader rewards under Alpenglow. A panic inside `calculate_reward` propagates up through `RewardState::calculate_reward` → `update_account` → `update_accounts` → `calc_vote_rewards_update_vote_states`, which is called unconditionally as part of block/slot processing. A panic here crashes bank/replay processing on every validator that processes the affected slot, which is a consensus-halt-class failure (all nodes executing this code path abort), not merely a single node's degraded service.

### Likelihood Explanation
Triggering `numerator / denominator > u64::MAX` requires `total_stake_lamports` to be extremely small relative to `epoch_state.max_possible_validator_reward * validator_stake_lamports / slots_per_epoch`. This is plausible during low-stake conditions (e.g., early cluster bootstrap, testnets/devnets, or a cluster that has undergone a mass de-stake), where `total_stake` (denominator) can be very small while `max_possible_validator_reward` (numerator, an inflation-driven quantity independent of the currently staked amount) remains comparatively large. This does not require a malicious peer or admin action — it is a natural consequence of low total network stake, exactly analogous to the report's "totalSupply is dust" precondition. I was not able to fully verify the concrete numeric bounds of `epoch_state.max_possible_validator_reward` and `slots_per_epoch` in this codebase to construct exact trigger values, so the precise likelihood (how "dust" total_stake needs to be) is uncertain and should be validated with concrete cluster-state values.

### Recommendation
Replace the bare `.try_into().unwrap()` in `calculate_reward` (`runtime/src/block_component_processor/vote_reward.rs:506`) with a saturating conversion consistent with the pattern already used in `calculate_block_reward` (`runtime/src/bank/partitioned_epoch_rewards/calculation.rs:227-230`), e.g. `.unwrap_or(u64::MAX)`, and/or add an explicit floor/guard on `total_stake_lamports` before performing the division so a panic can never occur regardless of how small total active stake becomes.

### Proof of Concept
Not independently executable from the index alone; conceptually:
1. Construct (or reach, via natural network conditions) an epoch where `epoch_stakes.total_stake()` is very small (dust) while `epoch_state.max_possible_validator_reward` remains a large, inflation-derived constant for that epoch.
2. Have a validator with non-trivial `validator_stake_lamports` receive a reward-slot credit in this epoch (via `ValidatedRewardCert`/`reward_validators`).
3. During `calc_vote_rewards_update_vote_states` → `RewardState::calculate_reward`, `numerator / denominator` (using u128 arithmetic) evaluates to a value `> u64::MAX`, causing `.try_into().unwrap()` at `vote_reward.rs:506` to panic, aborting bank/replay processing on every node executing this slot.

I could not fully confirm from the indexed code alone the exact numeric feasibility (real bounds on `max_possible_validator_reward`, `slots_per_epoch`, and realistic minimum `total_stake` in a live/test cluster) — a Devin session with full repo access and the ability to compute/simulate `EpochInflationState` values would be needed to confirm concrete trigger parameters.

### Citations

**File:** runtime/src/block_component_processor/vote_reward.rs (L190-234)
```rust
        let bank_slot = bank.slot();
        let epoch_stakes = bank.epoch_stakes_from_slot(reward_slot).ok_or(
            RewardStateError::MissingEpochStakes {
                reward_slot,
                bank_slot,
            },
        )?;
        let accounts = epoch_stakes.stakes().vote_accounts().as_ref();
        let total_stake = epoch_stakes.total_stake();
        // This assumes that if the epoch_schedule ever changes, the new schedule will maintain correct
        // info about older slots as well.
        let reward_epoch = bank.epoch_schedule.get_epoch(reward_slot);
        let epoch_inflation_state = {
            let epoch_inflation_account_state = EpochInflationAccountState::new_from_bank(bank);
            // This function should only be called after alpenglow is active and the slot in the the epoch
            // that activated Alpenglow should have created the account.
            debug_assert!(epoch_inflation_account_state.is_some());
            epoch_inflation_account_state
                .ok_or(RewardStateError::MissingEpochInflationAccountState { bank_slot })?
                .get_epoch_state(reward_epoch)
                .ok_or(RewardStateError::NoEpochValidatorStake {
                    reward_epoch,
                    bank_slot,
                })?
        };
        let migration_epoch =
            get_migration_epoch(bank).ok_or(RewardStateError::GenesisCertNotFound {
                reward_slot,
                bank_slot,
            })?;
        let reward_slot_timestamp_ns =
            calc_slot_timestamp(bank, reward_slot, block_producer_time_nanos);
        Ok(Self {
            reward_slot_timestamp_ns,
            current_epoch: bank.epoch(),
            reward_slot,
            reward_validators,
            bank_slot,
            leader_vote_pubkey: bank.leader().vote_address,
            accounts,
            total_stake,
            epoch_inflation_state,
            migration_epoch,
        })
    }
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L493-510)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L221-230)
```rust
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
```
