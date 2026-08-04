### Title
First-reward baseline defaults to `0` instead of the true pre-existing height, causing a massively inflated one-time relayer reward - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`calculate_reward` falls back to `previous_commitment_height` only when `LastRewardedHeight` has never been set for a `state_machine_id`. However, `previous_commitment_height` (`PreviousStateMachineHeight`) is itself unconditionally initialized to `0` the very first time any height is ever stored for that state machine, regardless of what that first height actually is. This means the very first reward computed for a newly tracked state machine is `latest_height - 0`, i.e. the entire raw height value, rather than the number of blocks actually advanced by the triggering consensus update.

### Finding Description
`store_latest_commitment_height` in the ISMP host sets the "previous" height like this: [1](#0-0) 

On the very first update for a `state_machine_id`, `LatestStateMachineHeight::<T>::get(height.id)` is `None`, so `previous_height` defaults to `0` and `PreviousStateMachineHeight` is stored as `0` — even though the first submitted `height.height` for a real, already-running remote chain can be an arbitrarily large absolute block number (chains are not bridged from genesis).

`calculate_reward` then reads this value as the baseline when no watermark exists yet: [2](#0-1) 

Since `LastRewardedHeight::get(state_machine_id)` is `None` on the first `process_message` call, `baseline = previous_height = 0`, and `blocks = latest_height.saturating_sub(0) = latest_height`. The reward is then `latest_height * block_cost`, not `(latest_height − previous‑actual‑height) * block_cost`. `LastRewardedHeight` is then set to `latest_height` in `process_message`: [3](#0-2) 

So the very first reward for any state machine is computed against a fabricated baseline of `0`, not against the chain's actual pre-existing height, before any watermark is ever correctly established.

### Impact Explanation
This causes the treasury (`T::TreasuryAccount`) to pay a reward proportional to the entire absolute height of the remote chain at first-registration time, rather than the number of blocks actually processed by the triggering message. For any state machine whose first tracked height is non-trivial (which is the normal case for any already-running chain being bridged), this results in a reward payout many orders of magnitude larger than intended — an accounting/logic flaw that drains protocol funds (`RelayerRewarded` amount, minted `ReputationAsset`) far beyond the legitimate "cost per block" model the pallet is designed to enforce. This is triggered purely by unprivileged message delivery (`on_executed`/`process_message`), matching the "wrongful accounting" / "loss of funds" impact class.

### Likelihood Explanation
High. No privileged action or malicious infrastructure is required — any relayer that is first to deliver a `Consensus` message causing a `StateMachineUpdated` event for a state machine that has not yet had a reward recorded will trigger this path. Since `StateMachinesCostPerBlock` is set by governance for real bridged chains (which are never registered starting at height 0), this bug is expected to manifest on the very first reward for essentially every newly incentivized state machine.

### Recommendation
Do not rely on `PreviousStateMachineHeight`'s implicit `0` default as an economically meaningful baseline. When no reward watermark exists and no genuine prior commitment height is available (i.e., `PreviousStateMachineHeight` was itself defaulted rather than derived from a real prior update), the first reward should either be skipped (start the watermark at `latest_height` without paying), or the baseline should be seeded explicitly (e.g., via `update_cost_per_block` or a dedicated initialization call) with the actual starting height of the state machine before incentives begin, rather than assuming block `0`.

### Proof of Concept
1. A brand-new `state_machine_id` (never before updated) is registered for incentives via `update_cost_per_block`.
2. A relayer submits the first-ever `Consensus` message causing a `StateMachineUpdated` event with `latest_height = H` for a real remote chain already at absolute block height `H` (e.g., `H = 5,000,000`).
3. `store_latest_commitment_height` sets `PreviousStateMachineHeight = 0` (since `LatestStateMachineHeight` was `None`) and `LatestStateMachineHeight = H`. [1](#0-0) 
4. `on_executed` → `process_message` → `calculate_reward` computes `baseline = LastRewardedHeight::get() = None → previous_height = 0`, so `blocks = H - 0 = H`. [4](#0-3) 
5. `reward = H * block_cost` is transferred from the treasury and minted as reputation to the relayer, vastly exceeding the cost of processing a single message — confirmed by comparing this to the intended per-block-of-progress accounting the pallet claims to implement.
6. `LastRewardedHeight` is then set to `H`, permanently locking in the corrupted, oversized initial watermark.

### Citations

**File:** modules/pallets/ismp/src/host.rs (L229-234)
```rust
	fn store_latest_commitment_height(&self, height: StateMachineHeight) -> Result<(), Error> {
		let previous_height = LatestStateMachineHeight::<T>::get(height.id).unwrap_or_default();
		PreviousStateMachineHeight::<T>::insert(height.id, previous_height);
		LatestStateMachineHeight::<T>::insert(height.id, height.height);
		Ok(())
	}
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L70-72)
```rust
			LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
				*watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
			});
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L86-97)
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
```
