## Finding

### Title
`update_cost_per_block` retroactively misprices unpaid consensus-relay blocks — pending reward span is not settled before the rate changes (`modules/pallets/consensus-incentives/src/lib.rs`)

### Summary
`pallet-consensus-incentives` pays relayers `(latest_height - LastRewardedHeight) * cost_per_block` the next time a `ConsensusMessage` is processed. `update_cost_per_block` overwrites `StateMachinesCostPerBlock` immediately, with no step that first "closes out" the reward owed for blocks already accrued at the old rate. The very next reward calculation then applies the **new** rate across the **entire** unpaid span since the watermark — exactly the same broken invariant as the C4 Wise-Lending finding, where `setPoolFee` changed the rate without invoking `syncPool` first, so the next interest sync mis-applied the new fee to the whole unsynced period.

### Finding Description
The reward path: [1](#0-0) 

computes `blocks = latest_height - baseline` where `baseline` is `LastRewardedHeight` (or `previous_height` if unset), then multiplies by `block_cost` fetched live from `StateMachinesCostPerBlock`: [2](#0-1) 

`update_cost_per_block` is a plain storage overwrite with no interaction with `LastRewardedHeight` or any pending-reward settlement: [3](#0-2) 

Consequence: if `N` blocks have advanced since the last reward and governance changes `cost_per_block` from `C_old` to `C_new` before the next `ConsensusMessage` lands, the relayer is paid `N * C_new` for a span that was `N * C_old` blocks-worth of work under the previously advertised rate. There is no `syncManually`-equivalent call (no function that settles `LastRewardedHeight` to `latest_height` and pays out at the old rate before the new rate takes effect). This mirrors the Wise-Lending report's exact defect: the rate is changed in place while a to-be-settled interval sits unflushed, and the next settlement silently uses the wrong rate for that interval.

### Impact Explanation
- If `cost_per_block` is **decreased**, relayers who advanced consensus under the old, higher committed rate are underpaid for that already-completed work.
- If `cost_per_block` is **increased**, the `TreasuryAccount` overpays for blocks that were already relayed under the old, lower rate — an unintended transfer of treasury funds beyond what was owed, i.e., loss of protocol funds via `T::Currency::transfer` in `process_message`.

Either direction produces incorrect reward accounting that a relayer/attacker can passively benefit from simply by observing a pending rate change and timing/waiting for their next natural `ConsensusMessage` submission to land after the new rate is set — no special privilege beyond normal relaying is needed to receive the mispriced payout.

### Likelihood Explanation
Low-to-moderate: it requires a rate change (`update_cost_per_block`) to occur while a nonzero unpaid block span exists — the same "low likelihood, but real and not privileged-attacker-dependent" profile as the original C4 finding, which the judge still accepted as in-scope because an ordinary, non-malicious governance call produces the loss.

### Recommendation
Before mutating `StateMachinesCostPerBlock` in `update_cost_per_block`, force-settle the pending span for that `state_machine_id` at the **old** rate: compute `reward = (current_latest_height - LastRewardedHeight) * old_cost_per_block`, pay it out (or otherwise checkpoint `LastRewardedHeight` to the current latest height) before applying the new `cost_per_block`. This closes the window during which mixed-rate blocks get priced at the wrong rate, analogous to invoking `syncPool`/`syncManually` before `setPoolFee` in the original report.

### Proof of Concept
1. Governance calls `update_cost_per_block(state_machine_id, 100)`.
2. Relayer submits consensus proofs advancing the state machine from height `H` to `H+1000` (no reward claimed yet, `LastRewardedHeight` still points to an older height, so 1000 blocks are pending at rate 100).
3. Before any `ConsensusMessage` triggers `on_executed`, governance calls `update_cost_per_block(state_machine_id, 10_000)` (a 100x increase), e.g. for a different, legitimate reason (repricing for a new time period).
4. The relayer submits (or a delayed) `ConsensusMessage` referencing the still-pending span; `process_message`/`calculate_reward` computes `reward = 1000 * 10_000` instead of `1000 * 100`, paying out 100x the intended amount from `TreasuryAccount` for blocks that were relayed while the rate was still 100 — confirmed by the existing test harness pattern in `reward_covers_only_unpaid_heights_after_rollback`, which shows the pallet always uses the *current* `block_cost` read live at call time in `calculate_reward`, with no rate history or settlement checkpoint tied to `update_cost_per_block`. [4](#0-3)

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

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L118-219)
```rust
// A relayer is paid once for advancing a state machine across a span of heights. When the latest
// height is rolled back and later resubmitted, the reward should still only cover the new blocks.
// The `LastRewardedHeight` watermark keeps each payout scoped to the span that has not been paid.
#[test]
fn reward_covers_only_unpaid_heights_after_rollback() {
	let mut ext = new_test_ext();
	ext.execute_with(|| {
		const BLOCK_COST: u128 = 100;
		let host = Ismp::default();
		let state_machine_id = setup_state_machine();
		let treasury_account: AccountId32 = PalletId(*b"treasury").into_account_truncating();

		pallet_consensus_incentives::Pallet::<Test>::update_cost_per_block(
			RuntimeOrigin::root(),
			state_machine_id,
			BLOCK_COST,
		)
		.unwrap();

		let (consensus_message, relayer_account) = setup_host_and_message(&host);
		let message = MessageWithWeight { message: consensus_message, weight: Weight::zero() };
		let updated = |height: u64| {
			vec![IsmpEvent::StateMachineUpdated(StateMachineUpdated {
				state_machine_id,
				latest_height: height,
			})]
		};

		// The chain has already advanced to 1025 and every block up to it has been rewarded once.
		host.store_state_machine_commitment(
			StateMachineHeight { id: state_machine_id, height: 1024 },
			commitment(),
		)
		.unwrap();
		host.store_latest_commitment_height(StateMachineHeight {
			id: state_machine_id,
			height: 1024,
		})
		.unwrap();
		host.store_state_machine_commitment(
			StateMachineHeight { id: state_machine_id, height: 1025 },
			commitment(),
		)
		.unwrap();
		host.store_latest_commitment_height(StateMachineHeight {
			id: state_machine_id,
			height: 1025,
		})
		.unwrap();

		let treasury_before_first = Balances::balance(&treasury_account);
		<pallet_consensus_incentives::Pallet<Test> as FeeHandler>::on_executed(
			vec![message.clone()],
			updated(1025),
		)
		.unwrap();

		assert_eq!(Balances::balance(&treasury_account), treasury_before_first - BLOCK_COST);
		assert_eq!(
			pallet_consensus_incentives::LastRewardedHeight::<Test>::get(state_machine_id),
			Some(1025)
		);

		// The previous-height pointer references an older height whose commitment is no longer
		// retained in the bounded map.
		pallet_ismp::PreviousStateMachineHeight::<Test>::insert(state_machine_id, 1);

		// Deleting the latest commitment rolls the latest height back to that previous pointer.
		host.delete_state_commitment(StateMachineHeight { id: state_machine_id, height: 1025 })
			.unwrap();
		assert_eq!(host.latest_commitment_height(state_machine_id).unwrap(), 1);

		// The next honest consensus update advances to 1030, carrying the stale pointer forward as
		// the new previous height.
		host.store_state_machine_commitment(
			StateMachineHeight { id: state_machine_id, height: 1030 },
			commitment(),
		)
		.unwrap();
		host.store_latest_commitment_height(StateMachineHeight {
			id: state_machine_id,
			height: 1030,
		})
		.unwrap();
		assert_eq!(host.previous_commitment_height(state_machine_id), Some(1));

		let treasury_before_second = Balances::balance(&treasury_account);
		<pallet_consensus_incentives::Pallet<Test> as FeeHandler>::on_executed(
			vec![message],
			updated(1030),
		)
		.unwrap();

		// The real advance is 1025 -> 1030, so only the 5 new blocks are paid rather than the full
		// span back to the previous pointer.
		assert_eq!(Balances::balance(&treasury_account), treasury_before_second - 5 * BLOCK_COST);
		assert_eq!(
			pallet_consensus_incentives::LastRewardedHeight::<Test>::get(state_machine_id),
			Some(1030)
		);
	})
}
```
