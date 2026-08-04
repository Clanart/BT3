Confirmed: `StateMachinesCostPerBlock` supports `Some(0)` (any value including zero can be set via `update_cost_per_block`, distinct from `remove_incentives` which sets it to `None`), and `LastRewardedHeight` is only updated inside the `if reward.is_zero() { return Ok(()); }` early-return branch — i.e., skipped precisely when it's most needed.

### Title
Consensus-incentives reward watermark is not advanced when `reward.is_zero()`, causing overpaid treasury payout after a zero-cost window ends - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`Pallet::process_message` in `modules/pallets/consensus-incentives/src/impls.rs` only advances the `LastRewardedHeight` watermark inside the branch that actually transfers a reward. When `calculate_reward` returns zero — which happens whenever `StateMachinesCostPerBlock` is configured to `Some(0)` — the function returns early without updating the watermark, exactly mirroring the `tryInflation()` pattern in the external report where storage was only updated `if inflation > 0`. Real chain progress that happens during the zero-cost window is never checked off. When the cost is later restored to a non-zero value, the very next (fully permissionless, unprivileged) relayer who submits a valid consensus message is retroactively paid for the entire dormant span at the new rate, draining the treasury far beyond the intended incentive.

### Finding Description
`process_message` computes the reward and only touches storage when a payout actually occurs: [1](#0-0) 

```rust
fn process_message(...) -> Result<(), Error<T>> {
	if let Some(block_cost) = StateMachinesCostPerBlock::<T>::get(state_machine_id) {
		let reward = Self::calculate_reward(&state_machine_id, block_cost)?;

		if reward.is_zero() {
			return Ok(());
		}
		... // transfer, mint, event
		LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
			*watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
		});
	}
	Ok(())
}
```

`calculate_reward` derives `reward = blocks * block_cost`, where `blocks = latest_height - baseline` and `baseline = LastRewardedHeight::get(...).unwrap_or(previous_height)`: [2](#0-1) 

`block_cost` comes straight from `StateMachinesCostPerBlock`, which `update_cost_per_block` can set to **any** value including `0` (as opposed to `remove_incentives`, which clears the entry to `None`): [3](#0-2) 

So the sequence is:
1. Governance sets `StateMachinesCostPerBlock[id] = Some(0)` (e.g. to pause incentive spend during an incident) while chain height for `id` keeps advancing normally via permissionless `ConsensusMessage` submissions (this is the normal, unprivileged relayer flow already exercised by `test_incentivize_relayer`).
2. Every call to `process_message` during this window computes `reward = blocks * 0 = 0`, hits `reward.is_zero()`, and returns before the `LastRewardedHeight::mutate` call — the watermark keeps pointing at the old height.
3. Governance later restores `StateMachinesCostPerBlock[id] = Some(cost)` with `cost > 0`.
4. The next unprivileged relayer who submits **any** valid consensus message (even for a single-block advance) triggers `calculate_reward` with `baseline` still frozen at the pre-pause height and `latest_height` at the current (much higher) height. `blocks` therefore spans the entire dormant period, and `reward = blocks * cost` is paid out of the treasury to that one relayer in a single transaction — vastly more than the actual work (one block) they delivered.

This is the direct analog of the reported bug: `tryInflation()` skipped updating `lastKnownTimestamp`/`lastKnownMultiplier` whenever `inflation == 0` (because `totalSupply() == 0`), so a later non-zero-supply call retroactively applied the entire skipped multiplier drift. Here, `process_message` skips updating `LastRewardedHeight` whenever `reward == 0` (because `block_cost == 0`), so a later non-zero-cost call retroactively pays the entire skipped block span.

### Impact Explanation
This is a direct loss-of-funds bug against the `TreasuryAccount`: an unprivileged relayer — simply by being the first to submit a normal, valid consensus message after the cost is un-paused — receives a reward transfer sized to the full dormant block span rather than the single block they actually delivered. Depending on how long the zero-cost window lasted and the state machine's height delta, this can drain a disproportionate, attacker-uncontrolled-but-attacker-benefiting amount from the treasury in one `on_executed` call, which matches the bounty's "stealing or loss of funds" / "logic attacks" impact category.

### Likelihood Explanation
The precondition (governance toggling `StateMachinesCostPerBlock` to `0` and back) is a plausible, even expected, operational action — analogous to the original report's "protocol paused, supply is 0" scenario, which was still accepted as valid. Crucially, no malicious relayer, prover, or governance behavior is required to trigger the overpayment itself: the exploit path is completed by an ordinary relayer submitting an ordinary, honestly-verified consensus message, which is the pallet's normal permissionless entrypoint (`pallet_ismp::Pallet::handle_unsigned` → `FeeHandler::on_executed` → `process_message`).

### Recommendation
Update `LastRewardedHeight` to the new `latest_height` whenever the state machine has genuinely advanced (`blocks > 0`), regardless of whether `reward` happens to be zero because `block_cost == 0`. Concretely, move the watermark update outside of the `reward.is_zero()` early return, guarding it on `latest_height > baseline` instead, and only skip the treasury `transfer`/mint when `reward == 0`:

```rust
let reward = Self::calculate_reward(&state_machine_id, block_cost)?;

LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
    *watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
});

if reward.is_zero() {
    return Ok(());
}
// ... transfer + mint as before
```

### Proof of Concept
Extending the existing test harness pattern from `reward_covers_only_unpaid_heights_after_rollback`: [4](#0-3) 

1. `update_cost_per_block(root, id, 0)` — pause incentives.
2. Advance `latest_commitment_height`/`store_state_machine_commitment` for `id` from height `H0` to `H1` (simulating real consensus progress during the pause), and call `on_executed` with a `StateMachineUpdated{latest_height: H1}` event + a signed `ConsensusMessage`. Assert `Balances::balance(treasury)` unchanged and `LastRewardedHeight::get(id)` still `None`/unchanged (confirms the skip).
3. `update_cost_per_block(root, id, BLOCK_COST)` — resume incentives.
4. Advance height by one more block to `H1+1` and call `on_executed` again with a fresh signed message from any relayer.
5. Assert the treasury debit equals `(H1+1 - baseline) * BLOCK_COST` where `baseline` is the pre-pause watermark (i.e., the relayer is paid for the entire `H0..H1+1` span in one shot), demonstrating the overpayment versus the expected single-block reward `BLOCK_COST`.

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

**File:** modules/pallets/consensus-incentives/src/lib.rs (L128-166)
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

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L121-219)
```rust
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
