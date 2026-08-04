### Title
Reward misattribution in `pallet-consensus-incentives`: only the first message's signer is paid for all state-machine updates in a batch - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`FeeHandler::on_executed` derives the relayer to be rewarded solely from `messages[0]`, then pays that single relayer for **every** `StateMachineUpdated` event produced anywhere in the batch, regardless of which message in the batch actually produced which event.

### Finding Description
`on_executed` extracts the reward recipient like this: [1](#0-0) 

It only looks at `messages.get(0)`, decoding the embedded `signer` field from that single message's `Message::Consensus` variant to determine `relayer_account`. It then iterates over **all** `IsmpEvent::StateMachineUpdated` events in the whole batch (which can be produced by other, later messages in the same batch, for other state machines), and pays the single `relayer_account` derived from message 0 for every one of them: [2](#0-1) 

`process_message`/`calculate_reward` re-derive the reward purely from persisted host heights (`latest_commitment_height`, `previous_commitment_height`, `LastRewardedHeight` watermark) — they never check that the `relayer_account` being paid is actually tied to the specific message that advanced that particular state machine: [3](#0-2) 

ISMP consensus messages carry a self-contained signature (`consensus_msg.signer`) and are submitted via `ensure_none(origin)` (unsigned dispatch), so the "signer" attributed to a message is whatever public key produced the embedded proof signature — it is not cryptographically bound to whoever assembled/submitted the batch extrinsic, nor to which specific state machine's update it corresponds to once multiple consensus messages ride in the same batch. Because reward attribution is keyed off `messages[0]` alone while payout iterates over the union of all `StateMachineUpdated` events in the batch, any batch containing more than one consensus message pays the entirety of the batch's rewards to whoever's signature happens to sit in slot 0, even if other messages in that same batch performed the state-machine advances that generated the rest of the rewarded events.

### Impact Explanation
This is a direct fund-misattribution/theft primitive from the treasury: `T::Currency::transfer` moves protocol treasury funds and `T::ReputationAsset::mint_into` mints reputation to a single wrong beneficiary for work that (per the intended design described in the pallet's own README — "reward relayers who successfully deliver consensus updates") was actually delivered via other messages/relayers bundled in the same extrinsic. An attacker can systematically redirect consensus-incentive rewards for state-machine updates they did not perform to themselves by controlling message order within a batch, at the cost of only including one cheap/trivial consensus message of their own as element 0.

### Likelihood Explanation
The extrinsic is unsigned (`ensure_none`), so any unprivileged caller can submit a `Vec<MessageWithWeight>` batch and control its ordering; ISMP consensus proofs are broadcast/gossiped data, so a batch can be assembled where message 0 is the attacker's own (possibly minimal or previously-seen) consensus proof, while subsequent messages that trigger the valuable `StateMachineUpdated` events belong to state machines whose real update work was proven by a different relayer's signature embedded further in the batch. No compromised keys, governance, or malicious-prover assumptions are required — only the ability to submit an unsigned batched extrinsic with more than one `Message::Consensus` entry, which is a normal, permissionless operation of this pallet.

### Recommendation
Attribute each `StateMachineUpdated` event's reward to the signer of the specific `Message::Consensus` entry that produced it (pair events to their originating message index/state-machine id rather than collapsing to `messages[0]`), or reject/split batches containing consensus messages for more than one state machine unless each message's signer is individually recovered and paid only for the state machine(s) it advances.

### Proof of Concept
1. Attacker holds/derives a trivial but validly-signed `Message::Consensus` for `StateMachineA` (e.g., resubmitting a previously accepted height, or the cheapest state machine configured with `StateMachinesCostPerBlock`).
2. Attacker observes/collects a legitimate `Message::Consensus` for `StateMachineB` that was produced and signed by Relayer B (broadcast for delivery, or copied from a still-pending extrinsic before finalization).
3. Attacker submits a single unsigned extrinsic to `pallet_ismp::handle_messages` with `messages = [attacker_msg_for_A, relayerB_msg_for_B]`.
4. `pallet-ismp` executes both, producing `StateMachineUpdated` events for both `StateMachineA` and `StateMachineB`.
5. `on_executed` reads `messages[0]` → recovers `relayer_account = attacker`. It then iterates `highest_per_state_machine` built from **all** events (A and B) and calls `process_message` for both, transferring `calculate_reward` for `StateMachineB`'s full block-span reward (and minting matching reputation) to the attacker instead of Relayer B, who actually supplied and paid gas/effort for that proof. [4](#0-3)

### Citations

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
