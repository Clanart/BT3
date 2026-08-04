This is the closest verifiable analog to the LoopFi bug: **a reward calculation that multiplies an entire unpaid time/height span by the *current* rate parameter instead of splitting the span across the rate that was actually in effect for each sub-interval.**

### Title
Consensus-incentives reward pays the entire unrewarded height span at the current `cost_per_block`, mispricing rewards across a rate change - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives` pays relayers `(latest_height - baseline) * cost_per_block` for delivering consensus updates. `cost_per_block` is read once, at claim time, via `StateMachinesCostPerBlock::<T>::get`, and applied to the *entire* unpaid block span since `LastRewardedHeight`. This is the same broken invariant as the LoopFi finding: when the per-unit rate changes partway through an accruing interval, the whole interval is billed at the new rate instead of splitting it at the point the rate changed.

### Finding Description
`process_message` computes the reward for a `ConsensusMessage` delivery as: [1](#0-0) 

and the span/rate math is: [2](#0-1) 

`blocks = latest_height - baseline` is the full span of blocks that have not yet been paid for, potentially spanning many consensus updates and a long period of wall-clock time. `block_cost` is fetched fresh from `StateMachinesCostPerBlock` at the moment this particular update is processed — it is *not* the rate that was configured when each portion of that span was actually delivered. `update_cost_per_block` can change the rate at any time: [3](#0-2) 

If the rate is updated (e.g., from 100 to 10) while a relayer has an outstanding unpaid span from before the change, the very next `on_executed` call for that state machine pays the *entire* backlog — old and new portions alike — at whichever rate happens to be live at settlement time, rather than `old_rate * (span_before_change) + new_rate * (span_after_change)`.

### Impact Explanation
This causes an incorrect transfer between the `TreasuryAccount` and the relayer on every rate change that lands while a reward span is outstanding — either overpaying the relayer (loss of treasury funds) or underpaying them, and the mismatch is proportional to the size of the backlog and the magnitude of the rate change. Because rewards flow automatically via `on_executed`/`FeeHandler` on every processed `ConsensusMessage`, no admin or attacker action beyond a normal governance rate update is required to trigger the mispricing — the same class of accounting bug flagged in the LoopFi report, applied to Hyperbridge's treasury-funded relayer reward flow instead of an emissions vault.

### Likelihood Explanation
Every `update_cost_per_block` call is a plausible trigger: whenever the configured cost changes while any state machine has an unpaid block span (which is the normal, expected condition between consensus deliveries), the next reward computation prices the whole backlog at the wrong rate. Given that cost-per-block is expected to be tuned over time as chains and market conditions change, this is a routine occurrence, not an edge case.

### Recommendation
Track the block height (or timestamp) at which each rate change takes effect, and when computing a reward that spans a rate change, split it: `reward = old_rate * (change_height - baseline) + new_rate * (latest_height - change_height)`, summed across however many rate-change boundaries fall inside `[baseline, latest_height]`. Alternatively, force settlement (pay out at the currently configured rate and reset the watermark) as part of `update_cost_per_block` itself, so no unpaid span can ever straddle a rate change.

### Proof of Concept
1. Governance sets `StateMachinesCostPerBlock[X] = 100` via `update_cost_per_block`.
2. State machine `X` advances from height 1000 to 1100 without an `on_executed` consensus-message call landing in between (e.g., relayer batches deliveries, or the pallet simply hasn't processed a `ConsensusMessage` for a while) — `LastRewardedHeight[X]` stays at 1000.
3. Governance updates the rate: `update_cost_per_block(X, 10)`.
4. A relayer now delivers a `ConsensusMessage` that brings `StateMachineUpdated { latest_height: 1100 }`. `calculate_reward` computes `blocks = 1100 - 1000 = 100`, `block_cost = 10` (current), `reward = 1000` — instead of the historically correct `100 * 100 = 10000` that should apply to the pre-change span.
5. The relayer is underpaid by 9000 units (or, in the reverse rate-change direction, overpaid) purely because of when the batch of blocks happened to be settled relative to the rate change, confirming the LoopFi-class mispricing analog exists in this pallet. [2](#0-1) [4](#0-3)

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L41-51)
```rust
	fn process_message(
		state_machine_height: StateMachineHeight,
		state_machine_id: StateMachineId,
		relayer_account: T::AccountId,
	) -> Result<(), Error<T>> {
		if let Some(block_cost) = StateMachinesCostPerBlock::<T>::get(state_machine_id) {
			let reward = Self::calculate_reward(&state_machine_id, block_cost)?;

			if reward.is_zero() {
				return Ok(());
			}
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L77-100)
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
	}
```

**File:** modules/pallets/consensus-incentives/src/lib.rs (L128-150)
```rust
	#[pallet::call]
	impl<T: Config> Pallet<T> {
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
