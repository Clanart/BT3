No clawback mechanism links `pallet-fishermen`'s veto path back to `pallet-consensus-incentives`' reward ledger. That confirms the analog: rewards are paid out irreversibly the instant a consensus update is accepted, with no mechanism to reclaim them if the underlying state commitment is later vetoed within its challenge period.

### Title
Consensus relayer rewards are minted and paid before the challenge period elapses, with no clawback on veto - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`pallet-consensus-incentives::on_executed` pays the full block-span reward to a relayer the instant a `ConsensusMessage` is accepted by `update_client`, using `latest_commitment_height`/`previous_commitment_height` that have *not yet* passed the configured `challenge_period`. The intermediate state commitments those heights represent are provisional: the protocol's own documentation states fishermen have the entire `challenge_period` to veto them via `delete_state_commitment`, and the pallet-fishermen veto path never claws back or reverses any consensus-incentives payout. This is the same "reward computed too early" defect as the seed report — value is transferred out of the treasury before the work it is paying for (a canonical, un-vetoed state commitment) has actually been confirmed.

### Finding Description
`update_client` in [1](#0-0)  verifies the consensus proof, stores each new intermediate `StateMachineCommitment`/`update_time`, advances `LatestStateMachineHeight`, and returns `MessageResult::ConsensusMessage(state_updates)` — all synchronously, with the state commitments immediately marked "latest" even though they are only usable by relayers for request/response delivery once `challenge_period` has elapsed (see [2](#0-1)  and the design note in [3](#0-2) : "fishermen will check if these pending StateCommitments describe valid states... If the challenge_period elapses without any fraud proofs being presented, we can safely conclude that the provided StateCommitments are indeed canonical").

Immediately after that message result, `pallet_ismp`'s message-processing pipeline invokes the configured `FeeHandler::on_executed`, which for consensus messages is `pallet-consensus-incentives`: [4](#0-3) 

`process_message`/`calculate_reward` compute `reward = (latest_height - baseline) * cost_per_block` and immediately `T::Currency::transfer` it from `TreasuryAccount` to the relayer, then permanently advance the `LastRewardedHeight` watermark: [5](#0-4) 

`LastRewardedHeight` only ever moves forward (`watermark.max(state_machine_height.height)`), and there is no code path anywhere in the repository that reduces this watermark or reverses the treasury transfer when the corresponding state commitment is later vetoed. The veto path, `delete_state_commitment`, only rewinds `LatestStateMachineHeight` back to `PreviousStateMachineHeight` in [6](#0-5)  — it does not touch `pallet-consensus-incentives::LastRewardedHeight` or claw back any paid reward.

So the sequence is: consensus proof accepted → reward paid instantly for the full new block span → challenge period runs → a fisherman vetoes one of those heights as an invalid state → the state commitment is deleted and `LatestStateMachineHeight` rolls back, but the treasury has already permanently paid out the reward for blocks that turned out not to be canonical, and the watermark that was advanced past those heights is never reset. This mirrors the seed bug exactly: reward-per-unit is computed and disbursed at the moment new "allocation" (here, a state height claim) arrives, rather than being deferred until the end of its trial/confirmation period (the challenge period).

### Impact Explanation
This is a direct, unrecoverable loss of protocol treasury funds: a reward is minted and transferred for a state-height span whose canonicity has not yet been established, and if that span is subsequently proven non-canonical and vetoed, the payment cannot be reclaimed. Because the watermark also advances irreversibly, subsequent honest updates that re-establish the correct chain of heights are never re-rewarded for the disputed span either, permanently distorting the accounting between treasury outflow and legitimately delivered consensus progress.

### Likelihood Explanation
This does not require a malicious relayer, prover, or governance actor — any consensus update that is later found to carry an invalid intermediate state during its normal challenge window (the exact scenario the protocol's own challenge-period/fisherman design anticipates) triggers the flaw. The relayer submitting the update need only be relaying data produced upstream; the reward is paid unconditionally by `on_executed` before any veto window closes, so the vulnerable condition is reached on every ordinary consensus update, not merely a crafted attack path.

### Recommendation
Defer relayer reward calculation and treasury payout in `pallet-consensus-incentives` until the challenge period for each rewarded height has elapsed without a veto (mirroring how the message-handling and messaging-relayer code paths already gate on `verify_delay_passed`/`wait_for_challenge_period`). Alternatively, add a clawback: on `delete_state_commitment`/veto, have `pallet-fishermen` invoke a hook that reduces `LastRewardedHeight` and recovers any reward already paid for the vetoed height(s) from the relayer, symmetrical to how `Vault.sendRewardsToGame`/end-of-period accounting is recommended in the source report.

### Proof of Concept
1. Configure a `StateMachineId` with `StateMachinesCostPerBlock = C` and a non-zero `challenge_period`.
2. A relayer submits a valid `ConsensusMessage` that verifies successfully and produces intermediate `StateMachineCommitment`s advancing `latest_commitment_height` from `H0` to `H1` (see `update_client` in `modules/ismp/core/src/handlers/consensus.rs`).
3. `on_executed` fires immediately: `pallet-consensus-incentives::process_message` computes `reward = (H1 - H0) * C` and transfers it from the treasury to the relayer; `LastRewardedHeight` is set to `H1` (`modules/pallets/consensus-incentives/src/impls.rs:41-75`).
4. Before the `challenge_period` for height `H1` elapses, a fisherman submits a valid veto and `delete_state_commitment` is called on `H1`, rolling `LatestStateMachineHeight` back to `PreviousStateMachineHeight` (`modules/pallets/ismp/src/host.rs:194-222`).
5. Observe: the treasury balance reduction from step 3 is never restored, and `LastRewardedHeight` remains at `H1` — permanently paid for a span that the protocol's own fisherman process proved was not canonical, with no code path to recover the funds or reset the watermark.

### Citations

**File:** modules/ismp/core/src/handlers/consensus.rs (L41-83)
```rust
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

**File:** modules/ismp/core/src/handlers.rs (L103-114)
```rust
/// for the state machine has elasped.
pub fn verify_delay_passed<H>(host: &H, proof_height: &StateMachineHeight) -> Result<bool, Error>
where
	H: IsmpHost,
{
	let update_time = host.state_machine_update_time(*proof_height)?;
	let delay_period = host
		.challenge_period(proof_height.id)
		.ok_or(Error::ChallengePeriodNotConfigured { state_machine: proof_height.id })?;
	let current_timestamp = host.timestamp();
	Ok(delay_period.as_secs() == 0 || current_timestamp.saturating_sub(update_time) > delay_period)
}
```

**File:** docs/content/protocol/ismp/consensus.mdx (L215-215)
```text
A `StateMachineUpdated` event is emitted to notify network participants (both relayers and fishermen) of some newly available `StateCommitment`s for a given state machine. Relayers will wait for the configured `challenge_period` before attempting to transmit new requests & responses. While fishermen will check if these pending `StateCommitment`s describe valid states on the counterparty network. If the `challenge_period` elapses without any fraud proofs being presented, we can safely conclude that the provided `StateCommitment`s are indeed canonical.
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

**File:** modules/pallets/ismp/src/host.rs (L194-222)
```rust
	fn delete_state_commitment(&self, height: StateMachineHeight) -> Result<(), Error> {
		// The height's entry in the state commitment queue is deliberately left
		// behind; locating it would mean scanning the queue, which is the per-insert
		// cost the queue exists to avoid. Usually its eviction is a no-op, but when
		// the vetoed height is the latest the reset below re-opens it for honest
		// resubmission, and the resubmitted height gets a *second* queue entry. The
		// stale entry then evicts the live commitment when it reaches the head —
		// one insertion before the live entry would have, since the resubmission
		// lands directly behind its stale twin. So a veto costs that height one
		// insertion of retention and permanently burns one queue slot. Both are
		// negligible against the configured caps; making it exact would need a
		// height -> index map on the insert path.
		BoundedStateCommitments::<T>::remove(height.id, height.height);
		BoundedStateMachineUpdateTime::<T>::remove(height.id, height.height);

		// technically any state commitment can be vetoed,
		// safety check that it's the latest before resetting it.
		if let Some(latest) = LatestStateMachineHeight::<T>::get(height.id) {
			if latest == height.height {
				// Reset back to the initial height to allow for honest updates
				let prev_height =
					PreviousStateMachineHeight::<T>::get(height.id).ok_or_else(|| {
						Error::Custom("Previous state machine height should exist".to_string())
					})?;
				LatestStateMachineHeight::<T>::insert(height.id, prev_height);
			}
		}
		Ok(())
	}
```
