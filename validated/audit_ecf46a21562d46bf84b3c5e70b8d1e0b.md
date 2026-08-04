## Analysis

The external report's core broken invariant: **a governance-adjustable rate parameter is applied retroactively across an unswept historical span, because the code has no per-height/per-epoch checkpointing of the rate that was in effect when that span accrued.**

The closest local analog is `pallet-consensus-incentives`, which pays relayers for delivering ISMP consensus updates using exactly this pattern. [1](#0-0) 

### Title
Retroactive rate application in `pallet-consensus-incentives` allows relayers to drain excess treasury funds via delayed consensus-proof submission across a rate change - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives::calculate_reward` computes relayer rewards as `(latest_height - LastRewardedHeight) * StateMachinesCostPerBlock`. `LastRewardedHeight` is only advanced when a reward is actually paid, so any gap between consensus-proof deliveries accumulates as an unpaid "span." Because `StateMachinesCostPerBlock` is read fresh at reward time rather than being locked in per-height, a governance rate increase (`update_cost_per_block`) applies retroactively to the *entire* accumulated span, not just to blocks produced after the change.

### Finding Description
`calculate_reward` reads the current `block_cost` from `StateMachinesCostPerBlock` and multiplies it by `blocks = latest_height - baseline`, where `baseline` is the persisted `LastRewardedHeight` watermark: [2](#0-1) 

`LastRewardedHeight` only moves when `process_message` successfully pays a reward: [3](#0-2) 

There is no storage of the rate that was active while each block-span accrued, and no restriction limiting `update_cost_per_block` to only affect future spans: [4](#0-3) 

Consensus-proof submission (and thus reward collection) is a permissionless action — any relayer whose sr25519 signature recovers from the consensus message is paid, with no allow-list gating who may deliver a `ConsensusMessage`: [5](#0-4) 

This is structurally identical to the LendingLedger bug: a governance-configurable per-unit rate is applied over a span that spans the moment of the rate change, because nothing forces a checkpoint/settlement at the moment of adjustment.

### Impact Explanation
An unprivileged relayer can withhold submitting consensus proofs for a state machine, letting the unpaid height-span (`latest_height - LastRewardedHeight`) grow arbitrarily large. Once governance performs a routine, benign `update_cost_per_block` rate increase (an expected, ordinary operational action — not a governance mistake or malicious act), the relayer submits a single consensus proof and is paid the *entire* accumulated backlog at the new, higher rate via `T::Currency::transfer` from `TreasuryAccount`: [6](#0-5) 

This is a direct, unauthorized over-drain of treasury funds — the relayer is paid for blocks that were bridged under the old (lower) rate, at the new rate, purely by timing an ordinary permissionless call around a routine parameter update they can observe on-chain (rate changes are public events before finalization/inclusion). No malicious governance, compromised relayer set, or privileged access is required; only an ordinary relayer strategically delaying/timing a legitimate action.

### Likelihood Explanation
Consensus messages naturally batch multiple blocks of progress already (see the `impls.rs` comment about batches with multiple `StateMachineUpdated` events), so backlogs of un-rewarded height are already a normal occurrence, not a contrived edge case. Rate updates via `update_cost_per_block` are a documented, ordinary maintenance operation (adjusting for gas-cost drift, chain economics, etc.), making the "backlog + rate bump" collision a realistic, low-effort, and repeatable pattern for any relayer to game — likelihood is high given the treasury pays out unconditionally with `Preservation::Expendable` and no per-height rate memory.

### Recommendation
Checkpoint (settle) the pending reward span immediately whenever `update_cost_per_block`/`remove_incentives` is called, by paying out (or freezing/recording) rewards accrued up to the current `latest_commitment_height` at the *old* rate before applying the new rate, similar to updating `LastRewardedHeight` at the time of the parameter change. Alternatively, store a per-height rate history (or a `(height, rate)` checkpoint list) so `calculate_reward` can compute a piecewise sum across any rate changes that occurred within `[baseline, latest_height]`, rather than applying only the current rate to the whole span.

### Proof of Concept
1. Governance sets `StateMachinesCostPerBlock[sm] = 10` via `update_cost_per_block`.
2. Consensus advances from height 100 (last rewarded) to height 1100 on `sm`, but no relayer submits a `ConsensusMessage` claim for it (an honest lull, or a relayer deliberately withholds submission — nothing prevents this).
3. Governance performs an ordinary rate update: `update_cost_per_block(sm, 1000)` (e.g., due to gas-cost changes on `sm`).
4. A relayer now submits the first `ConsensusMessage` that reflects `latest_height = 1100`.
5. `calculate_reward` computes `blocks = 1100 - 100 = 1000`, `reward = 1000 * 1000 = 1,000,000` — the whole backlog paid at the new rate, instead of `1000 * 10 = 10,000` (what it should have earned if paid before the rate bump). The `TreasuryAccount` transfers the inflated amount to the relayer via `process_message`, and `LastRewardedHeight` jumps to 1100, permanently masking the overpayment. [7](#0-6)

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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L108-124)
```rust
	fn on_executed(
		messages: Vec<MessageWithWeight>,
		events: Vec<IsmpEvent>,
	) -> DispatchResultWithPostInfo {
		let maybe_relayer_account = messages.get(0).and_then(|first_message| {
			if let Message::Consensus(consensus_msg) = &first_message.message {
				let data = sp_io::hashing::keccak_256(&consensus_msg.consensus_proof);
				Signature::decode(&mut &consensus_msg.signer[..])
					.ok()
					.and_then(|sig| sig.verify_and_get_sr25519_pubkey(&data, None).ok())
					.map(|pub_key| pub_key.into())
			} else {
				None::<[u8; 32]>
			}
		});

		if let Some(relayer_account) = maybe_relayer_account {
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
