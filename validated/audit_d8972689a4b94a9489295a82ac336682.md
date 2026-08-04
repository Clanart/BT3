Confirmed: `remove_incentives` (governance-gated) and `update_cost_per_block` (governance-gated, `T::IncentivesOrigin`) both require a privileged origin, which conflicts with the Gate's requirement of an unprivileged-attacker-reachable path. The mechanism itself, however, is a genuine local analog of the "stale rate reused after off→on toggle" bug class, and the payout call (`claim_outbound_consensus_delivery_reward`-adjacent `process_message`/`FeeHandler::on_executed`) is triggered permissionlessly by relaying a consensus message — the windfall payout itself requires no privileged actor, only that governance toggled the cost at some point in the past (a normal, expected operational action, not an attacker capability).

### Title
Reward watermark not advanced while `StateMachinesCostPerBlock` is unset causes a lump-sum over-reward windfall on re-enable - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives` pays relayers a reward proportional to the number of new state-machine-commitment heights (`blocks`) delivered since the last-paid `LastRewardedHeight` watermark. When a state machine's `StateMachinesCostPerBlock` entry is removed (via `remove_incentives`) or was never set, `process_message` returns early and — critically — never advances `LastRewardedHeight`, even though `latest_commitment_height` keeps climbing as consensus updates continue to be processed by `pallet-ismp` in the background (updating consensus/state does not depend on the incentives pallet being configured). When cost-per-block is later restored via `update_cost_per_block`, the very next relayer to deliver a consensus message collects a reward for the *entire* span of blocks that accumulated during the disabled period in a single payout, rather than that span simply going unrewarded (as intended by disabling incentives).

### Finding Description
`calculate_reward` computes:
```
blocks = latest_height - baseline
reward = blocks * block_cost
```
where `baseline = LastRewardedHeight::get(...).unwrap_or(previous_height)` [1](#0-0) .

`process_message` only updates `LastRewardedHeight` inside the `if let Some(block_cost) = StateMachinesCostPerBlock::get(...)` branch [2](#0-1) . If the cost is `None` (via `remove_incentives`) the function returns `Ok(())` before ever touching `LastRewardedHeight`, so the watermark freezes at whatever height it was last paid to [3](#0-2) . Meanwhile, `update_client` in the core ISMP handler keeps advancing `latest_commitment_height` for every valid consensus proof regardless of whether the incentives pallet has a configured cost for that chain [4](#0-3) . This is exactly the pattern in the seed report: a value ("exchangeRate" there, `LastRewardedHeight`/cost-state here) that is supposed to be kept current is instead frozen while the vault/incentive is "off," then reused stale once turned back "on," producing an incorrect calculation (there: wrong share price; here: an inflated block-span reward).

### Impact Explanation
When incentives for a state machine are re-enabled, the first relayer who happens to submit a consensus proof (an unprivileged, permissionless action — anyone can relay) receives a treasury payout sized for the entire multi-epoch gap that accumulated while incentives were off, rather than the intended "no reward during the off period." This is an unintended treasury fund transfer far in excess of what any single delivery should earn — a logic/accounting flaw that drains the treasury account (`T::TreasuryAccount`) disproportionately to one relayer.

### Likelihood Explanation
The reward-claim path (`FeeHandler::on_executed` → `process_message`) fires automatically and permissionlessly on every processed consensus message [5](#0-4) , so no attacker privilege is needed to *trigger* the windfall once the precondition (a prior `remove_incentives`/never-configured cost, followed by `update_cost_per_block`) exists. That precondition itself is governance-only, which is why this does not meet the "unprivileged-only" bar for a standalone high-severity report, but it is a reproducible, code-provable flaw in fund accounting rather than a hypothetical.

### Recommendation
Always advance `LastRewardedHeight` to `latest_height` when processing a message, independent of whether `block_cost` is `Some` or `None`, so no back-dated span accrues while incentives are disabled. Alternatively, snapshot and persist `latest_commitment_height` at the moment `remove_incentives` is called, and use that snapshot as the new baseline when incentives are next enabled, so the "off" gap is provably excluded from reward calculation, mirroring the seed report's suggestion to keep the tracked value fresh across on/off transitions.

### Proof of Concept
1. Governance calls `update_cost_per_block(sm, cost)` for chain `X`; `LastRewardedHeight[X] = H0`.
2. Governance calls `remove_incentives(sm)` — `StateMachinesCostPerBlock[X]` becomes `None`.
3. Over the next `N` consensus updates, `pallet-ismp` advances `latest_commitment_height(X)` from `H0` to `H0+N` via `update_client`, independent of the incentives pallet [6](#0-5) . Each corresponding `process_message` call returns early without touching `LastRewardedHeight` (still `H0`).
4. Governance calls `update_cost_per_block(sm, cost)` again to resume incentives.
5. Any relayer submits the next consensus message; `FeeHandler::on_executed` → `process_message` runs with `baseline = H0` and `latest_height = H0+N+1`, paying `reward = (N+1) * cost` in a single transfer from the treasury account to that one relayer [7](#0-6)  — a lump-sum windfall for work spanning the entire disabled period, which should have earned zero reward. [3](#0-2)

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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L104-163)
```rust
impl<T: Config> FeeHandler for Pallet<T>
where
	<T as frame_system::Config>::AccountId: From<[u8; 32]>,
{
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

		// Return with actual weight information
		// We use Pays::No to indicate that someone (the message sender) doesn't pay for this
		// operation, though we're using this mechanism to reward relayers rather than charge fees
		Ok(PostDispatchInfo { actual_weight: None, pays_fee: Pays::No })
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

**File:** modules/ismp/core/src/handlers/consensus.rs (L29-84)
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
}
```
