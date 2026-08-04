## Title
Stale-Rate Reward Inflation in `pallet-consensus-incentives` — `update_cost_per_block` Changes the Rate Without Accruing the Pending Block Span First - (File: `modules/pallets/consensus-incentives/src/lib.rs`)

### Summary
`pallet-consensus-incentives` pays relayers `(latest_height - LastRewardedHeight) * block_cost` for delivering `ConsensusMessage`s. `block_cost` is a single mutable storage value (`StateMachinesCostPerBlock`) that governance updates via `update_cost_per_block`, and `LastRewardedHeight` is only a watermark, not a per-interval ledger. Because the rate is read at *payout* time rather than accrued at each rate-change boundary, any block span that has accumulated since the last payout but is unpaid at the moment governance changes the rate gets retroactively re-priced at the *new* rate — exactly the invariant break described in the seed report (StRSR's `_payoutRewards` must run before `setRewardRatio`/`setRewardPeriod`).

### Finding Description
The reward formula lives in `calculate_reward`: [1](#0-0) 

`block_cost` is fetched fresh from `StateMachinesCostPerBlock` in `process_message` at the moment a consensus message is processed, not fixed at accrual time: [2](#0-1) 

`update_cost_per_block` simply overwrites the rate with no prior settlement of the pending unrewarded span: [3](#0-2) 

Sequence that breaks the invariant:
1. Chain advances from height H0 to H1 while `block_cost = C_old`. No consensus message has been submitted yet, so `LastRewardedHeight` is still at H0 (the span H0→H1 has accrued at `C_old` but is unpaid).
2. Governance calls `update_cost_per_block` to raise the rate to `C_new` (a routine, benign parameter tune — no accrual step exists to prevent this).
3. Any relayer (unprivileged) submits the next valid consensus message that advances the chain to H2. `calculate_reward` computes `(H2 - H0) * C_new`, applying the *new*, higher rate to the entire span including the H0→H1 portion that should have been priced at `C_old`.
4. The treasury pays out more than intended for blocks that predate the rate change; the reverse (rate cut) analogously underpays whoever was owed the old-rate span.

This mirrors the report's core broken invariant precisely: a slope/rate change is applied retroactively to an already-elapsed, unaccrued interval because there is no `_payoutRewards()`-equivalent checkpoint before the parameter mutation.

### Impact Explanation
This directly threatens `TreasuryAccount` funds: `T::Currency::transfer` moves treasury balance to relayers using a rate that never applied to the pending span, so the treasury systematically overpays (or underpays) every time governance retunes `cost_per_block` while there is an outstanding unrewarded block span. Since consensus updates for any given `state_machine_id` are frequent and asynchronous relative to governance parameter changes, an unrewarded span almost always exists at the moment of a rate change, making this a reliable, repeatable loss-of-funds vector against the treasury rather than a theoretical edge case.

### Likelihood Explanation
High. No special conditions are needed beyond the routine, expected operational action of retuning `cost_per_block` (a normal governance maintenance task, not an attack) combined with the normal, permissionless act of any relayer submitting the next consensus message — something relayers do continuously to earn rewards. The bug fires on the very next `on_executed` call after any rate change if a pending span exists, which is the common case given consensus messages arrive on their own cadence independent of governance actions.

### Recommendation
Before mutating `StateMachinesCostPerBlock` in `update_cost_per_block` (and in `remove_incentives`), settle/accrue the reward for the pending unrewarded span at the *current* rate, advancing `LastRewardedHeight` to the current `latest_commitment_height` for that state machine, exactly as the seed report recommends calling `_payoutRewards()` before `setRewardRatio`/`setRewardPeriod`. Only after that checkpoint should the new rate take effect for subsequently accrued blocks.

### Proof of Concept
1. Set `StateMachinesCostPerBlock[SM] = 100` via `update_cost_per_block`.
2. Advance `SM`'s commitment height from 1000 to 1100 (no consensus message processed yet — `LastRewardedHeight` stays at 1000, matching the pattern shown in the existing rollback test): [4](#0-3) 
3. Governance calls `update_cost_per_block(SM, 100_000)` — no accrual happens, `StateMachinesCostPerBlock` is simply overwritten (`modules/pallets/consensus-incentives/src/lib.rs:140-142`).
4. Any relayer submits a valid `ConsensusMessage` that triggers `on_executed` with `latest_height = 1100`; `calculate_reward` pays `(1100-1000) * 100_000 = 10,000,000` instead of the correct `100 * 100_000... ` wait: correctly should be `100 (blocks under old rate) * 100 (old rate) = 10,000` — the relayer instead receives `100 * 100,000 = 10,000,000`, a 1000x overpayment funded entirely by `TreasuryAccount`.

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

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L121-167)
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

```
