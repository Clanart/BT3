## Analysis

The seed bug's broken invariant: a "last processed epoch/height" watermark is only advanced on the branch that pays out; when the payable amount is computed as zero, the function returns early without moving the watermark, so a later payout computes its span against a stale baseline and pays for a bigger span than it should.

The same shape exists in `pallet-consensus-incentives`.

### Title
`LastRewardedHeight` watermark is not advanced when a state machine's reward is zero, letting a subsequent reward calculation overpay from a stale baseline - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary

`Pallet::process_message` only updates the `LastRewardedHeight` watermark inside the branch that actually transfers a reward: [1](#0-0) 

```rust
if let Some(block_cost) = StateMachinesCostPerBlock::<T>::get(state_machine_id) {
    let reward = Self::calculate_reward(&state_machine_id, block_cost)?;
    if reward.is_zero() {
        return Ok(());          // <-- watermark NOT advanced
    }
    ... transfer ...
    LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
        *watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
    });
}
```

`calculate_reward` derives the reward as `(latest_height − baseline) * block_cost`, where `baseline` falls back to `LastRewardedHeight` and only to `host.previous_commitment_height()` if no watermark was ever recorded: [2](#0-1) 

### Finding Description

Whenever `block_cost == 0` for a `state_machine_id` (e.g. governance temporarily pauses rewards for a chain via `StateMachinesCostPerBlock`, mirroring the seed report's `setAllowedAsset(asset, false)` toggle), `calculate_reward` always returns `0`, and `process_message` returns before touching `LastRewardedHeight`. Consensus proofs for that state machine keep landing and `latest_commitment_height` keeps advancing during this window, but the pallet's own bookkeeping of "how far we've already paid" is frozen at whatever height it was before the pause.

When governance later re-enables a non-zero `block_cost` for the same state machine, the very next relayer to deliver a consensus update triggers `calculate_reward` with `baseline = LastRewardedHeight` still pinned to the pre-pause height. `blocks = latest_height − baseline` now spans the entire pause window plus the resumed period, and the reward is `blocks * new_block_cost` — a lump sum sized as if every one of those blocks had always been priced at the new cost, even though they were explicitly configured to be free (or unconfigured) during the pause.

This is structurally identical to the `BondNFT.distribute()` bug: a per-unit-time index (`epoch[tigAsset]` there, `LastRewardedHeight` here) that is supposed to track "up to when have we already accounted for this asset/chain" is skipped exactly when the current-period value is zero, letting the state drift stale and causing an unrelated future actor to be paid/credited for a span they didn't actually earn during the window that state should have reflected.

### Impact Explanation

The reward is paid straight out of the treasury account (`T::TreasuryAccount`) to whichever relayer happens to submit the first consensus update after the resume: [3](#0-2) 

That single relayer collects a reward sized for the entire frozen span (pause duration + any additional advance), which is fund loss from the treasury / an incorrect-amount payout to a single beneficiary rather than the intended "0 during pause, then per-block afterwards" schedule. This falls in the "loss of funds" / "logic attack causing wrong amount" bucket the bounty accepts.

### Likelihood Explanation

Triggering the stale-baseline state only needs a governance-level cost change (setting `StateMachinesCostPerBlock` to `0` and later to non-zero for the same chain), which is a normal operational lever exposed by this pallet, not a compromise of any trust assumption — directly analogous to the original report's `setAllowedAsset` toggle, which the C4 judges still accepted as Medium severity because it is an ordinary state transition, not an attack requiring a malicious admin. Any relayer (an unprivileged, permissionless role — anyone can submit consensus proofs) can be the one to reap the inflated reward simply by being first to relay after the cost is restored; no relayer collusion or privileged access is required to *collect* the miscomputed reward, only the ordinary pre-existing governance lever to *create* the stale state.

### Recommendation

Advance `LastRewardedHeight` to `state_machine_height.height` on every processed update for a configured state machine, independent of whether the computed reward is zero — mirroring the C4 mitigation of updating `epoch[tigAsset]` even when `totalShares[_tigAsset] == 0`. Only the actual token transfer/mint should be skipped when `reward == 0`; the height bookkeeping must still move forward so a later non-zero-cost period only prices blocks that occur after that point, not the entire frozen span.

### Proof of Concept

1. Governance sets `StateMachinesCostPerBlock[X] = C > 0`; relayer R1 delivers proofs normally, `LastRewardedHeight[X]` tracks along.
2. Governance sets `StateMachinesCostPerBlock[X] = 0` (a routine pause). Consensus for `X` continues to be relayed and `latest_commitment_height` advances by `N` blocks while paused; `process_message` runs each time, computes `reward = 0`, and returns before touching `LastRewardedHeight[X]` (line ranges cited above).
3. Governance sets `StateMachinesCostPerBlock[X] = C` again.
4. Any relayer (say R2) submits the next consensus update for `X`. `calculate_reward` computes `baseline = LastRewardedHeight[X]` (still the pre-pause height), `blocks = latest_height − baseline` (includes the whole paused span `N` plus whatever advanced post-resume), and pays `blocks * C` from the treasury to R2 in one shot — a payout never intended to accrue during the zero-cost window.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L46-73)
```rust
		if let Some(block_cost) = StateMachinesCostPerBlock::<T>::get(state_machine_id) {
			let reward = Self::calculate_reward(&state_machine_id, block_cost)?;

			if reward.is_zero() {
				return Ok(());
			}

			T::Currency::transfer(
				&T::TreasuryAccount::get().into_account_truncating(),
				&relayer_account,
				reward,
				Preservation::Expendable,
			)
			.map_err(|_| Error::<T>::RewardTransferFailed)?;

			Self::deposit_event(Event::<T>::RelayerRewarded {
				relayer: relayer_account.clone(),
				amount: reward,
				state_machine_height,
			});

			T::ReputationAsset::mint_into(&relayer_account, reward.saturated_into())
				.map_err(|_| Error::<T>::ReputationMintFailed)?;

			LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
				*watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
			});
		}
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L78-99)
```rust
	fn calculate_reward(
		state_machine_id: &StateMachineId,
		block_cost: <T as pallet_ismp::Config>::Balance,
	) -> Result<<T as pallet_ismp::Config>::Balance, Error<T>> {
		let host = <T::IsmpHost>::default();
		let latest_height = host
			.latest_commitment_height(state_machine_id.clone())
			.map_err(|_| Error::<T>::CouldNotGetStateMachineHeight)?;
		let previous_height =
			host.previous_commitment_height(state_machine_id.clone()).unwrap_or_default();

		// Use the rewarded watermark as the baseline and fall back to the previous height until
		// the first reward is recorded for this chain. The watermark only moves forward, so a
		// height that is rolled back and later resubmitted is not paid for a second time.
		let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);

		let blocks = latest_height.saturating_sub(baseline);

		let blocks_as_balance: <T as pallet_ismp::Config>::Balance = blocks.saturated_into();
		let reward = blocks_as_balance.saturating_mul(block_cost);

		Ok(reward)
```
