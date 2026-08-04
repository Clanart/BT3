## Analysis

Confirmed via code inspection:

- `Pallet::<T>::execute` in `pallet-ismp` processes an entire `Vec<Message>` batch (as delivered by a single `handle_unsigned`/`handle` extrinsic call), collects **all** resulting events from **all** messages into one `events` vector, and calls `T::FeeHandler::on_executed(messages_with_weights, events.clone())` **once for the whole batch**. [1](#0-0) 

- `pallet-consensus-incentives`'s `on_executed` derives the sole `relayer_account` for the entire call **only from `messages[0]`**, verifying it is a `Message::Consensus` and recovering the sr25519 pubkey from its `signer`/`consensus_proof`. [2](#0-1) 

- It then builds `highest_per_state_machine` by scanning **every** `StateMachineUpdated` event in the batch-wide `events` vector — regardless of which message in the batch produced that event — and pays every one of those state machines' rewards to that single `relayer_account`. [3](#0-2) 

- The `signer` field on `Message::Consensus` for messages other than index 0 is never inspected by this pallet, and the core ISMP handler (`update_client`) does not bind proof validity to the signer at all — it only performs consensus-proof verification via the `ConsensusClient`, so any syntactically valid `ConsensusMessage` for state machine B/C is processed and its `StateMachineUpdated` event emitted independent of who is credited as `signer`. [4](#0-3) 

- The pallet's own documented design intent confirms rewards are meant to be attributed per relayer-signature, not per-batch: "The pallet cryptographically recovers the relayer's public key from the signature attached to the `ConsensusMessage` to identify who should receive the reward" — but the implementation only ever inspects one message's signature for an arbitrarily large multi-state-machine batch. [5](#0-4) 

I was not able to locate the exact extrinsic/dispatchable (`handle_unsigned`) definition that ultimately calls `execute()` within the tool-call budget, but the pattern of `Vec<Message>` batching feeding a single `FeeHandler::on_executed` call is unambiguous from `impls.rs` and is corroborated by the existing regression test suite, which already exercises `on_executed` with multiple `StateMachineUpdated` events per call. [6](#0-5) 

### Title
Reward misattribution: `on_executed` pays every state machine's consensus reward in a batch to `messages[0]`'s signer - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`FeeHandler::on_executed` for `pallet-consensus-incentives` derives the reward beneficiary from only the first message in a batch, but distributes rewards for every distinct `StateMachineId` whose `StateMachineUpdated` event appears anywhere in the batch's event stream, regardless of which message actually produced that state update.

### Finding Description
`pallet-ismp::Pallet::execute` accepts a `Vec<Message>` and invokes `handle_incoming_message` for each, aggregating all resulting events into one flat `Vec<Event>` before calling `T::FeeHandler::on_executed(messages_with_weights, events)` a single time for the whole batch. `pallet-consensus-incentives::on_executed` recovers a relayer account only from `messages[0]` (must be `Message::Consensus` with a valid sr25519 signature over `keccak_256(consensus_proof)`), then iterates the entire aggregated `events` vector, groups by `StateMachineId`, and pays the reward for each group's highest `latest_height` to that single recovered `relayer_account`. Nothing ties later `StateMachineUpdated` events (produced by messages at index ≥ 1) back to the signer of the specific `Message::Consensus` that generated them.

### Impact Explanation
An unprivileged caller can submit a batch containing one cheap/self-signed `Message::Consensus` at index 0 alongside additional valid `Message::Consensus` entries for unrelated `StateMachineId`s (whose `signer` fields belong to, or are irrelevant to, other parties, since core proof verification does not check `signer`). Every resulting `StateMachineUpdated` reward across the whole batch is then paid from `TreasuryAccount` to the attacker's single account, and `ReputationAsset` is minted to that same account for all state machines. This breaks the reward-accounting invariant that beneficiary must match the relayer who actually produced each specific state machine's proof, letting a single account siphon rewards for consensus work it did not do, at the treasury's expense.

### Likelihood Explanation
High. No privileged access, front-running, or infrastructure compromise is required beyond crafting a single extrinsic with an ordered `Vec<Message>` — this is a standard unprivileged `handle_unsigned` submission. Consensus proofs for public networks (BEEFY/GRANDPA/parachain, etc.) are independently constructible/observable public data, so an attacker can freely assemble a multi-state-machine batch and control which message occupies index 0.

### Recommendation
Bind each `StateMachineUpdated` event to the specific message (and therefore signer) that produced it, e.g. by having `handle_incoming_message`/`MessageResult::ConsensusMessage` retain a per-message index or by processing rewards per-message using each message's own events rather than aggregating and crediting only `messages[0]`. Reject or explicitly disallow batches mixing multiple independently-signed `Message::Consensus` entries under a single reward attribution, or require that reward computation iterate `messages` and their individually-produced events in lockstep.

### Proof of Concept
1. Attacker (key K) builds `messages = [msg0, msg1]`:
   - `msg0`: `Message::Consensus` for `StateMachineId` A, self-signed by K, with a small/zero-cost real proof.
   - `msg1`: `Message::Consensus` for `StateMachineId` B, a genuinely valid consensus proof (independently constructible/public), with `signer` set to anything (e.g. empty or another party's signature — never checked for B).
2. Submit both in one `handle_unsigned` call → `pallet_ismp::Pallet::execute` processes both, emitting `StateMachineUpdated` for A and B, and calls `on_executed(messages, events)` once.
3. In `on_executed`, `maybe_relayer_account` = K (from `msg0` only).
4. `highest_per_state_machine` contains entries for both A and B.
5. The reward loop calls `process_message(..., state_machine_id=A, relayer_account=K)` and `process_message(..., state_machine_id=B, relayer_account=K)` — both rewards paid to K, even though K did not sign/prove B's update itself. [7](#0-6)

### Citations

**File:** modules/pallets/ismp/src/impls.rs (L40-86)
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
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L112-122)
```rust
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
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L124-156)
```rust
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

**File:** modules/ismp/core/src/handlers.rs (L93-99)
```rust
	match message {
		Message::Consensus(consensus_message) => consensus::update_client(host, consensus_message),
		Message::FraudProof(fraud_proof) => consensus::freeze_client(host, fraud_proof),
		Message::Request(req) => request::handle(host, req),
		Message::Response(resp) => response::handle(host, resp),
		Message::Timeout(timeout) => timeout::handle(host, timeout),
	}
```

**File:** modules/pallets/consensus-incentives/README.md (L21-22)
```markdown
* **Trigger**: The incentive process is triggered when the `on_executed` hook from the `FeeHandler` trait is called. The pallet filters for `ConsensusMessage` types.
* **Relayer Identification**: The pallet cryptographically recovers the relayer's public key from the signature attached to the `ConsensusMessage` to identify who should receive the reward.
```

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L169-173)
```rust
		<pallet_consensus_incentives::Pallet<Test> as FeeHandler>::on_executed(
			vec![message.clone()],
			updated(1025),
		)
		.unwrap();
```
