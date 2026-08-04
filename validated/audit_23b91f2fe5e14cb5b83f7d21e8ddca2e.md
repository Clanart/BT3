This confirms `execute(messages: Vec<Message>)` in `Pallet::execute` accepts an arbitrary caller-supplied batch of `Message`s in a single call, runs each through `handle_incoming_message`, collects all resulting `events`, and passes the *entire batch's* `events` together with the *entire batch's* `messages` to `FeeHandler::on_executed` in one shot. [1](#0-0) 

In `pallet-consensus-incentives`'s `on_executed`, the relayer who is credited is derived **only from `messages.get(0)`** — the first message in the whole submitted batch — while every `StateMachineUpdated` event produced by the *entire* batch (which can include state-machine advances triggered by other consensus messages in the same call) is rewarded to that single first-message signer. [2](#0-1) 

### Title
Consensus-incentive rewards for an entire message batch are paid only to the signer of the first message, letting any relayer steal rewards for state advances they did not deliver - (File: modules/pallets/consensus-incentives/src/impls.rs)

### Summary
`pallet-ismp::Pallet::execute` accepts a caller-controlled `Vec<Message>` in a single extrinsic and forwards the combined `events` from processing *all* messages to `FeeHandler::on_executed` once per call. [1](#0-0)  `pallet-consensus-incentives::on_executed` identifies "the relayer" solely from `messages[0]`'s embedded consensus-proof signature, then iterates the *collapsed* `StateMachineUpdated` events from the whole batch and pays the reward for every state machine's height advance to that single recovered account. [3](#0-2) 

### Finding Description
This mirrors the external report's core flaw: an aggregate/derived value (here, "who delivered this batch" — analogous to GMX's "current leveraged position size") is used to attribute a payout, instead of tying the payout strictly to the specific delta/actor that produced it (GMX: the actual increase amount; here: the actual message that produced each `StateMachineUpdated` event).

Concretely: `messages.get(0)` is used to recover a single relayer's `sr25519` pubkey from the first `Message::Consensus` in the batch. [4](#0-3)  The events vector, however, is built from *all* messages in the batch (`handle_incoming_message` runs over every message and their resulting events are flattened together before being handed to `on_executed`). [5](#0-4)  The pallet then collapses all `StateMachineUpdated` events per `state_machine_id` to their highest `latest_height` and pays the full watermark-delta reward for *every* state machine touched in the batch to the single relayer recovered from message #0. [6](#0-5) 

There is no per-event linkage back to which specific `Message::Consensus` (and therefore which specific submitter/signature) actually produced each `StateMachineUpdated` event — the code implicitly (and incorrectly) assumes the whole batch belongs to one relayer.

### Impact Explanation
An unprivileged relayer can submit a batch (`execute`) that includes their own cheap/trivial `Message::Consensus` as the *first* element, concatenated with one or more consensus messages that would otherwise be submitted separately by other relayers (or crafted by the attacker for other chains), such that the batch's flattened events include multiple `StateMachineUpdated` advances for different state machines. Because attribution is keyed off `messages[0]` only, the attacker's account collects `RelayerRewarded` treasury payouts and `ReputationAsset` mints for state-machine advances they did not economically/technically deliver on their own — a wrong-beneficiary fund diversion from the treasury. This directly matches the bounty's "unauthorized transaction/execution... wrong beneficiary or amount" impact category, since treasury funds move to an account that did not "own" all the credited chain advances.

### Likelihood Explanation
The `execute` entrypoint takes a plain `Vec<Message>` supplied by the calling extrinsic; nothing in `impls.rs::execute` or `on_executed` restricts a batch to messages signed by the same account, nor validates that each `StateMachineUpdated` in `events` corresponds to `messages[0]`. Any account able to call the ISMP handling extrinsic (a normal, permissionless relayer action) can construct such a batch. No malicious peer, prover, governance actor, or leaked key is required — only ordinary message construction/ordering by an unprivileged caller.

### Recommendation
Attribute each `StateMachineUpdated` reward to the specific `Message::Consensus` (and its verified signer) that produced it, rather than to `messages[0]`. Concretely, iterate `messages` alongside their corresponding `MessageResult`/events, recover a signer per consensus message, and only reward the height delta actually attributable to that message's proof — mirroring how the recommendation in the seed report was to size fees/rewards on the *actual increment*, not a stale/aggregate reference.

### Proof of Concept
1. Attacker holds a valid signer key and crafts `Message::Consensus(A)` — a minimal/cheap consensus proof advancing `StateMachineId::X` by 1 block, signed by the attacker.
2. Attacker also includes `Message::Consensus(B)`, a legitimate, larger consensus update for a different `StateMachineId::Y` (e.g., copied/re-submitted from mempool/relaying infrastructure they observed but did not need to have signed).
3. Attacker submits `execute(vec![A, B])` in one call so `messages[0] == A`.
4. `Pallet::execute` runs both messages, producing `StateMachineUpdated` events for both `X` and `Y`, and calls `on_executed(messages_with_weights, events)` once. [5](#0-4) 
5. `on_executed` recovers only the signer of `A` (attacker) from `messages[0]`, and pays the reward for **both** `X`'s and `Y`'s height advance to the attacker. [2](#0-1) 
6. Attacker receives `RelayerRewarded` treasury funds and `ReputationAsset` for a chain update they only had to observe, not deliver themselves.

### Citations

**File:** modules/pallets/ismp/src/impls.rs (L40-79)
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
