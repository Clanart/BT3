## Title
Reward misattribution in `pallet-consensus-incentives::on_executed` lets an attacker steal another relayer's consensus-delivery reward by piggybacking on a batched `handle_unsigned` call - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`handle_unsigned` on `pallet-ismp` accepts an arbitrary `Vec<Message>` in a single unsigned extrinsic and executes them all atomically, producing one combined event list [1](#0-0) . `pallet-consensus-incentives::on_executed` then attributes the *entire batch's* rewards — for every distinct `StateMachineId` advanced by any message in that batch — to the relayer recovered from **only the first message** in the batch [2](#0-1) . This is the same class of bug as the Taiko report: an accounting mechanism (the reward-attribution "signer") is computed from a coarser/mismatched granularity (per-batch, indexed by position 0) than the value it is applied to (per-state-machine, per-message advance), letting the resulting economic payout diverge from the actual work performed.

### Finding Description
`handle_unsigned` is a **permissionless, unsigned** extrinsic — anyone can submit `Vec<Message>` containing any combination of valid `ConsensusMessage`s and other valid ISMP messages, as long as they collectively decode and verify [3](#0-2) . `pallet-ismp::execute` runs every message in the vector, collects **all** resulting events (including every `StateMachineUpdated` for every state machine touched), and calls `FeeHandler::on_executed(messages_with_weights, events)` exactly once for the whole batch [4](#0-3) .

Inside `pallet-consensus-incentives::on_executed`:
```rust
let maybe_relayer_account = messages.get(0).and_then(|first_message| {
    if let Message::Consensus(consensus_msg) = &first_message.message {
        ...
        .map(|pub_key| pub_key.into())
    } else { None }
});
...
for (state_machine_id, latest_height) in highest_per_state_machine {
    let _ = Self::process_message(state_machine_height, state_machine_id, relayer_account.clone().into());
}
``` [5](#0-4) 

The `relayer_account` is derived **once**, from `messages.get(0)` only. It is then reused as the payee for the reward on **every** `state_machine_id` that shows up in `highest_per_state_machine`, regardless of which message in the batch actually produced that chain's `StateMachineUpdated` event. The comment at lines 125-131 explicitly documents that the code deliberately collapses multiple `StateMachineUpdated` events across the batch into per-state-machine highest heights — but it never re-derives the signer per state machine, it only fixes the *double-payment-within-one-chain* bug while leaving the *cross-chain misattribution* bug in place.

`process_message`/`calculate_reward` then unconditionally pays `Reward = (latest_height - LastRewardedHeight_watermark) * CostPerBlock` from the treasury to whatever `relayer_account` was passed in [6](#0-5) , and advances the `LastRewardedHeight` watermark for that chain [7](#0-6) .

**Attack construction**: An attacker (call them Mallory) monitors the mempool/gossip for a pending, valid, unsigned `handle_unsigned` submission from an honest relayer that will advance a high-value chain (`CostPerBlock` set high by governance) by many blocks. Because `handle_unsigned` is unsigned and permissionless, Mallory can:
1. Take her own trivially-cheap, valid `ConsensusMessage` for some other configured state machine (or reuse any consensus message she can produce/replay for a client she controls/observes) as `messages[0]`.
2. Append the honest relayer's valid consensus proof(s) for the high-value chain as subsequent entries in the same `messages` vector.
3. Submit the combined batch via her own `handle_unsigned` call before the honest relayer's stand-alone submission lands (front-running is not required to break the invariant — she only needs the batch to execute; even independent submission of an equivalent batch that happens to include both proofs works, since anyone can restate someone else's already-broadcast valid proof in their own batch).

Because verification is state-based (a valid consensus proof is valid regardless of who submits it — this is standard for permissionless relayer protocols and is explicitly the design elsewhere in this codebase, e.g. "first-to-submit wins" documented in the relayer docs), Mallory's batch executes successfully, advances **both** state machines, and `on_executed` pays the **entire combined reward for every state machine in the batch** to Mallory's `relayer_account`, because it was recovered from `messages[0]` (her cheap message), not from whichever message actually advanced the high-value chain.

### Impact Explanation
This directly matches the required impact class "stealing or loss of funds" via "logic attacks": treasury funds intended as `$BRIDGE` compensation for the specific relayer that verifiably advanced a given state machine's consensus are instead paid to an unrelated party who merely co-batched a cheap, unrelated consensus message ahead of it in the same extrinsic. The honest relayer's proof still lands (state machine still advances, security is not broken), but the relayer never gets economically compensated and never recovers their gas/infra cost — this is an unauthorized redirection of protocol payouts, not merely a griefing/DoS issue. Given `CostPerBlock` is configured per state machine and can cover large multi-block gaps, and `LastRewardedHeight` moves forward once paid, the attacker's theft is also final and non-reversible (the `LastRewardedHeight` watermark advances regardless of *who* was paid, so the real relayer cannot re-claim it later).

### Likelihood Explanation
- The entry point (`handle_unsigned`) is public/unsigned and requires no privileged role, staking, or governance action — any unprivileged attacker can call it.
- No malicious relayer/prover/operator collusion is required beyond the attacker themselves; the "honest relayer" whose reward is stolen is a passive victim whose broadcast/already-known-valid proof is simply re-included by the attacker.
- The bug is not a hypothetical edge case — it is a straightforward consequence of the documented design choice to key the whole batch's payee off `messages.get(0)`, visible directly in the source and only partially mitigated (the mitigation fixed double-counting per chain, not misattribution across chains within one batch).
- The practical constraint is that the attacker needs at least one cheap, valid `ConsensusMessage` of their own to seed `messages[0]`; this is readily achievable for any state machine the attacker can independently generate consensus proofs for (which is the normal, permissionless relayer capability documented throughout this repo).

### Recommendation
In `pallet-consensus-incentives::on_executed`, derive and verify a relayer signer **per message that actually produces each `StateMachineUpdated` event**, not once for the whole batch. Concretely:
- Iterate `messages` and, for each `Message::Consensus`, record `(consensus_state_id or state_machine_id, recovered_signer)`.
- When collapsing events into `highest_per_state_machine`, look up the signer whose message produced that specific `StateMachineUpdated` (matching by `state_machine_id`/`consensus_state_id`), instead of reusing a single batch-wide `relayer_account`.
- Reject or skip attribution for any `StateMachineUpdated` event that cannot be tied back to a specific in-batch consensus message signer, rather than defaulting to `messages[0]`.

### Proof of Concept
Conceptual reproduction (pallet unit-test style, mirroring the existing `reward_covers_only_unpaid_heights_after_rollback` test harness):
1. Configure `StateMachinesCostPerBlock` for two state machines, A (cheap/attacker-controlled) and B (expensive, honest relayer's target), via `update_cost_per_block`.
2. Construct `consensus_message_A` signed by Mallory's key (cheap, advances A by 1 block) and `consensus_message_B`, a **valid, already-publicly-known** consensus proof that would legitimately advance B by N blocks (originally intended to be submitted by Alice, the honest relayer).
3. Submit `pallet_ismp::Pallet::handle_unsigned(RuntimeOrigin::none(), vec![consensus_message_A, consensus_message_B])` from Mallory.
4. Observe: `execute` produces `StateMachineUpdated` events for both A and B; `on_executed` recovers only Mallory's signer from `messages[0]` (`consensus_message_A`); `process_message` is invoked for both A and B with `relayer_account = Mallory`; the treasury transfers `reward_A + reward_B` entirely to Mallory's account, while Alice — who did the work of producing/broadcasting `consensus_message_B` — receives nothing.
5. Assert `Balances::balance(&mallory) == before + reward_A + reward_B` and `Balances::balance(&alice) == before` (unchanged), confirming the reward for advancing B was fully diverted.

### Citations

**File:** modules/pallets/ismp/src/lib.rs (L358-382)
```rust
	#[pallet::call]
	impl<T: Config> Pallet<T> {
		/// Execute the provided batch of ISMP messages, this will short-circuit and revert if any
		/// of the provided messages are invalid. This is an unsigned extrinsic that permits anyone
		/// execute ISMP messages for free, provided they have valid proofs and the messages have
		/// not been previously processed.
		///
		/// The dispatch origin for this call must be an unsigned one.
		///
		/// - `messages`: the messages to handle or process.
		///
		/// Emits different message events based on the Message received if successful.
		#[pallet::weight(weight())]
		#[pallet::call_index(0)]
		#[frame_support::transactional]
		pub fn handle_unsigned(
			origin: OriginFor<T>,
			messages: Vec<Message>,
		) -> DispatchResultWithPostInfo {
			ensure_none(origin)?;

			Self::execute(messages.clone())?;

			Ok(().into())
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

**File:** modules/pallets/ismp/src/impls.rs (L40-87)
```rust
	pub fn execute(messages: Vec<Message>) -> Result<Vec<events::Event>, Error<T>> {
		let host = Pallet::<T>::default();

		let message_results = messages
			.iter()
			.map(|msg| handle_incoming_message(&host, msg.clone()))
			.collect::<Result<Vec<_>, _>>()
			.map_err(|err| {
				log::debug!(target: "ismp", "Handling Error {:#?}", err);
				Pallet::<T>::deposit_event(Event::<T>::Errors { errors: vec![err.into()] });
				Error::<T>::InvalidMessage
			})?;

		let messages_with_weights = message_results
			.iter()
			.zip(messages)
			.map(|(result, message)| MessageWithWeight { message, weight: result.weight() })
			.collect::<Vec<_>>();

		let events = message_results
			.into_iter()
			// check that requests will be successfully dispatched
			// so we can not be spammed with failing txs
			.map(|result| match result {
				MessageResult::Request { events, .. } |
				MessageResult::Response { events, .. } |
				MessageResult::Timeout { events, .. } => events,
				MessageResult::ConsensusMessage(events) => events.into_iter().map(Ok).collect(),
				MessageResult::FrozenClient(_) => vec![],
			})
			.flatten()
			.collect::<Result<Vec<_>, _>>()
			.map_err(|err| {
				log::debug!(target: "ismp", "Handling Error {:#?}", err);
				Pallet::<T>::deposit_event(Event::<T>::Errors { errors: vec![err.into()] });
				Error::<T>::InvalidMessage
			})?;

		T::FeeHandler::on_executed(messages_with_weights, events.clone())
			.map_err(|_| Error::<T>::ErrorChargingFee)?;

		for event in events.clone() {
			// deposit any relevant events
			Pallet::<T>::deposit_event(event.into());
		}

		Ok(events)
	}
```
