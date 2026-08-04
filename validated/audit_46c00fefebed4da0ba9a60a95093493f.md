## Title
Consensus-Incentives Reward Back-Payment for the Inactive Window Between `remove_incentives` and `update_cost_per_block` - (File: `modules/pallets/consensus-incentives/src/lib.rs`, `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives` pays relayers `(latest_height - baseline) * cost_per_block` for consensus updates, where `baseline` is `LastRewardedHeight`, a watermark that is only advanced inside `process_message` when a reward is actually paid. `remove_incentives` deletes the `StateMachinesCostPerBlock` entry but never touches `LastRewardedHeight`, so the watermark freezes at whatever height it last reached while incentives were active. Consensus updates for that state machine keep advancing (`StateMachineUpdated` events still fire independently of this pallet), so the "distance" between the frozen watermark and the live height grows for the entire time incentives are disabled. When governance calls `update_cost_per_block` again to restart the reward, the very next processed consensus message pays the relayer for every block height gained during the entire disabled/inactive interval — exactly the analog of the reported bug: reward accrual counts an inactive period as if it were active.

### Finding Description
`calculate_reward` in `modules/pallets/consensus-incentives/src/impls.rs` computes: [1](#0-0) 

```
let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);
let blocks = latest_height.saturating_sub(baseline);
let reward = blocks_as_balance.saturating_mul(block_cost);
```

`baseline` only advances inside `process_message` when `StateMachinesCostPerBlock::<T>::get` returns `Some`: [2](#0-1) 

`remove_incentives` deletes the cost entry but does nothing to `LastRewardedHeight`: [3](#0-2) 

While `StateMachinesCostPerBlock` is `None`, `process_message`'s outer `if let Some(block_cost) = ...` never runs, so no reward is paid **and the watermark is never advanced** — consistent with intent (no rewards while disabled). However, `on_executed` still processes `StateMachineUpdated` events for that state machine because consensus verification/height advancement is independent of this incentives pallet: [4](#0-3) 

So `latest_height`/`previous_height` as reported by `IsmpHost` keep climbing throughout the "disabled" window, while `LastRewardedHeight` sits frozen at the pre-removal value. When `update_cost_per_block` is called again to re-enable rewards for the same `state_machine_id`, the very next consensus message causes `calculate_reward` to compute `blocks = latest_height - frozen_baseline`, which spans the entire disabled interval — the reward is paid as though every one of those blocks had active `cost_per_block` incentive, when in fact the state machine explicitly had incentives removed for that period.

This mirrors the reported bug precisely: `update_reward_infos`/`update` compute rewards from `current_timestamp - last_update_time` without excluding the inactive gap between distribution end and distribution restart; here `calculate_reward` computes rewards from `latest_height - LastRewardedHeight` without excluding the inactive gap between `remove_incentives` and the next `update_cost_per_block`.

### Impact Explanation
This causes unauthorized transfer of Treasury funds: `T::Currency::transfer` moves `TreasuryAccount` funds to the relayer for a block span that governance explicitly intended to be unrewarded (or rewarded at a different, possibly lower, rate before removal). Any relayer submitting the first `ConsensusMessage` after incentives are re-enabled captures the entire back-pay for the disabled window, which can be an arbitrarily large amount depending on how long incentives were off and the new `cost_per_block`. This is fund loss from the treasury to an unintended beneficiary/amount, matching the bounty's "stealing or loss of funds" / "transaction manipulation" categories. It requires no malicious peer, relayer, or governance actor — an honest relayer simply submitting a normal consensus message triggers the miscalculated payout once a legitimate governance operator restarts incentives.

### Likelihood Explanation
`remove_incentives` and later `update_cost_per_block` are both realistic, expected governance operations (e.g., pausing incentives for a misbehaving/low-priority chain and later re-enabling them, or adjusting economics). Any relayer that is already running and delivering consensus messages for that state machine will automatically trigger `on_executed` → `process_message` → `calculate_reward` on the next successful delivery after re-enable, with no special conditions required. This makes the flaw likely to trigger the first time this operational pattern (disable → re-enable) is used in production.

### Recommendation
When incentives are removed via `remove_incentives`, snapshot/advance `LastRewardedHeight` to the current `latest_commitment_height` for that state machine (or store a separate "disabled-at height" and treat it as the new baseline on re-enable), so that `calculate_reward` never counts blocks that occurred while `StateMachinesCostPerBlock` was `None`. Equivalently, on `update_cost_per_block` re-registration, reset `LastRewardedHeight` to the current height rather than letting a stale pre-removal value stand as the baseline.

### Proof of Concept
1. Governance calls `update_cost_per_block(sm_id, cost)` — incentives active; relayer submits consensus updates and `LastRewardedHeight[sm_id]` tracks the live height (e.g., reaches height `H0`).
2. Governance calls `remove_incentives(sm_id)` — `StateMachinesCostPerBlock[sm_id]` removed; `LastRewardedHeight[sm_id]` remains `H0`.
3. Consensus updates for `sm_id` continue to be delivered and verified by `pallet-ismp` independent of this pallet (height advances to `H1 >> H0`) — no reward paid, watermark frozen at `H0` as `process_message`'s `if let Some(block_cost)` never executes.
4. Governance calls `update_cost_per_block(sm_id, new_cost)` to re-enable incentives.
5. Any relayer delivers the next `ConsensusMessage` for `sm_id`. `on_executed` fires, `process_message` runs, `calculate_reward` computes `blocks = H1 - H0` (the entire disabled window) and pays `blocks * new_cost` from the Treasury to that relayer in a single transaction — reward for a period incentives were explicitly turned off.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L41-73)
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
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L89-99)
```rust
		// Use the rewarded watermark as the baseline and fall back to the previous height until
		// the first reward is recorded for this chain. The watermark only moves forward, so a
		// height that is rolled back and later resubmitted is not paid for a second time.
		let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);

		let blocks = latest_height.saturating_sub(baseline);

		let blocks_as_balance: <T as pallet_ismp::Config>::Balance = blocks.saturated_into();
		let reward = blocks_as_balance.saturating_mul(block_cost);

		Ok(reward)
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L108-157)
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

**File:** modules/pallets/consensus-incentives/src/lib.rs (L152-166)
```rust
		/// Update cost per block for a state machine
		#[pallet::call_index(1)]
		#[pallet::weight(T::WeightInfo::update_cost_per_block())]
		pub fn remove_incentives(
			origin: OriginFor<T>,
			state_machine_id: StateMachineId,
		) -> DispatchResult {
			T::IncentivesOrigin::ensure_origin(origin)?;

			StateMachinesCostPerBlock::<T>::remove(state_machine_id.clone());

			Self::deposit_event(Event::<T>::StateMachineCostPerBlockRemoved { state_machine_id });

			Ok(())
		}
```
