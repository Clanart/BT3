## Analysis

The Hubble Farms bug class — a config-update function that mutates a rate parameter (`reward_per_share`) without first settling/accruing the state up to the point of change, so the new rate retroactively applies to a span that should have been paid at the old rate — has a direct structural analog in `pallet-consensus-incentives`.

### Title
Retroactive reward-rate application in consensus relayer incentives lets a relayer capture treasury funds at a higher rate for blocks accrued under a lower rate - (File: `modules/pallets/consensus-incentives/src/lib.rs`)

### Summary
`update_cost_per_block` directly overwrites `StateMachinesCostPerBlock` for a state machine without ever "settling" (paying out or checkpointing) the span of blocks that have already accrued and gone unrewarded under the old rate. `calculate_reward` always multiplies the *entire* unpaid span — `latest_height - baseline` where `baseline` is `LastRewardedHeight` (the watermark) — by whatever `block_cost` happens to be stored *at the time the next `ConsensusMessage` is processed*, not by the rate(s) that were actually in effect while those blocks accrued. [1](#0-0) [2](#0-1) 

### Finding Description
The reward formula is `Reward = (LatestHeight − Baseline) × CostPerBlock`, where `Baseline` is the `LastRewardedHeight` watermark (or `previous_commitment_height` if unset) [3](#0-2) . Crucially, `CostPerBlock` is read fresh from `StateMachinesCostPerBlock` at reward-calculation time [4](#0-3) , and this map can be updated at any moment via the permissionless-to-invoke-conditions-aside extrinsic `update_cost_per_block`, which simply does:

```rust
StateMachinesCostPerBlock::<T>::mutate(state_machine_id.clone(), |maybe_cost| {
    *maybe_cost = Some(cost_per_block);
});
```

with no read/settlement of the pending unrewarded span before the mutation [5](#0-4) .

There is nothing that forces a `ConsensusMessage`/reward settlement to happen immediately before or during a rate change. This means blocks that accrued entirely under an old (e.g., low) rate can later be paid at a new (e.g., higher) rate the next time any relayer delivers a `ConsensusMessage` advancing `latest_commitment_height`, exactly mirroring the Hubble Farms pattern where `reward_per_share`/`last_issuance_ts` are changed without accruing rewards for the elapsed interval first.

Unlike a pure "governance misconfiguration" issue, the exploit primitive here does not require a malicious/compromised governance actor: governance changing `cost_per_block` is a normal, expected operation (e.g., periodic reward-rate adjustments). The attacker is an ordinary, unprivileged relayer who can observe the pending `StateMachineCostPerBlockUpdated` event/extrinsic in the mempool or simply monitor on-chain rate changes, and who controls *when* to submit an already-available, valid `ConsensusMessage` via the permissionless unsigned extrinsic (`handle_unsigned`, dispatched through `FeeHandler::on_executed`) [6](#0-5) . By withholding a valid, already-provable consensus update until *after* governance raises `cost_per_block`, the relayer causes the entire backlog of already-elapsed, previously-cheap blocks to be paid out at the new higher rate from the treasury — an amount the protocol never intended to disburse for that span.

### Impact Explanation
This directly causes loss of treasury funds: the `RelayerRewarded` transfer is a real on-chain `Currency::transfer` from `TreasuryAccount` to the relayer [7](#0-6) , plus a matching `ReputationAsset` mint. An attacker-controlled relayer can extract materially more value than the protocol intended to pay for that span of blocks, purely by timing submission around a rate change — a logic/timing attack resulting in fund loss from the treasury, matching the bounty's "stealing or loss of funds" / "logic attacks" categories.

### Likelihood Explanation
Rate changes via `update_cost_per_block` are a normal operational lever (adjusting incentive economics over time), so the precondition (a rate change occurring while there is an unrewarded backlog) is not contrived — it is the expected steady-state of the pallet. Any relayer able to submit a `ConsensusMessage` can trivially delay submission (they already hold the proof) to land after a rate increase; no relayer collusion, prover compromise, or governance malice is required, only ordinary opportunistic timing by a permissionless participant.

### Recommendation
Before applying a new `cost_per_block` in `update_cost_per_block`, force settlement of the currently accrued/unrewarded span at the old rate (e.g., by computing and paying out — or explicitly checkpointing to zero — the reward for `latest_commitment_height - LastRewardedHeight` using the *old* `block_cost` and updating `LastRewardedHeight` to the current height) before writing the new rate into `StateMachinesCostPerBlock`. This mirrors the referenced patch's remediation: "ensure these parameters are not directly altered without refreshing the global rewards state."

### Proof of Concept
1. Governance sets `cost_per_block = 10` for `state_machine_id` via `update_cost_per_block` [8](#0-7) .
2. The remote chain advances from height 1000 to height 2000 over time, but no relayer submits a `ConsensusMessage` yet — `LastRewardedHeight` stays at 1000, `latest_commitment_height` becomes 2000 (any relayer holding this proof can submit it whenever they choose).
3. Governance raises `cost_per_block` to 1000 for legitimate economic reasons via the same extrinsic — nothing in `update_cost_per_block` touches `LastRewardedHeight` or pays out the pending 1000-block backlog first.
4. The relayer now submits the previously-withheld valid `ConsensusMessage` advancing to height 2000. `calculate_reward` computes `blocks = 2000 - 1000 = 1000` and multiplies by the *current* `block_cost = 1000`, yielding a reward of `1,000,000` instead of the `10,000` the protocol intended to pay for that span at the old rate [9](#0-8) .
5. The treasury transfers the inflated amount to the relayer, and `LastRewardedHeight` is bumped to 2000, permanently erasing any record that the span was underpriced — the excess funds are gone.

### Citations

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

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L87-112)
```rust
#[test]
fn test_incentivize_relayer() {
	let mut ext = new_test_ext();
	ext.execute_with(|| {
		let host = Ismp::default();
		let state_machine_id = setup_state_machine();

		pallet_consensus_incentives::Pallet::<Test>::update_cost_per_block(
			RuntimeOrigin::root(),
			state_machine_id,
			100,
		)
		.unwrap();

		let (consensus_message, relayer_account) = setup_host_and_message(&host);

		pallet_ismp::Pallet::<Test>::handle_unsigned(
			RuntimeOrigin::none(),
			vec![consensus_message],
		)
		.unwrap();

		assert_eq!(Balances::balance(&relayer_account), UNIT + 4200);
		assert_eq!(Assets::balance(ReputationAssetId::get(), &relayer_account), 4200);
	})
}
```
