## Analysis

The seed report's core broken invariant: a cheap, self-controlled action (voting on your own permissionless proposal) credits a "score" that is supposed to represent real weight/participation, but the credit is granted without verifying that the specific actor actually did the qualifying work.

The direct analog in Hyperbridge is in `pallet-consensus-incentives`, where a relayer reward/reputation credit is attributed to whichever account signed the *first* message in a batch, while the actual reward amount is computed from the aggregate effect of the *entire* batch of messages — not just the one message tied to that signer.

### Title
Consensus-incentive rewards for an entire batch are attributed to the signer of only the first message, allowing reward/reputation misattribution - (File: modules/pallets/consensus-incentives/src/impls.rs)

### Summary
`pallet-ismp::execute()` runs an arbitrary, caller-supplied `Vec<Message>` submitted via the unsigned `handle_unsigned` extrinsic, flattens the `events` produced by *every* message in the batch into one vector, and invokes `FeeHandler::on_executed(messages_with_weights, events)` exactly once for the whole batch. [1](#0-0) [2](#0-1) 

`pallet-consensus-incentives::on_executed` determines the account to reward by recovering the signer of `messages.get(0)` only — if it isn't a `Message::Consensus`, `maybe_relayer_account` is `None`. It then builds `highest_per_state_machine` from **all** `StateMachineUpdated` events in the batch (regardless of which message produced them) and pays the full block-span reward plus mints reputation for every one of those state machines to that single `relayer_account`. [3](#0-2) 

### Finding Description
The reward/reputation credit has no per-message causal binding: `process_message` is called once per distinct `state_machine_id` found anywhere in the batch's event list, always passing the same `relayer_account` taken from `messages[0]`'s signature. [4](#0-3) 

Because `handle_unsigned` is `ensure_none` (unsigned, permissionless, callable by anyone with valid proofs) and accepts an arbitrary `Vec<Message>`, an attacker can construct a batch where:
- `messages[0]` is a `ConsensusMessage` signed by the attacker's own key, e.g. targeting a state machine whose advance is trivial/cheap to prove or whose `cost_per_block` is negligible.
- `messages[1..]` include one or more additional, independently-valid `ConsensusMessage`s that advance a different, high-`cost_per_block` state machine by a large block span (these proofs are deterministic consensus data — not secrets — and can be reconstructed/observed by anyone with access to the source chain's finality data, just like any relayer would build them).

`on_executed` will credit the attacker's `relayer_account` (from `messages[0]`) with the reward and `ReputationAsset` mint for **every** state machine advance in the batch, including the ones whose proofs were produced/signed by someone else. The treasury transfer and reputation mint in `process_message` execute unconditionally for the attacker's address: [5](#0-4) 

No existing guard ties the paid-out `state_machine_id`/`latest_height` back to the specific message (or its signer) that generated the corresponding `StateMachineUpdated` event; the collapsing into `highest_per_state_machine` is explicitly batch-wide by design (per the comment at lines 125-132), and the relayer identity is resolved once from index 0.

### Impact Explanation
This is a wrong-beneficiary / unauthorized-attribution fund flow: BRIDGE-denominated treasury funds and `ReputationAsset` mints intended to reward whoever actually produced/delivered a given chain's consensus proof are instead paid to an unrelated address that merely positioned a cheap consensus message first in the same unsigned batch. This falls squarely in "stealing or loss of funds" / "wrong beneficiary or amount" since the treasury pays the full configured reward to the wrong account for real, valid state-machine advances.

### Likelihood Explanation
The path requires no privileged role, no relayer/prover compromise, and no malicious peer assumption — `handle_unsigned` is explicitly designed to accept any unsigned, valid batch of ISMP datagrams from anyone. Batching multiple `ConsensusMessage`s together across different state machines is a normal, encouraged usage pattern (the FeeHandler collapsing logic itself anticipates multi-state-machine batches), which makes the misattribution reachable through ordinary transaction construction rather than a contrived edge case.

### Recommendation
Bind each `StateMachineUpdated` event to the specific `ConsensusMessage` (and its recovered signer) that produced it, e.g., by having `execute()` preserve a per-message index/signer alongside its emitted events, and have `on_executed` pay each state machine's reward to the signer of the message that actually advanced it, rather than defaulting the whole batch to `messages[0]`'s signer.

### Proof of Concept
1. Relayer B independently builds and would normally submit `ConsensusMessage_Y` (signed with key B) proving a large, expensive block-span advance for `state_machine_id = Y`, which has a high `StateMachinesCostPerBlock` value.
2. Attacker A observes/derives an equally valid `ConsensusMessage_Y` payload (consensus proofs are public/deterministic — no secret is needed to construct them, only access to source-chain finality data) and also crafts a trivial `ConsensusMessage_X` for a cheap/negligible-cost state machine `X`, signed with key A.
3. Attacker submits `handle_unsigned(messages = [ConsensusMessage_X (signer=A), ConsensusMessage_Y (signer=B)])`.
4. In `execute()`, both messages process successfully, and their events (including `StateMachineUpdated` for both X and Y) are flattened into one `events` vec passed to `on_executed`. [6](#0-5) 
5. `on_executed` recovers `relayer_account = A` from `messages[0]` only, then iterates the combined `events`, finds `highest_per_state_machine` = {X: ..., Y: large_span}, and calls `process_message` for both X and Y with `relayer_account = A`. [7](#0-6) 
6. The treasury transfers the full reward for Y's large block-span advance to attacker A, and mints A the corresponding `ReputationAsset` — even though A did not produce or sign `ConsensusMessage_Y`.

### Citations

**File:** modules/pallets/ismp/src/impls.rs (L37-87)
```rust
impl<T: Config> Pallet<T> {
	/// Execute the provided ISMP datagrams, this will short circuit if any messages are invalid.
	/// This also charges fee on valid message delivery
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

**File:** modules/pallets/ismp/src/lib.rs (L370-382)
```rust
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
