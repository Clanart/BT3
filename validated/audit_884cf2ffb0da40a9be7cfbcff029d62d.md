### Title
Consensus-incentive rewards pay out an unpaid block backlog at the *current* `block_cost` rather than the rate in force when each block was finalized - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`pallet-consensus-incentives` pays relayers for advancing a state machine's finalized height, scoped to the span between a persisted watermark (`LastRewardedHeight`) and the newly reported `latest_height`. The reward for that whole span is computed with a single, currently-configured `block_cost`, not the cost that was in effect when each block in the span was actually finalized. This is the same broken invariant as the external report: a mutable "current rate" parameter is retroactively and uniformly applied across a batch of historical units that may have accrued under a different rate.

### Finding Description
`calculate_reward` computes the reward as: [1](#0-0) 

```rust
let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);
let blocks = latest_height.saturating_sub(baseline);
let blocks_as_balance: <T as pallet_ismp::Config>::Balance = blocks.saturated_into();
let reward = blocks_as_balance.saturating_mul(block_cost);
```

`block_cost` is fetched from `StateMachinesCostPerBlock` at call time [2](#0-1)  — the value currently stored, with no record of what the rate was for each block already accrued in the unpaid backlog `[baseline, latest_height)`. `LastRewardedHeight` only tracks a height watermark, never the rate that applied at each height [3](#0-2) .

`update_cost_per_block` simply overwrites the map entry with no snapshotting of the old rate and no requirement to first flush/pay out any pending backlog at the old rate before the new one takes effect [4](#0-3) . So if blocks 1000→2000 for a state machine went unrewarded (e.g., because no relayer submitted a consensus update while `LastRewardedHeight` sat behind `latest_height`, or the reward path failed and only the watermark update was skipped) and the rate changes from 100 to 100,000 in between, the next relayer to deliver a consensus proof collects `1000 * 100,000` from the treasury for a backlog that was only ever supposed to be worth `1000 * 100` under the old rate.

This directly mirrors the external report's structure: `checkHoldUp`/`getComplianceTransferableTokens` apply a single "current" lock period to every historical issuance instead of the lock period effective at each issuance's own time; here, a single "current" `block_cost` is applied to every historical block in the backlog instead of the cost effective at each block's own finalization time.

### Impact Explanation
This is a direct treasury fund-loss / incorrect-amount payout bug: `T::Currency::transfer` moves funds from the treasury account to the relayer using an amount computed from a mismatched (post-update) rate [5](#0-4) . A permissionless, otherwise-honest relayer that simply happens to be the first to submit a consensus message after a rate increase captures an inflated reward for blocks that finalized under the old, lower rate — an "unauthorized"-sized transfer to the rightful category of recipient (a relayer) but for the wrong amount, draining treasury funds beyond what governance intended to pay for that span. Conversely, a rate decrease underpays relayers for work already done under the higher rate, which is a loss for relayers rather than the protocol, but still a wrong-amount settlement.

### Likelihood Explanation
Triggering this needs no compromised or malicious governance/relayer: a normal, benign `update_cost_per_block` call (a routine parameter tune, analogous to the "Alice moves country" scenario in the report) combined with any backlog of unpaid blocks (which can arise naturally whenever relayers are slow, a batch has multiple `StateMachineUpdated` events, or the reward transfer previously failed silently since `process_message`'s `Err` is discarded with `let _ =` in `on_executed`) is sufficient. The claimant is any unprivileged relayer who happens to deliver the next consensus proof — no special access or timing manipulation is required beyond normal operation.

### Recommendation
Snapshot the `block_cost` alongside height whenever it changes (e.g., a `Vec<(height, cost)>` schedule per state machine, or force a reward flush at the old rate for `[baseline, current_latest_height)` inside `update_cost_per_block` before installing the new rate), and have `calculate_reward` integrate the correct historical rate over each sub-range of `[baseline, latest_height)` instead of multiplying the whole span by a single current-time rate.

### Proof of Concept
1. Governance calls `update_cost_per_block(state_machine_id, 100)`.
2. State machine advances from height 1000 to height 2000 with no relayer submitting a consensus update in between that resets `LastRewardedHeight` (or several `StateMachineUpdated` events land in one batch while `LastRewardedHeight` lags) — the reward backlog sits unpaid, `LastRewardedHeight = 1000`.
3. Governance later calls `update_cost_per_block(state_machine_id, 100_000)` (routine repricing).
4. A relayer delivers a fresh consensus proof; `on_executed` → `process_message` → `calculate_reward` computes `blocks = latest_height(2000) - baseline(1000) = 1000` and reward `= 1000 * 100_000`, transferred from the treasury via `T::Currency::transfer` [5](#0-4) , even though 1000 of those blocks were only ever priced at 100 under the old rate — a 1000x overpayment for that portion of the backlog, paid entirely at claim time regardless of when each block actually finalized.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L46-47)
```rust
		if let Some(block_cost) = StateMachinesCostPerBlock::<T>::get(state_machine_id) {
			let reward = Self::calculate_reward(&state_machine_id, block_cost)?;
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L53-59)
```rust
			T::Currency::transfer(
				&T::TreasuryAccount::get().into_account_truncating(),
				&relayer_account,
				reward,
				Preservation::Expendable,
			)
			.map_err(|_| Error::<T>::RewardTransferFailed)?;
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L86-99)
```rust
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

**File:** modules/pallets/consensus-incentives/src/lib.rs (L81-86)
```rust
	/// The highest height a relayer has already been paid for, per state machine. Rewards only
	/// ever cover the span above this watermark, so a height that is revisited after a rollback
	/// is not paid for twice.
	#[pallet::storage]
	pub type LastRewardedHeight<T: Config> =
		StorageMap<_, Blake2_128Concat, StateMachineId, u64, OptionQuery>;
```

**File:** modules/pallets/consensus-incentives/src/lib.rs (L130-150)
```rust
		/// Update cost per block for a state machine
		#[pallet::call_index(0)]
		#[pallet::weight(T::WeightInfo::update_cost_per_block())]
		pub fn update_cost_per_block(
			origin: OriginFor<T>,
			state_machine_id: StateMachineId,
			cost_per_block: <T as pallet_ismp::Config>::Balance,
		) -> DispatchResult {
			T::IncentivesOrigin::ensure_origin(origin)?;

			StateMachinesCostPerBlock::<T>::mutate(state_machine_id.clone(), |maybe_cost| {
				*maybe_cost = Some(cost_per_block);
			});

			Self::deposit_event(Event::<T>::StateMachineCostPerBlockUpdated {
				state_machine_id,
				cost_per_block,
			});

			Ok(())
		}
```
