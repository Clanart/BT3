## Analysis

The Curve `GaugeController` bug's core invariant break is: **removing an identifier (a gauge) does not reset or reconcile the state tied to it, so state that should be neutralized on removal instead persists and produces an incorrect outcome once the identifier's status changes again.**

The closest local analog is in `pallet-consensus-incentives`, which pays relayers for consensus proof delivery based on a per-state-machine "watermark" of already-rewarded block height.

### The corrupted value

`LastRewardedHeight<T>` is only advanced inside `process_message`, and that function only runs its reward/watermark logic when `StateMachinesCostPerBlock::<T>::get(state_machine_id)` returns `Some`: [1](#0-0) 

`calculate_reward` computes the reward as `(latest_height - baseline) * block_cost`, where `baseline` comes from `LastRewardedHeight`, falling back to `previous_commitment_height` only if no watermark was ever set: [2](#0-1) 

Governance can disable incentives for a chain via `remove_incentives`, which deletes the `StateMachinesCostPerBlock` entry but does **not** touch `LastRewardedHeight`: [3](#0-2) 

While the entry is removed, `process_message` short-circuits at `if let Some(block_cost) = ...` and returns `Ok(())` without ever updating `LastRewardedHeight`. Consensus updates for that state machine continue to advance `latest_commitment_height` on the host regardless, since consensus verification is independent of the incentives pallet. When governance later re-enables incentives via `update_cost_per_block`: [4](#0-3) 

the very next relayer to submit a single consensus message triggers `calculate_reward` with the *stale* `baseline` (from before the removal), so `blocks = latest_height - baseline` spans the entire disabled period plus any period since re-enable — all paid out in one shot to whichever relayer happens to deliver that one message, funded directly from `T::TreasuryAccount`: [5](#0-4) 

This exactly mirrors the seed bug's shape: an admin action on an identifier (`remove_incentives`/gauge removal) leaves per-identifier accounting state (`LastRewardedHeight`/user vote weight) unreconciled, and the stale state later produces an incorrect, unearned outcome (inflated reward/unrecoverable vote) that an ordinary unprivileged actor (a relayer, analogous to Alice) can trigger or is harmed by — no malicious relayer, prover, or governance intent required.

### Title
Consensus-incentives reward watermark is not reset on `remove_incentives`, causing an inflated treasury payout to the first relayer after re-enabling — (File: modules/pallets/consensus-incentives/src/lib.rs)

### Summary
`remove_incentives` deletes `StateMachinesCostPerBlock` for a state machine but leaves `LastRewardedHeight` untouched. Because the watermark is only advanced inside the gated `process_message` reward path, any block height advanced by consensus updates while incentives are disabled is never recorded. When incentives are re-enabled, `calculate_reward` treats the entire disabled span as unrewarded blocks and pays it out in full to whichever relayer submits the first eligible consensus message afterward.

### Finding Description
`process_message` only updates `LastRewardedHeight` when `StateMachinesCostPerBlock::get` is `Some` [6](#0-5) . `remove_incentives` clears that map entry without capturing or freezing the current `latest_commitment_height` as a new watermark [7](#0-6) . Since the underlying ISMP host continues to accept and record consensus state advancement independently of this pallet, `latest_commitment_height` keeps growing during the "removed" window. Once `update_cost_per_block` re-adds an entry [8](#0-7) , `calculate_reward`'s `blocks = latest_height.saturating_sub(baseline)` computes the full span including the disabled period [9](#0-8) , and the reward is transferred straight from `T::TreasuryAccount` to the relayer that happened to deliver the first post-re-enable message [10](#0-9) .

### Impact Explanation
This causes an incorrect (inflated) amount to be paid out of the treasury to an unprivileged relayer, for a span of blocks it did not actually service under active incentives. This is a direct loss-of-funds / wrong-amount payout from protocol treasury funds, triggered by ordinary governance lifecycle operations (disable then re-enable incentives) with no need for a malicious relayer, prover, or admin intent.

### Likelihood Explanation
Disabling and re-enabling incentives for a state machine (e.g., for cost recalibration, chain maintenance, or temporary suspension) is a plausible, non-malicious governance operation, matching the seed report's framing exactly. Any relayer that happens to submit the first consensus message after re-enable automatically collects the inflated reward — no coordination or special access required.

### Recommendation
When `remove_incentives` clears `StateMachinesCostPerBlock`, also snapshot the current `latest_commitment_height` into `LastRewardedHeight` for that state machine so re-enabling incentives resumes accrual from that point rather than from the last time a reward was actually paid. Alternatively, track a distinct "disabled-since height" and clamp `calculate_reward`'s baseline to the maximum of the watermark and the disable height.

### Proof of Concept
1. Governance calls `update_cost_per_block(sm, cost)`, incentives begin accruing normally; some messages are delivered and `LastRewardedHeight[sm] = H0`.
2. Governance calls `remove_incentives(sm)`. `StateMachinesCostPerBlock[sm]` is removed.
3. Over the next period, consensus updates continue to be accepted by the ISMP host for `sm`, advancing `latest_commitment_height` to `H1 >> H0`, but `process_message` never runs its reward branch, so `LastRewardedHeight[sm]` stays at `H0`.
4. Governance calls `update_cost_per_block(sm, cost)` again to re-enable incentives.
5. Any relayer submits a single valid consensus message for `sm`. `process_message` -> `calculate_reward` computes `blocks = H1 - H0` (spanning the entire disabled window) and transfers `(H1 - H0) * cost` from `T::TreasuryAccount` to that relayer in one transaction, despite no incentive being owed for most of that span.

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
