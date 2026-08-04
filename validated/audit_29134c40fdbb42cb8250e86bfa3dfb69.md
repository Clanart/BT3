Confirmed: `Pallet::execute` (in `modules/pallets/ismp/src/impls.rs`) processes an arbitrary batch of `Message`s submitted in a single call — the extrinsic caller supplies `Vec<Message>` (which can freely mix `Message::Consensus` entries signed by different relayer keys plus messages of other types) and passes the *entire batch* to `T::FeeHandler::on_executed(messages_with_weights, events)` in one shot.

### Title
Reward misattribution in consensus incentive payout via multi-signer message batching - (File: modules/pallets/consensus-incentives/src/impls.rs)

### Summary
`pallet-consensus-incentives`'s `FeeHandler::on_executed` derives the relayer to be rewarded **only from `messages[0]`** — the first message in the batch — then pays that single signer for *every* `StateMachineUpdated` event produced anywhere in the batch, regardless of which `Message::Consensus` entry (and therefore which relayer signature) actually produced each event.

### Finding Description
`Pallet::execute` in [1](#0-0)  accepts an unsigned/permissionless `Vec<Message>` batch, maps each to `handle_incoming_message`, collects the resulting events, and calls `T::FeeHandler::on_executed(messages_with_weights, events.clone())` once for the whole batch.

`FeeHandler::on_executed` for consensus incentives only inspects `messages.get(0)` to recover a `relayer_account` via signature verification: [2](#0-1) . It then iterates **all** `StateMachineUpdated` events collected across the whole batch — collapsed per `state_machine_id` to the highest height — and pays the reward for every one of them to that single first-message signer: [3](#0-2) .

Nothing in `execute` or `on_executed` binds a given `StateMachineUpdated` event back to the specific `Message::Consensus` entry (and its embedded `signer`) that produced it. Since `messages: Vec<Message>` is attacker-controlled input to a permissionless extrinsic, an attacker can construct a batch containing:
1. Their own cheap/self-signed `Message::Consensus` entry as `messages[0]` (attributing the whole batch to themselves), and
2. Additional valid consensus proofs for other state machines that legitimately advance those chains' commitments (these can be copied/rebroadcast from any public relayer's already-published proof, since consensus proofs are freely available/public per the protocol's design).

The result: every state machine advanced within that batch triggers a payout in `LastRewardedHeight`-gated rewards, but 100% of it is credited to the account in `messages[0]`, even for chains whose actual proof-of-delivery work belongs to a different relayer. This directly parallels the CurveDAO report's core issue — rewards distributed unfairly because the accounting doesn't correctly track which entity actually did the qualifying work (analogous to conflating `balanceOfAt`/`totalSupplyAt` snapshots), letting one actor collect rewards disproportionate to their contribution.

### Impact Explanation
This is a fund-loss/logic-attack on the treasury-funded relayer incentive pool: a single unprivileged relayer can systematically capture consensus-incentive rewards for state machine updates it did not deliver, simply by front-loading its own message as `messages[0]` in a batch alongside other chains' consensus proofs. Both the token reward (`T::Currency::transfer` from the treasury) and the `ReputationAsset` mint are misdirected: [4](#0-3) . Over time this drains the treasury to the wrong beneficiary and corrupts the reputation system used to track relayer contribution.

### Likelihood Explanation
High for any relayer submitting batched consensus messages, since `execute` accepts caller-supplied batches without pairing signer-to-event. No malicious peer, prover, or governance actor is required — an ordinary relayer submitting a mixed batch (or intentionally crafting one) triggers the misattribution. The code even has an explicit comment showing the double-reward-per-batch class of bug was previously identified and partially fixed (collapsing multiple events per state machine to one), but the fix did not address cross-message signer attribution within the same batch.

### Recommendation
Attribute each `StateMachineUpdated` event's reward to the signer of the specific `Message::Consensus` that produced it, not to `messages[0]`. This requires threading a mapping from `Message::Consensus` (and its verified signer) to the `StateMachineId`(s)/heights it actually updated, rather than aggregating all batch events under a single derived signer. Short term, restrict `on_executed`'s consensus-incentive path to only reward when the batch contains exactly one `Message::Consensus` entry (reject/ignore mixed multi-signer batches for reward purposes). Long term, redesign the event-to-message correlation so `handle_incoming_message`'s per-message result carries its own relayer attribution through to `on_executed`.

### Proof of Concept
1. Attacker holds a signing key `K_attacker` and constructs `Message::Consensus(proof_A)` for chain A signed by `K_attacker` (any valid, even minimal/no-op, consensus proof accepted by the light client).
2. Attacker copies a currently-pending, publicly available valid `Message::Consensus(proof_B)` for chain B (any relayer's proof is public per protocol design — proofs are "freely provided" per the relayer docs).
3. Attacker submits `execute(vec![Message::Consensus(proof_A), Message::Consensus(proof_B)])`.
4. `handle_incoming_message` processes both, producing `StateMachineUpdated` events for both chain A and chain B.
5. `on_executed` recovers the relayer solely from `messages[0]` (`proof_A`, signed by `K_attacker`), then loops over `highest_per_state_machine` containing both chain A and chain B and pays the full reward for **both** state machine advances to `K_attacker`'s account — even though `proof_B`'s actual submitter/relayer never signed `messages[0]` and gets nothing. [5](#0-4) [6](#0-5)

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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L53-68)
```rust
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
