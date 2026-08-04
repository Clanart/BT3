Confirmed: `store_latest_commitment_height` in `modules/pallets/ismp/src/host.rs` sets `PreviousStateMachineHeight` from whatever `LatestStateMachineHeight` currently holds — `unwrap_or_default()` (i.e. `0`) the very first time a state machine is registered [1](#0-0) . `previous_commitment_height` simply returns that stored value, `None` if never written [2](#0-1) . In `pallet-consensus-incentives`, `calculate_reward` uses `previous_height.unwrap_or_default()` (0) as fallback baseline when no `LastRewardedHeight` watermark exists yet, then pays `(latest_height - baseline) * cost_per_block` [3](#0-2) .

### Title
First consensus update for a newly registered state machine pays reward for the entire historical height span from 0 — treasury drain via zero-initialized baseline - (File: modules/pallets/consensus-incentives/src/impls.rs)

### Summary
The reported bug class is an uninitialized "last updated" watermark that defaults to zero and corrupts a subsequent delta-based calculation. The same primitive exists in `pallet-consensus-incentives`: the reward-baseline height defaults to `0` on the very first payout for any state machine, so the first relayer to submit a valid consensus message for a state machine that is bootstrapped at (or updated to) a large height is paid for the *entire* span from genesis to that height, not just the actual block progress the relay covered.

### Finding Description
`calculate_reward` computes:
```rust
let previous_height = host.previous_commitment_height(state_machine_id).unwrap_or_default(); // 0 if never set
let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);
let blocks = latest_height.saturating_sub(baseline);
let reward = blocks_as_balance.saturating_mul(block_cost);
``` [3](#0-2) 

`previous_commitment_height` reads `PreviousStateMachineHeight`, which is only ever written by `store_latest_commitment_height`, and on the *first* call for a state machine it is written as `LatestStateMachineHeight::<T>::get(height.id).unwrap_or_default()` = `0` [1](#0-0) . There is no genesis/registration step that initializes `PreviousStateMachineHeight` to the state machine's actual starting height (e.g. the height at which the consensus client was first configured). Consequently, when a state machine is first added to the network at a non-trivial height (e.g. an existing chain being bridged in mid-life, height = 5,000,000) and `StateMachinesCostPerBlock` is set before or at the same time as the first accepted `ConsensusMessage`, `on_executed` computes `blocks = latest_height - 0 = 5,000,000` and pays `5,000,000 * cost_per_block` to the relayer who happened to submit that first message [4](#0-3) .

This is functionally identical to the reported `lastUpdatedDay` bug: a watermark that should represent "the point already accounted for" instead defaults to the origin (`0`/`day 0`), so the delta calculation blows up on first use. Here the analog is not an infinite loop but an unbounded one-time reward inflation, because `blocks` is a `u64` multiplied against `cost_per_block`, with only `saturating_mul` bounding it at the balance type's max rather than at any sane per-update cap.

### Impact Explanation
This directly causes loss of funds from the protocol treasury: `T::Currency::transfer(&TreasuryAccount, &relayer_account, reward, ...)` moves the inflated reward out of the treasury to an unprivileged consensus-relayer signer [5](#0-4) . Any account capable of producing a valid signed `ConsensusMessage` (a permissionless, unprivileged relayer role per the pallet's own design) can trigger this by being first to relay for a newly onboarded/re-onboarded state machine, receiving a reward proportional to the full historical height rather than the actual work of relaying one consensus update. This matches the bounty's "stealing or loss of funds" / "logic attacks" criteria and does not require a malicious peer, prover, or admin — it is triggered by ordinary, honest relaying of the first consensus update.

### Likelihood Explanation
Likelihood is moderate-to-high in practice: it fires deterministically any time governance configures `StateMachinesCostPerBlock` for a state machine whose commitment height starts non-trivially above 0 (the common case — bridged chains are almost never onboarded at height 0), and the reward is realized by the very first successful, ordinary consensus-relay operation. No race condition, front-running, or privileged access is required; it is a straightforward one-shot consequence of the zero-default baseline. The existing test suite (`reward_covers_only_unpaid_heights_after_rollback`) demonstrates the pallet authors reasoned carefully about rollback double-payment, but nowhere tests or guards the "genesis baseline" first-registration case [6](#0-5) .

### Recommendation
Initialize `LastRewardedHeight` (or the underlying `PreviousStateMachineHeight`) to the state machine's actual starting height at the time the consensus client/state machine is first registered, instead of relying on `unwrap_or_default()` → 0. Alternatively, in `calculate_reward`, treat "no prior watermark" as "reward only the delta since `update_cost_per_block` was first configured" or since the state machine's genesis commitment height (recorded explicitly at registration), never falling back to `0` when the true starting height is nonzero.

### Proof of Concept
1. Register a new `StateMachineId` on Hyperbridge whose first consensus update stores `LatestStateMachineHeight = 5,000,000` (a chain onboarded mid-life) via `store_latest_commitment_height`; per `modules/pallets/ismp/src/host.rs:229-234`, `PreviousStateMachineHeight` is set to `0` on this first write.
2. Governance calls `update_cost_per_block(state_machine_id, cost_per_block)` to set a nonzero per-block reward [7](#0-6) .
3. Any relayer submits a signed `ConsensusMessage` that gets accepted and dispatches `IsmpEvent::StateMachineUpdated { state_machine_id, latest_height: 5_000_000 }`.
4. `on_executed` → `process_message` → `calculate_reward`: `previous_height = previous_commitment_height(...).unwrap_or_default() = 0`; `LastRewardedHeight` is `None` so `baseline = 0`; `blocks = 5_000_000 - 0 = 5_000_000`; `reward = 5_000_000 * cost_per_block`, transferred from the treasury to the relayer in a single call [3](#0-2) .
5. This mirrors the test file's existing pattern (`pallet_consensus_incentives.rs:118-219`) but with `previous_height`/baseline left at its default-0 state instead of a realistic prior height — reproducible by omitting the pre-seeding of `PreviousStateMachineHeight`/`LastRewardedHeight` before the first `on_executed` call.

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

**File:** modules/pallets/ismp/src/host.rs (L337-339)
```rust
	fn previous_commitment_height(&self, id: StateMachineId) -> Option<u64> {
		PreviousStateMachineHeight::<T>::get(id)
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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L82-99)
```rust
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
