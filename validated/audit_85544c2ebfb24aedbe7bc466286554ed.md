## Confirmed: `FeeHandler::on_executed` attributes ALL consensus-update rewards in a batch to the signer of `messages[0]` only

### Title
Reward misattribution in `FeeHandler::on_executed` lets an attacker collect every relayer's consensus-update reward in a batch by placing a cheap self-signed `Consensus` message first - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`Pallet::execute` in `modules/pallets/ismp/src/impls.rs` processes an entire `Vec<Message>` batch, aggregates **all** resulting `IsmpEvent`s from every message in the batch, and calls `T::FeeHandler::on_executed(messages_with_weights, events)` exactly once per batch. `FeeHandler::on_executed` for the consensus-incentives pallet derives the reward recipient from `messages[0]` alone and then pays out a reward for every distinct `state_machine_id` found anywhere in the aggregated `events`, regardless of which message actually produced that event.

### Finding Description
`Pallet::execute` [1](#0-0)  builds one combined `events` vector from processing every `Message` in the batch and hands it, together with the whole `messages_with_weights` vector, to `T::FeeHandler::on_executed` in a single call.

In `FeeHandler::on_executed` [2](#0-1) , `maybe_relayer_account` is computed **only** from `messages[0]`: it checks whether the first message is `Message::Consensus`, and if so recovers the sr25519 public key from the signature over that single message's `consensus_proof`. No other message in the batch influences who is treated as "the relayer."

The code then aggregates the **highest `latest_height` per `state_machine_id`** across the *entire batch's* `events` [3](#0-2) , and pays the reward for **every** state machine in that map to the single `relayer_account` derived from `messages[0]` [4](#0-3) .

There is no correlation enforced between which message produced a given `StateMachineUpdated` event and who is paid for it. Since anyone can submit the batch extrinsic containing message datagrams (consensus proofs for public consensus updates are not privileged/secret data — they can be copied/re-embedded by anyone), an attacker can:
1. Construct or intercept genuine `Message::Consensus` datagrams for high-cost state machines (these are valid, publicly verifiable consensus proofs, so they will pass `handle_incoming_message` and legitimately advance those chains' commitments, producing genuine `StateMachineUpdated` events).
2. Prepend their own trivially-cheap self-signed `Message::Consensus` for an unrelated, low-cost state machine as `messages[0]`.
3. Submit the batch. `execute()` processes all messages successfully, aggregates all `StateMachineUpdated` events, and calls `on_executed` once.
4. `on_executed` derives the payee solely from the attacker's `messages[0]` signature and pays the attacker the treasury reward for **all** state machines updated in the batch — including the expensive ones whose proofs were not economically supplied/paid-for-relaying by the attacker.

This breaks the intended invariant that `RelayerRewarded` payouts (and the associated `T::ReputationAsset` mint) go to the party that actually delivered the corresponding consensus proof, and lets an unprivileged attacker drain `T::TreasuryAccount` by simply prepending a cheap message to a batch containing other valid, high-value consensus messages.

### Impact Explanation
This is a direct fund-drain and wrong-beneficiary bug in the production consensus-incentives pallet: treasury funds intended as relayer rewards are systematically redirected to an attacker who did not deliver the corresponding consensus proofs, and legitimate relayers are permanently deprived of rewards for updates they actually delivered (since `LastRewardedHeight` watermark advances regardless of payee [5](#0-4) , the reward for that block-span cannot be reclaimed later).

### Likelihood Explanation
High. No privileged access is required — an attacker only needs to be the one who assembles/submits the batch extrinsic (or otherwise gets to control message ordering within a batch/block), copy publicly-broadcast consensus proofs from genuine relayers, and prepend one self-signed cheap `Consensus` message. The exploit is deterministic and repeatable every time a batch mixing an attacker consensus message with genuine ones is processed.

### Recommendation
`on_executed` should track a per-message (or per-`state_machine_id`) relayer attribution rather than a single global `maybe_relayer_account` derived from `messages[0]`. Specifically, each `Message::Consensus` in the batch should be paired with the `StateMachineUpdated` event(s) it actually produced (e.g., by iterating `messages` and their corresponding per-message results rather than a flattened/aggregated `events` list), and rewards should be paid to the signer of the specific consensus message that produced each state machine's update.

### Proof of Concept
1. Attacker holds a valid sr25519 keypair and crafts `Message::Consensus { consensus_proof: cheap_proof, signer: sig_over_keccak256(cheap_proof) }` for a low/zero-cost `state_machine_id_cheap` (e.g. `StateMachinesCostPerBlock` unset or minimal).
2. Attacker copies genuine, publicly available `Message::Consensus` datagrams for `state_machine_id_expensive_1`, `..._2`, etc. (submitted originally by honest relayers, but consensus proofs themselves are not access-controlled — anyone can wrap them in a new extrinsic).
3. Attacker submits one extrinsic with `messages = [attacker_cheap_msg, honest_expensive_msg_1, honest_expensive_msg_2, ...]`.
4. `Pallet::execute` processes all messages successfully; `events` contains `StateMachineUpdated` for `state_machine_id_cheap`, `..._1`, `..._2`.
5. `FeeHandler::on_executed` computes `maybe_relayer_account` from `messages[0]` = attacker's account, then in the `highest_per_state_machine` loop pays the attacker the treasury reward for `state_machine_id_cheap`, `..._1`, and `..._2` via `Self::process_message`, transferring from `T::TreasuryAccount` to the attacker for updates the attacker did not economically deliver.
6. Assert: attacker's account balance increases by the sum of rewards for all three state machines; the honest relayers who supplied `..._1`/`..._2` proofs receive nothing, and `RelayerRewarded` events name the attacker as `relayer` for those state machines. [6](#0-5)

### Citations

**File:** modules/pallets/ismp/src/impls.rs (L40-78)
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
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L70-72)
```rust
			LastRewardedHeight::<T>::mutate(state_machine_id, |watermark| {
				*watermark = Some(watermark.unwrap_or_default().max(state_machine_height.height));
			});
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L108-156)
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
```
