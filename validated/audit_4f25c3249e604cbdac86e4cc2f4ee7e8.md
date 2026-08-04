## Analysis

The external report's core bug pattern: a newly-added participant's accounting checkpoint is initialized to `0` instead of the current accumulator value, so the very first settlement against that participant is computed as `current - 0` and pays out the *entire historical backlog* to whoever triggers it first.

Hyperbridge has a structurally identical bug in `pallet-consensus-incentives`, the pallet that pays relayers `$BRIDGE` from the treasury for delivering consensus updates.

**The corrupted value:** `PreviousStateMachineHeight` for a state machine that has never had a commitment stored before. [1](#0-0) 

`store_latest_commitment_height` sets `PreviousStateMachineHeight::<T>::insert(height.id, previous_height)` where `previous_height = LatestStateMachineHeight::<T>::get(height.id).unwrap_or_default()`. For a state machine whose consensus client is being onboarded for the first time (or whose first `StateMachineUpdated` ever lands), `LatestStateMachineHeight` doesn't exist yet, so `previous_height` defaults to `0` — even though the real remote chain may already be at a very large block height (Ethereum, an established parachain, etc.).

`calculate_reward` in the incentives pallet uses exactly this value as its fallback baseline: [2](#0-1) 

```rust
let previous_height = host.previous_commitment_height(state_machine_id.clone()).unwrap_or_default();
let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);
let blocks = latest_height.saturating_sub(baseline);
let reward = blocks_as_balance.saturating_mul(block_cost);
```

`LastRewardedHeight` is also `None` on first payout, so `baseline = previous_height = 0`. The reward becomes `latest_height * block_cost` — the entire historical block range from genesis to the first-ever tracked height, paid in a single transfer from the treasury.

This is triggered through a fully permissionless path: `update_client` (called via any signed `ConsensusMessage`) is public, requires no special role, and is exactly the entrypoint `on_executed`/`process_message` reacts to. [3](#0-2) 

The existing regression test only covers the *rollback* case (baseline correctly bounded to a recent previous height); it never exercises the true first-ever-commitment case where `previous_commitment_height` is the hard-coded `0` default rather than a real prior height: [4](#0-3) 

### Title
Treasury drain via zero-initialized `PreviousStateMachineHeight` baseline on first consensus reward - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives::calculate_reward` pays a relayer `(latest_height - baseline) * block_cost` in `$BRIDGE` from the treasury for delivering a consensus update. `baseline` falls back to `previous_commitment_height`, which itself falls back to `0` in `pallet-ismp`'s `store_latest_commitment_height` whenever no prior `LatestStateMachineHeight` is recorded for that state machine — i.e., on the very first commitment ever stored for it. Since real remote chains are onboarded at large starting heights, the first relayer to deliver a consensus proof after governance configures `StateMachinesCostPerBlock` for that chain is rewarded for the entire historical range `0..latest_height` in a single payout.

### Finding Description
`process_message`/`calculate_reward` compute the reward span as:
```
baseline = LastRewardedHeight.get(id).unwrap_or(previous_commitment_height)
blocks = latest_height - baseline
reward = blocks * block_cost
```
`previous_commitment_height` is written by `store_latest_commitment_height`, which derives it as `LatestStateMachineHeight.get(id).unwrap_or_default()` — `0` when the state machine has no prior recorded height. This is exactly true the first time a consensus update for a given `state_machine_id` is ever accepted (fresh onboarding, or a chain whose consensus client was only just created). No code path seeds `PreviousStateMachineHeight`/`LastRewardedHeight` to the chain's actual starting height when the consensus client or the reward configuration is created.

### Impact Explanation
A single legitimate, permissionless consensus-message delivery can drain the treasury of up to `latest_height * block_cost` in `$BRIDGE`, since `latest_height` for a freshly-tracked remote chain can already be in the millions. This is unauthorized/incorrect fund distribution from the treasury — no malicious relayer, prover, or governance action beyond the routine act of setting a per-block reward is required; the exploit is simply "be the relayer who delivers the first consensus update after rewards are enabled."

### Likelihood Explanation
High whenever a new state machine's consensus incentives are turned on: `update_cost_per_block` is the normal governance workflow for enabling relayer rewards on a chain, and any relayer racing to deliver the first consensus proof afterward collects the full historical backlog automatically — this is the expected/default outcome of the current code, not an edge case requiring special conditions.

### Recommendation
When enabling rewards for a state machine (in `update_cost_per_block`) or when a consensus client/state machine is first tracked, seed `LastRewardedHeight` to the chain's current `latest_commitment_height` (not `previous_commitment_height`, and never let it default to `0`) so that the first reward only covers blocks produced *after* incentives were turned on.

### Proof of Concept
1. Governance creates a consensus client for a new remote chain and later calls `update_cost_per_block(state_machine_id, cost)`.
2. A relayer submits the first-ever `ConsensusMessage` for that chain; `update_client` stores a commitment at height `H` (e.g., `H = 5_000_000`), and `store_latest_commitment_height` sets `PreviousStateMachineHeight = 0` (no prior value existed).
3. `on_executed` → `process_message` → `calculate_reward`: `LastRewardedHeight` is `None`, so `baseline = previous_commitment_height = 0`; `blocks = 5_000_000`; `reward = 5_000_000 * cost`.
4. `T::Currency::transfer(treasury, relayer, reward)` executes, potentially exceeding the treasury balance intended for ordinary incremental rewards, in one transaction. [5](#0-4)

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

**File:** modules/ismp/core/src/handlers/consensus.rs (L29-83)
```rust
pub fn update_client<H>(host: &H, msg: ConsensusMessage) -> Result<MessageResult, anyhow::Error>
where
	H: IsmpHost,
{
	let consensus_client_id = host.consensus_client_id(msg.consensus_state_id).ok_or(
		Error::ConsensusStateIdNotRecognized { consensus_state_id: msg.consensus_state_id },
	)?;
	let consensus_client = host.consensus_client(consensus_client_id)?;
	let trusted_state = host.consensus_state(msg.consensus_state_id)?;
	host.is_consensus_client_frozen(msg.consensus_state_id)?;
	host.is_expired(msg.consensus_state_id)?;

	let (new_state, intermediate_states) = consensus_client.verify_consensus(
		host,
		msg.consensus_state_id,
		trusted_state,
		msg.consensus_proof,
	)?;
	host.store_consensus_state(msg.consensus_state_id, new_state)?;
	let timestamp = host.timestamp();
	host.store_consensus_update_time(msg.consensus_state_id, timestamp)?;
	let mut state_updates = vec![];
	for (id, mut commitment_heights) in intermediate_states {
		commitment_heights.sort_unstable_by(|a, b| a.height.cmp(&b.height));
		let previous_latest_height = host.latest_commitment_height(id)?;
		let mut last_commitment_height = None;
		for commitment_height in commitment_heights.iter() {
			let state_height = StateMachineHeight { id, height: commitment_height.height };

			// Only allow heights greater than latest height
			if previous_latest_height > commitment_height.height {
				continue;
			}

			// Skip duplicate states
			if host.state_machine_commitment(state_height).is_ok() {
				continue;
			}

			last_commitment_height = Some(state_height);
			host.store_state_machine_commitment(state_height, commitment_height.commitment)?;
			host.store_state_machine_update_time(state_height, host.timestamp())?;
		}

		if let Some(latest_height) = last_commitment_height {
			let latest_height = StateMachineHeight { id, height: latest_height.height };
			state_updates.push(Event::StateMachineUpdated(StateMachineUpdated {
				state_machine_id: id,
				latest_height: latest_height.height,
			}));
			host.store_latest_commitment_height(latest_height)?;
		}
	}

	Ok(MessageResult::ConsensusMessage(state_updates))
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
