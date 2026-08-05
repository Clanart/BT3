### Title
Unguarded division by `total_stake` in Alpenglow per-slot voting-reward calculation can panic the validator - (File: runtime/src/block_component_processor/vote_reward.rs)

### Summary
The Revert bug class is: a configuration value that is legitimately allowed to be updated by normal (non-malicious) operation is later used as a divisor in a critical accounting path without a zero-check, causing a hard revert/panic that breaks core protocol functions (liquidations). The closest Agave analog is the Alpenglow per-slot reward calculation, where `total_stake` — a value fetched from `EpochStakes` — is used directly as a divisor in `calculate_reward()` with no defensive zero-check, unlike the equivalent block-reward path (`calculate_block_reward`) and the Tower points path (`calculate_alpenglow_points`), both of which explicitly guard against a zero denominator.

### Finding Description
In `RewardState::try_new`, `total_stake` is pulled straight from `bank.epoch_stakes_from_slot(reward_slot).total_stake()`: [1](#0-0) 

That `total_stake` value is passed unchecked into `calculate_reward()`, which uses it as part of the divisor for a `u128` division: [2](#0-1) 

```
let denominator = epoch_state.slots_per_epoch as u128 * total_stake_lamports as u128;
let reward_lamports: u64 = (numerator / denominator).try_into().unwrap();
```

There is no check that `total_stake_lamports != 0` before this division. Compare this to the two sibling reward-calculation code paths in the same codebase that use total/aggregate stake as a divisor and both explicitly guard against zero:

- `calculate_block_reward` (block-revenue-sharing path) checks `if total_active_stake == 0 { 0 } else { ... }` before dividing: [3](#0-2) 

- `calculate_alpenglow_points` (Tower/points path) explicitly filters out a zero stake denominator and returns a hard error instead of dividing: [4](#0-3) 

The `calculate_reward()` function used for per-slot Alpenglow validator/leader rewards is the odd one out: it has no such guard. `slots_per_epoch` is always non-zero, but `total_stake_lamports` (aggregate stake across all epoch validators) is not defensively checked here even though the analogous code elsewhere in the same reward-calculation subsystem treats a zero aggregate-stake denominator as an expected, handle-able condition rather than an invariant that can never occur.

### Impact Explanation
If `EpochStakes::total_stake()` for a given epoch is ever `0` — for example transiently during bootstrap/test/edge-case cluster configurations, or via any future code path that can produce an `EpochStakes` snapshot with no non-zero stake before this function is reached — `calculate_reward()` performs an unchecked division by zero on a `u128`, which panics. This function is invoked from `RewardState::calculate_reward` → `update_account` → `update_accounts` → `calc_vote_rewards_update_vote_states`, which is called as part of normal per-slot Alpenglow bank/reward processing. A panic in this path during block/slot processing would crash or halt the validator process handling that slot, i.e. a "false execution/rooting acceptance"-adjacent /consensus-processing crash, matching the spirit of the sibling guarded code (which was clearly written with the understanding that a zero denominator here is possible and must be handled, not merely asserted away).

### Likelihood Explanation
Likelihood is difficult to fully confirm from static analysis alone: whether `total_stake` can genuinely reach `0` in a live cluster at the exact slot this function executes depends on invariants enforced upstream in `EpochStakes`/`stakes_cache` construction that were not fully traced in this session (e.g., genesis/bootstrap validator requirements, minimum stake requirements). The existence of near-identical explicit zero-guards in the two sibling reward functions in the very same subsystem (`calculate_block_reward` and `calculate_alpenglow_points`) is strong evidence that the codebase authors did not consider zero aggregate stake to be provably impossible for these computations — otherwise those guards would be unnecessary. This is a normal-operation/non-malicious-admin-analog scenario (config/state naturally reaching a degenerate value), not a "malicious validator" assumption, and is consistent with the Reject-list exclusions (no privileged/malicious actor required).

### Recommendation
Add an explicit `total_stake_lamports == 0` guard to `calculate_reward()` in `runtime/src/block_component_processor/vote_reward.rs`, mirroring the pattern already used in `calculate_block_reward` (return `(0, 0)` or otherwise skip reward calculation) and `calculate_alpenglow_points` (treat as an unrepresentable/error state to be safely handled), rather than relying on an implicit invariant that `total_stake_lamports` is always non-zero. This closes the gap between the assumption made here and the more defensive/explicit handling already used for equivalent denominators elsewhere in the same reward-calculation subsystem.

### Proof of Concept
Static PoC (no dynamic run performed in this session): construct a call to `calculate_reward(&epoch_state, /*total_stake_lamports=*/0, validator_stake_lamports)` directly:
```rust
// runtime/src/block_component_processor/vote_reward.rs
let epoch_state = EpochInflationState { max_possible_validator_reward: 100, slots_per_epoch: 432_000, ..Default::default() };
calculate_reward(&epoch_state, 0, 1000); // denominator = slots_per_epoch * 0 = 0 -> u128 division by zero -> panic
```
This directly exercises the unguarded `numerator / denominator` at [5](#0-4)  and panics, in contrast to `calculate_block_reward`'s explicit `total_active_stake == 0` short-circuit at [3](#0-2) . Whether `RewardState::try_new` can actually supply `total_stake == 0` from `epoch_stakes.total_stake()` in a live cluster is the remaining open question that would require deeper tracing of `EpochStakes` construction/validation code not completed in this session.

### Citations

**File:** runtime/src/block_component_processor/vote_reward.rs (L191-198)
```rust
        let epoch_stakes = bank.epoch_stakes_from_slot(reward_slot).ok_or(
            RewardStateError::MissingEpochStakes {
                reward_slot,
                bank_slot,
            },
        )?;
        let accounts = epoch_stakes.stakes().vote_accounts().as_ref();
        let total_stake = epoch_stakes.total_stake();
```

**File:** runtime/src/block_component_processor/vote_reward.rs (L488-510)
```rust
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
```

**File:** runtime/src/bank/partitioned_epoch_rewards/calculation.rs (L206-213)
```rust
    let total_active_stake = reward_epoch_delegated_stakes
        .delegated_stakes
        .get(&vote_pubkey)
        .copied()
        .unwrap_or(0);
    if total_active_stake == 0 {
        0
    } else {
```

**File:** runtime/src/inflation_rewards/points.rs (L280-300)
```rust
    let earned_points = if earned_credits == 0 || stake_amount == 0 {
        0
    } else {
        let Some(total_stake) = reward_epoch_delegated_stakes
            .delegated_stakes
            .get(&stake.delegation.voter_pubkey)
            .copied()
            .filter(|stake| *stake != 0)
        else {
            record_error(format!(
                "AG delegated stake denominator for vote_pubkey={} in epoch={} failed",
                stake.delegation.voter_pubkey, reward_epoch_delegated_stakes.epoch
            ));
            return Err(CalculatedStakePoints {
                tower_points: 0,
                ag_points: 0,
                new_credits_observed,
                force_credits_update_with_skipped_reward: true,
            });
        };
        earned_credits * stake_amount / total_stake as u128
```
