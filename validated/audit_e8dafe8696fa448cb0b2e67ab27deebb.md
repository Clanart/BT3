## Title
Consensus relayer reward retroactively applies the current `cost_per_block` rate to the entire unpaid block span, allowing an unprivileged relayer to over-collect treasury funds by delaying submission across a rate change - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

## Summary
This is a structural analog of the Fraxlend "penalty rate applied since last call, including the pre-maturity period" bug. In `pallet-consensus-incentives`, `calculate_reward` pays a relayer `blocks * block_cost` where `blocks = latest_height - baseline` and `block_cost` is whatever `StateMachinesCostPerBlock` currently holds — the *current* rate is applied uniformly across the whole unpaid span, even if part of that span accrued under a different (lower) rate. Exactly like the Fraxlend borrower who avoided calling `addInterest()` before maturity and got charged the post-maturity penalty rate retroactively, a relayer here can avoid submitting a consensus update until governance raises `cost_per_block`, then submit a single update spanning old + new blocks and collect the new (higher) rate for the entire backlog, draining excess funds from the treasury.

## Finding Description
`calculate_reward` computes the reward span and rate as follows: [1](#0-0) 

- `baseline` is `LastRewardedHeight` (or `previous_commitment_height` on first payout).
- `blocks = latest_height.saturating_sub(baseline)`.
- `reward = blocks * block_cost`, where `block_cost` is read fresh from `StateMachinesCostPerBlock` at call time — the *current* value, not a historical, per-interval value.

There is no mechanism that segments the `[baseline, latest_height]` span by the periods during which different `cost_per_block` values were in effect. `update_cost_per_block` simply overwrites the stored rate: [2](#0-1) 

Consensus message submission (and therefore triggering `on_executed`/`process_message`/`calculate_reward`) is a permissionless, relayer-initiated action — any account can submit a valid consensus proof via `pallet_ismp::Pallet::handle_unsigned`. Nothing forces a relayer to submit promptly; `LastRewardedHeight` only advances when a message is actually processed: [3](#0-2) 

So if a relayer withholds submission while blocks `[baseline, X]` accrue under `cost_per_block = C1`, and then governance later raises the rate to `C2` for legitimate reasons (e.g., increased infra costs), the relayer can submit one consensus update covering `[baseline, X']` (where `X' > X`), and `calculate_reward` will pay `(X' - baseline) * C2` — over-paying for the `[baseline, X]` portion that should have been priced at `C1`. This is the direct structural analog of the FraxlendPair bug: the "penalty"/current rate is applied to a segment of time/height that occurred before the rate changed, because there is no per-segment interest/reward accounting, only a single watermark and a single "current rate" lookup.

## Impact Explanation
This causes direct loss of treasury funds to an unprivileged actor (any account capable of submitting a valid, signed consensus message satisfies the "relayer" role in `on_executed`, no allow-list or staking is required in the reward pathway itself). The relayer does not need to collude with governance, front-run any transaction, or corrupt any proof — they only need to time an otherwise-honest, valid consensus submission after a publicly known parameter change. The overpayment amount is `(X - baseline) * (C2 - C1)`, which scales with both the length of the withheld backlog and the size of the rate increase, and is paid straight out of `T::TreasuryAccount`: [4](#0-3) 

## Likelihood Explanation
Likelihood is moderate-to-high in any deployment where `update_cost_per_block` is expected to be adjusted over time (the pallet explicitly supports rate changes via a dedicated extrinsic and emits `StateMachineCostPerBlockUpdated`, implying rate changes are a normal, anticipated operational event). Any relayer only needs to observe the on-chain rate and choose submission timing — no special access, race condition, or malicious peer is required.

## Recommendation
Segment reward computation by the periods during which each `cost_per_block` value was in effect, e.g., record `(height, cost_per_block)` checkpoints when the rate changes, and when computing `calculate_reward`, sum `Σ (min(latest_height, checkpoint_end) - max(baseline, checkpoint_start)) * rate_at_checkpoint` over all checkpoints overlapping `[baseline, latest_height]`, analogous to the Fraxlend mitigation of applying the correct rate to each sub-interval instead of the current rate to the whole elapsed span.

## Proof of Concept
1. Governance calls `update_cost_per_block(state_machine_id, C1 = 10)`.
2. Chain state machine height advances from `H0` to `H0 + 1000` (1000 blocks) with no relayer submitting a consensus update (`LastRewardedHeight` stays at `H0`).
3. Governance calls `update_cost_per_block(state_machine_id, C2 = 1000)` for a legitimate operational reason.
4. Relayer submits a single consensus proof advancing the light client to `H0 + 1000`.
5. `on_executed` → `process_message` → `calculate_reward` computes `blocks = 1000`, `block_cost = C2 = 1000`, reward `= 1,000,000` — paid entirely at the new rate even though 1000 of those blocks accrued while the rate was `C1 = 10` (expected fair payout would have been `1000 * 10 = 10,000`).
6. Confirmed by `test_incentivize_relayer` and `reward_covers_only_unpaid_heights_after_rollback` in `modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs`, which show `calculate_reward` always multiplies the entire unpaid block span by a single, current `block_cost` value with no historical segmentation. [5](#0-4)

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
