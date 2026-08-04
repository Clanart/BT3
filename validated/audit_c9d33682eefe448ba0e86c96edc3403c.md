### Title
Relayer reward accounting uses the post-update cost-per-block rate to pay for blocks span that accrued under the old rate — (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives` pays relayers for state-machine consensus updates by multiplying an unpaid block span (`latest_height - watermark`) by the *currently configured* `StateMachinesCostPerBlock`. The watermark (`LastRewardedHeight`) is only advanced inside `process_message`/`on_executed`, i.e. it is settled lazily, on the next relayed message — never when governance calls `update_cost_per_block` to change the rate. This is the exact broken invariant from the Sherlock report: a rate parameter is mutated without first "closing out" (settling) the interval that accrued under the old rate, so the next settlement silently applies the *new* rate to a span that should have been priced at the *old* rate.

### Finding Description
`calculate_reward` computes the payout as: [1](#0-0) 

```
baseline = LastRewardedHeight::get(state_machine_id).unwrap_or(previous_height)
blocks   = latest_height - baseline
reward   = blocks * StateMachinesCostPerBlock::get(state_machine_id)   // <- current rate
```

`LastRewardedHeight` is only advanced from `process_message`, which runs as part of `FeeHandler::on_executed` when a consensus message is actually processed: [2](#0-1) 

There is no code path that settles/pays out the pending span at the *old* rate before `StateMachinesCostPerBlock` is overwritten by the admin `update_cost_per_block` extrinsic (confirmed via the testsuite calling it directly to set the rate before any messages are processed): [3](#0-2) 

Consequently, whenever `StateMachinesCostPerBlock` is changed while a state machine has an unpaid block span sitting behind `LastRewardedHeight` (which is the normal, always-present condition since the watermark only moves on message delivery, and message delivery can lag block production by any amount of time), the *entire* pending span — accrued at the *old* rate — gets paid out at the *new* rate the moment the next consensus message lands. This mirrors the `IncentiveGauge._upsertIncentive()` bug exactly: the rate is swapped without first calling the equivalent of `_updatePoolByPid()` to flush the interval that elapsed under the previous rate.

### Impact Explanation
This is a direct fund-safety bug against the pallet's `TreasuryAccount`:
- If the rate is raised, the very next relayer to deliver a consensus update for that state machine collects a reward computed over the whole unpaid span at the higher rate, over-paying the treasury for blocks that were priced lower when they were produced — a logic-driven loss of treasury funds to an ordinary, non-malicious relayer.
- If the rate is lowered, relayers are underpaid for blocks that accrued at the higher rate, silently shortchanging honest relayers.

No malicious relayer, prover, or admin action is required — the admin's `update_cost_per_block` call is itself a routine, expected governance operation, and the party who benefits/loses is simply whichever relayer happens to deliver the next honest consensus proof. The `Currency::transfer` in `process_message` moves real funds out of `TreasuryAccount`: [4](#0-3) 

so the mispriced payout is an actual, irreversible on-chain fund transfer, not just an accounting artifact.

### Likelihood Explanation
Likelihood is high in normal operation: `StateMachinesCostPerBlock` is expected to be periodically retuned by governance (cost-per-block for a chain naturally drifts), and `LastRewardedHeight` virtually always trails `latest_commitment_height` by some span because rewards settle only when a relayer happens to submit the next consensus message, not continuously. Any rate change that lands while such a lag exists (the common case) triggers the mispricing on the very next settlement — no special timing or race condition beyond ordinary operational cadence is needed.

### Recommendation
Before overwriting `StateMachinesCostPerBlock`, settle the currently pending span at the old rate: for every state machine affected by the rate change (or lazily, by recording the rate that was in effect for each unpaid span rather than always reading the *current* rate), pay out `(latest_height - LastRewardedHeight) * old_rate` and advance the watermark, mirroring the fix applied upstream (call the settlement step unconditionally/before the rate mutation, analogous to moving `_updatePoolByPid()` outside the `if` in `IncentiveGauge._upsertIncentive()`). Alternatively, store `(rate, effective_from_height)` history and compute `calculate_reward` as a sum over rate segments instead of a single current-rate multiplication.

### Proof of Concept
1. Governance calls `update_cost_per_block(root, sm_id, 100)`. A relayer delivers a consensus message advancing `sm_id` from height 1000 to 1100 but the message is delayed/batched so it isn't processed immediately (or an earlier message already advanced state without yet triggering `on_executed`, e.g. via `store_latest_commitment_height` outside the fee-handler path as shown in the rollback test).
2. Before any relayer message is processed against this pending span, governance calls `update_cost_per_block(root, sm_id, 100_000)` (e.g. legitimately re-pricing a congested chain).
3. A relayer now submits (or a previously in-flight) consensus message that triggers `on_executed` → `process_message` → `calculate_reward`. `baseline` is still the old `LastRewardedHeight` (e.g. height 1000), `latest_height` is 1100, so `blocks = 100`, but the reward is computed as `100 * 100_000` instead of `100 * 100`, a 1000x overpayment drawn straight from `TreasuryAccount` to the relayer, as seen in the existing test harness pattern: [5](#0-4) 

This test already demonstrates the reward/treasury debit mechanics (`Balances::balance(&treasury_account)` before/after `on_executed`) that a rate-change-during-unpaid-span scenario would exploit to over- or under-pay.

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

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L94-99)
```rust
		pallet_consensus_incentives::Pallet::<Test>::update_cost_per_block(
			RuntimeOrigin::root(),
			state_machine_id,
			100,
		)
		.unwrap();
```

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L121-179)
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
```
