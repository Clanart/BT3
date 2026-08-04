### Title
Governance rate change lets a delayed consensus-update claim overpay relayer rewards from treasury - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`pallet-consensus-incentives` pays relayers a reward equal to `(latest_height - baseline) × block_cost`, where `block_cost` is read from `StateMachinesCostPerBlock` **at claim time**, not from the rate(s) that were actually in effect while each block in that span accrued. This is the same class of bug as the external report: a value representing accrued-over-time compensation is computed by multiplying an elapsed span by the *current* rate parameter instead of the rate that was in force historically, so a parameter change skews the result relative to what should have been paid.

### Finding Description
`calculate_reward` in [1](#0-0)  computes:

```
blocks = latest_height - baseline   // baseline = LastRewardedHeight or previous_commitment_height
reward = blocks * block_cost        // block_cost = StateMachinesCostPerBlock::get(state_machine_id) NOW
```

`block_cost` is a governance-mutable parameter updated via `update_cost_per_block`, with no history or per-height snapshotting: [2](#0-1) . The reward watermark (`LastRewardedHeight`) only tracks *how many blocks* are owed, never *at what rate* they accrued: [3](#0-2) .

Because a state-machine's commitment height can advance across many blocks between consensus-update submissions (submission cadence is entirely a function of when a relayer/anyone submits a `ConsensusMessage` — not a privileged or malicious actor, just ordinary operational timing), the elapsed unrewarded span can be large. If governance raises `cost_per_block` (e.g., a legitimate re-pricing to reflect network conditions) while a large unrewarded span is outstanding, the very next consensus update to land is rewarded for the **entire backlog** at the **new, higher** rate:

```rust
LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
    *watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
});
``` [4](#0-3) 

There is no per-block rate history, no cap on `blocks` before a rate change is applied, and no re-baselining triggered by `update_cost_per_block` itself — the pallet simply multiplies the full backlog by whatever `block_cost` value happens to be live when `process_message` runs. This mirrors exactly the report's root cause: "interest accrual is always calculated using the last calculated variables... [but the getter] calculates rates again," except here it's the opposite direction — the *payout* function recomputes using the *current* rate over a historical span instead of applying the rate(s) that were actually in effect during that span.

### Impact Explanation
This directly causes unauthorized/incorrect movement of treasury funds: `T::Currency::transfer` pays `reward` from `TreasuryAccount` to the relayer [5](#0-4) , and the amount is provably wrong whenever `cost_per_block` changes while a backlog exists. A rate increase drains excess funds from the treasury to whichever account happens to deliver the next update (no proof of who did the work during which sub-period is required — the whole backlog goes to one payee), which is a direct fund-loss/overpayment bug on a live economic surface, not merely a display/getter cosmetic issue. Conversely, a rate decrease underpays relayers for work performed under the old (higher) rate, which is an economic loss to the relayer, though less severe since it doesn't drain protocol funds.

### Likelihood Explanation
This requires no malicious peer, relayer, or prover — it triggers under entirely ordinary conditions: (1) governance periodically re-prices `cost_per_block` for a state machine (a documented, expected operation), and (2) submission of consensus updates is not synchronized to rate changes (network conditions, relayer downtime, or simple submission latency naturally create backlogs of tens to hundreds of blocks). Any relayer who happens to submit the next update after a rate change benefits or loses purely by timing, with no attacker action needed — this is Medium-to-High likelihood given governance rate updates are an expected, recurring maintenance operation on this pallet.

### Recommendation
Snapshot the rate applicable to each unrewarded span rather than reading the live `block_cost` at claim time. Options: (a) when `update_cost_per_block` is called, force-settle/checkpoint the currently accrued reward for all state machines at the old rate before switching to the new rate (write `LastRewardedHeight` forward and pay out or record the accrued amount at the old rate first), or (b) store `(height, rate)` change history and integrate the reward as a sum over rate-segments instead of a single `blocks * current_rate` multiplication.

### Proof of Concept
1. Governance sets `StateMachinesCostPerBlock[chain_X] = 10` via `update_cost_per_block`.
2. No consensus update for `chain_X` lands for 1,000 blocks (ordinary network/relayer latency — `LastRewardedHeight` stays at height `H`).
3. Governance legitimately re-prices and calls `update_cost_per_block(chain_X, 1000)` (100x increase) — nothing in the pallet reacts to this by checkpointing the backlog.
4. A relayer submits the next `ConsensusMessage` for `chain_X`, advancing `latest_commitment_height` to `H + 1000`.
5. `on_executed` → `process_message` → `calculate_reward` computes `blocks = 1000`, reads `block_cost = 1000` (the new rate), and pays `reward = 1,000,000` from the treasury [6](#0-5)  — instead of the `10,000` (1000 blocks × old rate 10) that should have accrued for that historical span, a 100x overpayment straight from the treasury for a single delivered message.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L46-59)
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
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L70-72)
```rust
			LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
				*watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
			});
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L77-99)
```rust
	/// Calculate the reward for a message based on the state machine id
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
