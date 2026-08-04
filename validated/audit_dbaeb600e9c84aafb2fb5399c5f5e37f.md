### Title
Unpaid consensus-relay reward span is settled at the *current* `cost_per_block`, not the rate(s) in force while it accrued - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`pallet-consensus-incentives` accumulates an unpaid block-height span per state machine (`LastRewardedHeight` watermark to the chain's `latest_height`) and, whenever a relayer next submits a `ConsensusMessage`, pays the **entire** unpaid span using whatever `StateMachinesCostPerBlock` value is stored *at settlement time* — never the rate(s) that actually applied while each portion of that span accrued. This is the same broken-invariant class as the reported Initia bug: a rate/param change is not preceded by settling (accruing) the pending balance under the old rate, so a stale, unsettled quantity is retroactively repriced under the new rate.

### Finding Description
The reward computation lives in `calculate_reward`: [1](#0-0) 

and is invoked from `process_message`, which reads `StateMachinesCostPerBlock::<T>::get(state_machine_id)` fresh on every call and multiplies it by the *whole* unpaid span (`latest_height - baseline`), where `baseline` is the last-rewarded watermark: [2](#0-1) 

Governance can change the per-block rate at any time via the unprivileged-facing, public storage item `StateMachinesCostPerBlock`, set through `update_cost_per_block`: [3](#0-2) 

There is no function analogous to the removed `set_interest_fee_bps()` accrual call from the report that first "settles" the pending unpaid span (pays out the accrued blocks) using the *old* rate before the new rate takes effect. Because `LastRewardedHeight` is only advanced when a reward is actually paid, any block span left unclaimed while the old rate was in force is later paid entirely at whichever rate happens to be current when the next `ConsensusMessage` lands — exactly the "pending accrual assumes new values" defect described in the report, just in a Substrate/relayer-incentive pallet rather than a Move lending market.

### Impact Explanation
This directly causes treasury fund loss / unintended fund transfer, matching the bounty's "stealing or loss of funds" / "logic attacks" categories. A relayer (an unprivileged, permissionless role — anyone able to produce a validly signed `ConsensusMessage` can act as "the relayer" for `on_executed`) can observe the public `StateMachinesCostPerBlock` value and the growing gap between the chain's `latest_commitment_height` and the pallet's `LastRewardedHeight` watermark. By withholding submission of a consensus update while the rate is low, and only submitting it after governance raises `cost_per_block`, the relayer collects the *entire* accumulated span at the new, higher rate — extracting more from `TreasuryAccount` than the protocol intended to pay for blocks that accrued under the earlier, lower rate. Symmetrically, if the rate is lowered before a large backlog is claimed, the protocol underpays for work already done, which is also an "interest distortion" but not a fund-loss vector for the treasury (only unfair to relayers). The fund-loss direction (rate raised, then claim) is the actionable exploit: `T::Currency::transfer` moves treasury balance out based on the distorted, over-priced span: [4](#0-3) 

### Likelihood Explanation
Likelihood is moderate: it requires a legitimate rate change by `IncentivesOrigin` at some point (a normal, expected governance operation, not a compromised actor) and a relayer choosing to delay a routine consensus submission — no privileged access, collusion, or malicious peer/prover behavior is needed on the attacker's part. Any of the many independent consensus relayers can play this timing game since delivery of `ConsensusMessage`s is permissionless and the relevant storage (`StateMachinesCostPerBlock`, `LastRewardedHeight`, and the underlying `latest_commitment_height`) is all publicly readable, making the optimal timing trivially computable off-chain.

### Recommendation
Before/at the point `update_cost_per_block` (or `remove_incentives`) changes the rate for a `state_machine_id`, first settle any pending unpaid span at the *old* rate (mirroring the recommended "add back the accrual functions" fix from the report): e.g., compute `reward = (latest_height - LastRewardedHeight) * old_cost_per_block`, pay it out and advance `LastRewardedHeight` to `latest_height`, before writing the new rate into storage. Alternatively, record a rate history keyed by height range so `calculate_reward` can integrate the correct rate over each sub-span of the unpaid interval instead of applying a single current rate to the whole backlog.

### Proof of Concept
1. Governance calls `update_cost_per_block(state_machine_id, 100)`. `LastRewardedHeight` for the chain is initialized/at height `H0`. The remote chain's `latest_commitment_height` starts advancing via routine consensus updates, but the relayer deliberately does not submit a new `ConsensusMessage` (this pallet only pays on submission).
2. Over time the remote chain's height advances to `H0 + 10_000` (submitted/known via other relayers' consensus updates not tied to this pallet's reward path, or simply because updates accumulate before any reward-eligible message is processed) while `LastRewardedHeight` stays at `H0` because no `on_executed` call with a `Message::Consensus` happened.
3. Governance later raises the rate: `update_cost_per_block(state_machine_id, 10_000)` (e.g., due to genuinely higher infra costs).
4. The relayer now submits a `ConsensusMessage` referencing `latest_height = H0 + 10_000`. `calculate_reward` computes `reward = 10_000 blocks * 10_000 (new rate) = 100_000_000`, instead of the intended `10_000 * 100 = 1_000_000` that should have applied while the backlog was accruing under the old rate — draining ~100x more from `TreasuryAccount::get()` than the protocol budgeted for that historical span: [5](#0-4) 
5. `LastRewardedHeight` is then updated to `H0 + 10_000`, permanently baking in the over-payment with no way to reconcile it after the fact.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L41-75)
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
		Ok(())
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
