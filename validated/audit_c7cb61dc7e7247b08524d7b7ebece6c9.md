### Title
`pallet-consensus-incentives::calculate_reward()` prices an entire unsettled block span at the *current* `cost_per_block`, letting a relayer arbitrage a rate change to over-drain the treasury - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
This is the same broken invariant as the Backd `Minter._executeInflationRateUpdate()` finding: a rate is updated in storage, but the "settlement" step that owes value for time/blocks elapsed *before* the rate change is not checkpointed against the old rate first. In `pallet-consensus-incentives`, `update_cost_per_block()` overwrites `StateMachinesCostPerBlock` in place with no prior checkpoint, and `calculate_reward()` later multiplies the *entire* un-rewarded block span (`latest_height - baseline`) by whatever `block_cost` happens to be read at settlement time.

### Finding Description
`update_cost_per_block()` mutates the rate with no side effect on `LastRewardedHeight`: [1](#0-0) 

`calculate_reward()` computes the reward strictly from the current rate and the gap between the last-paid watermark and the newly proven height — there is no historical rate table, so every block in that span (no matter when it actually elapsed) is priced at whatever `block_cost` is in storage the moment the proof lands: [2](#0-1) 

`process_message()` then transfers `reward` straight from `TreasuryAccount` to the relayer that supplied the proof, and only *afterward* advances `LastRewardedHeight`: [3](#0-2) 

Submission of the consensus proof that triggers this whole path is not privileged — any relayer holding a valid consensus proof decides when to submit it via `on_executed`, which is invoked by `FeeHandler` after `pallet-ismp` processes the batch: [4](#0-3) 

Because the reward calculation has no memory of which `cost_per_block` was in effect for which sub-range of the span, a relayer that is entitled to submit a proof for a large backlog of already-finalized heights can simply wait to submit until *after* governance raises `cost_per_block`, then collect the new (higher) rate retroactively for the entire backlog — funds that were only ever supposed to accrue at the old, lower rate.

### Impact Explanation
This is a direct loss of treasury funds via a logic/timing attack, matching the bounty's "stealing or loss of funds" / "logic attacks" category. The relayer does not need to be malicious in the sense of forging proofs or corrupting consensus — they only need to control *when* they submit a legitimate proof, which is entirely within a normal relayer's discretion (nothing forces prompt submission; `LastRewardedHeight` simply accumulates until someone claims). Every cost-per-block increase becomes a windfall opportunity for whoever is holding an unsubmitted backlog of proven heights at that moment, at the direct expense of `T::TreasuryAccount`.

### Likelihood Explanation
`update_cost_per_block` is a normal, expected governance operation (rate re-pricing is exactly the kind of parameter tuning a live incentive pallet needs), so the triggering event is common, not exotic. The only extra requirement on the attacker's side is patience — holding back a valid proof instead of submitting it immediately, which is indistinguishable from ordinary relayer behavior (e.g., waiting for a batch, waiting for cheaper gas, or just being offline for a while) and requires no privileged role, malicious peer, or compromised key.

### Recommendation
Before applying a new `cost_per_block`, either (a) force settlement of all state machines' pending reward span at the *old* rate (mirroring the report's fix of calling `checkpointAllGauges()` before mutating the rate), or (b) track rate changes with an effective-height and split `calculate_reward` into per-rate-segment sums so a span that straddles a rate change is priced piecewise instead of entirely at the rate current at claim time.

### Proof of Concept
1. Governance sets `StateMachinesCostPerBlock[SM] = 1` via `update_cost_per_block`.
2. Chain `SM` advances 100,000 blocks with hyperbridge's commitment lagging behind (no relayer has submitted an update for a while); `LastRewardedHeight[SM]` stays at the old baseline.
3. A relayer already possesses a valid consensus proof advancing `SM`'s commitment by the full 100,000-block span but withholds submission.
4. Governance raises `StateMachinesCostPerBlock[SM] = 1000` (e.g., in response to rising infra costs for future blocks).
5. The relayer now submits the withheld proof. `on_executed` → `process_message` → `calculate_reward` computes `reward = 100_000 * 1000` instead of the `100_000 * 1` that should have applied to blocks that elapsed while the old rate was live.
6. `T::Currency::transfer` pays this inflated amount straight out of the treasury account, and `LastRewardedHeight` is only then advanced — no guard anywhere validates that the rate used matches the rate that was live when each block in the span actually occurred.

### Citations

**File:** modules/pallets/consensus-incentives/src/lib.rs (L133-150)
```rust
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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L104-157)
```rust
impl<T: Config> FeeHandler for Pallet<T>
where
	<T as frame_system::Config>::AccountId: From<[u8; 32]>,
{
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
			// When a batch contains multiple `StateMachineUpdated` events for the
			// same `state_machine_id` (sequential consensus updates for the same
			// chain), `calculate_reward` reads the same persisted
			// `(latest_commitment_height, previous_commitment_height)` pair on
			// every iteration and pays the same block-span reward N times.
			// Collapse the per-state-machine event stream to the single highest
			// `latest_height` so each state machine receives one reward per
			// batch, sized by the actual span of its commitment advance.
			let mut highest_per_state_machine: BTreeMap<StateMachineId, u64> = BTreeMap::new();
			for event in events {
				if let IsmpEvent::StateMachineUpdated(update) = event {
					highest_per_state_machine
						.entry(update.state_machine_id)
						.and_modify(|h| {
							if update.latest_height > *h {
								*h = update.latest_height;
							}
						})
						.or_insert(update.latest_height);
				}
			}

			for (state_machine_id, latest_height) in highest_per_state_machine {
				let state_machine_height =
					StateMachineHeight { id: state_machine_id.clone(), height: latest_height };

				let _ = Self::process_message(
					state_machine_height,
					state_machine_id,
					relayer_account.clone().into(),
				);
			}
		}
```
