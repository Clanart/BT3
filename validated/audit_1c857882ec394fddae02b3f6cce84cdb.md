### Title
Windfall relayer reward on first consensus update for a state machine due to zero-initialized `PreviousStateMachineHeight` - ([File: modules/pallets/ismp/src/host.rs, modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`pallet-consensus-incentives` pays relayers `(latest_height - baseline) * CostPerBlock` for delivering a `ConsensusMessage`, where `baseline` falls back to the ISMP host's `previous_commitment_height`. That watermark is stored in `PreviousStateMachineHeight`, which is zero-initialized the very first time a state machine's height is recorded via `store_latest_commitment_height`. This mirrors the reported `GaugeController` bug: a per-entity accumulator/watermark that isn't seeded to a meaningful starting value on first use, so the first delta computed against it is `full_value - 0` instead of the true incremental progress — enabling an outsized, one-time claim.

### Finding Description
`IsmpHost::store_latest_commitment_height` in [1](#0-0)  derives the "previous" watermark from whatever was already in `LatestStateMachineHeight`:

```rust
fn store_latest_commitment_height(&self, height: StateMachineHeight) -> Result<(), Error> {
    let previous_height = LatestStateMachineHeight::<T>::get(height.id).unwrap_or_default();
    PreviousStateMachineHeight::<T>::insert(height.id, previous_height);
    LatestStateMachineHeight::<T>::insert(height.id, height.height);
    Ok(())
}
```

For a state machine that has never had a height recorded, `LatestStateMachineHeight::get` returns `None`, so `previous_height` defaults to `0`. The very first time a consensus proof is verified for that chain (which can land at a large real-world height, e.g. millions of blocks after governance registers a new remote chain), `PreviousStateMachineHeight` is set to `0` and `LatestStateMachineHeight` is set to the actual (large) height.

`pallet-consensus-incentives::calculate_reward` in [2](#0-1)  then computes the reward:

```rust
let latest_height = host.latest_commitment_height(state_machine_id.clone())...;
let previous_height = host.previous_commitment_height(state_machine_id.clone()).unwrap_or_default();
let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);
let blocks = latest_height.saturating_sub(baseline);
let reward = blocks_as_balance.saturating_mul(block_cost);
```

`LastRewardedHeight` (this pallet's own watermark) is also `None` on the first reward for a state machine, so it falls back to `previous_height`, which — per the above — is `0` for a brand-new state machine's first recorded height. The result: `blocks = latest_height - 0 = latest_height`, i.e. the reward is computed as if the relayer advanced the chain by its entire absolute height value, not by the actual incremental progress the submitted `ConsensusMessage` represents.

This is structurally identical to the `GaugeController.add_gauge()` bug: a fresh entity's tracking variable starts at `0` instead of being seeded to the entity's true starting point, so the first delta computation (`new_value - 0`) yields the full absolute value rather than a genuine increment, and whoever triggers that first update captures the inflated difference.

### Impact Explanation
Any address that successfully submits the first valid `ConsensusMessage` for a newly registered state machine collects `latest_height * CostPerBlock` from the `TreasuryAccount`, via `T::Currency::transfer` in [3](#0-2) , instead of the reward for the true number of blocks the proof advances knowledge by. For any state machine onboarded at a non-trivial height (which is the normal case — real chains are rarely registered at genesis), this can drain the treasury by orders of magnitude more than intended for a single relayed message, since `CostPerBlock` is calibrated assuming `blocks` reflects genuine incremental progress, not an absolute height number. This is unauthorized/excessive fund transfer from the protocol treasury triggered by an ordinary, unprivileged relayer action — matching the bounty's "stealing or loss of funds" / "logic attacks" categories.

### Likelihood Explanation
No malicious relayer, prover, or admin collusion is required — it is triggered by the normal, expected first-use flow of `pallet-consensus-incentives` combined with `pallet-ismp`'s height-tracking initialization. Any relayer racing to be first to deliver a consensus proof for a newly configured state machine (which governance is expected to do periodically as new chains are onboarded) receives the windfall automatically. The `pallet_consensus_incentives::testsuite` test `reward_covers_only_unpaid_heights_after_rollback` in [4](#0-3)  only exercises the rollback/resubmission case where prior state already exists at height 1024/1025; it does not test the true first-ever update for a state machine, so this zero-baseline case appears untested and unguarded.

### Recommendation
Seed `PreviousStateMachineHeight` (and/or `LastRewardedHeight`) to the state machine's actual onboarding/genesis height rather than defaulting to `0` on first use — e.g., have the code path that first creates a consensus client / registers a state machine explicitly initialize `PreviousStateMachineHeight` to the initial height being trusted, so the first `calculate_reward` call measures a real incremental span instead of the entire absolute height. Alternatively, `calculate_reward` should special-case "no `LastRewardedHeight` and no genuine previous commitment" by treating the first update's rewardable span as `0` (or a governance-configured cap) rather than `latest_height - 0`.

### Proof of Concept
1. Governance adds a new state machine `X` via the consensus client (no height has ever been recorded for `X`, so `LatestStateMachineHeight::<T>::get(X)` is `None`).
2. A relayer submits the first valid `ConsensusMessage` proving `X` is at height `H = 5_000_000` (a realistic, already-advanced chain).
3. `store_latest_commitment_height` runs: `previous_height = None.unwrap_or_default() = 0`; sets `PreviousStateMachineHeight[X] = 0`, `LatestStateMachineHeight[X] = 5_000_000`.
4. `FeeHandler::on_executed` fires; `calculate_reward` computes `previous_height = host.previous_commitment_height(X).unwrap_or_default() = 0`; `LastRewardedHeight::get(X) = None`, so `baseline = 0`; `blocks = 5_000_000 - 0 = 5_000_000`.
5. `reward = 5_000_000 * CostPerBlock` is transferred from `TreasuryAccount` to the relayer's recovered account in one transaction — vastly more than the reward intended for delivering a single consensus update.

### Citations

**File:** modules/pallets/ismp/src/host.rs (L229-234)
```rust
	fn store_latest_commitment_height(&self, height: StateMachineHeight) -> Result<(), Error> {
		let previous_height = LatestStateMachineHeight::<T>::get(height.id).unwrap_or_default();
		PreviousStateMachineHeight::<T>::insert(height.id, previous_height);
		LatestStateMachineHeight::<T>::insert(height.id, height.height);
		Ok(())
	}
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
