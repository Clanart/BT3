### Title
Consensus-incentives reward misattribution when a submitted message batch advances multiple state machines - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`pallet-consensus-incentives::on_executed` pays out the **entire** consensus-relaying reward for *every* state machine advanced in a batch to the signer of only the **first** message in that batch (`messages.get(0)`), while the reward amount itself is computed by scanning **all** `StateMachineUpdated` events produced by the whole batch. Whoever controls the ordering of a submitted `Vec<Message>` — which is simply whoever calls `pallet_ismp`'s message-handling extrinsic — can place their own (possibly worthless) `Message::Consensus` first and append genuinely valid, independently-obtainable consensus proofs for other/higher-value state machines, and collect the reward that should have gone to whoever actually produced those other updates.

### Finding Description
`calculate_reward` mirrors the M-01 pattern from the external report: it computes a value (blocks synced × cost-per-block) from state that has already advanced (`latest_commitment_height`) relative to a baseline (`LastRewardedHeight`) that has *not yet* been updated for the current call. [1](#0-0) 

The relayer identity used for attribution, however, is taken from only the first message in the batch: [2](#0-1) 

...while the reward-worthy events are collected across **all** events in the batch, keyed only by `state_machine_id`, with no check that the event actually originated from `messages[0]`: [3](#0-2) 

The inline comment shows the pallet was already patched once to stop a *within-message* double-payment bug (collapsing repeated `StateMachineUpdated` events for the same chain to a single highest height), but the fix did not address the case where **different chains' updates in the same batch, produced by different messages/signers, are all paid to the signer of message 0**. Nothing ties a given `StateMachineUpdated` event back to the specific `Message::Consensus` that produced it, nor to that message's own `signer` field.

Because `Message::Consensus.signer` is a self-supplied field — `sig.verify_and_get_sr25519_pubkey(&data, None)` only proves the attacker holds a keypair that signed `keccak_256(consensus_proof)`, not that the attacker actually produced or discovered that proof — and because consensus proofs for public chains (BEEFY/GRANDPA finality proofs, etc.) are public data obtainable by anyone running a light client, an unprivileged party can:

1. Independently fetch a currently-valid consensus proof for a high-value state machine (e.g., one with a large configured `StateMachinesCostPerBlock`), the same way any relayer does — no compromised or malicious relayer needed.
2. Craft their own trivial/self-signed `Message::Consensus` (e.g., for a zero-cost or attacker-controlled state machine) and place it as `messages[0]`.
3. Submit both messages in one call to `pallet-ismp`'s message-handling entrypoint, which processes them together and invokes `on_executed(messages, events)` once for the whole batch.
4. Both messages verify successfully against the ISMP host's consensus client (proof validity is independent of the incentive-only `signer` field), producing `StateMachineUpdated` events for both state machines.
5. `on_executed` reads `relayer_account` only from `messages[0]` (the attacker) and then loops over **every** `StateMachineUpdated` event in the batch — including the one for the high-value chain the attacker did not meaningfully prove — paying the attacker the full treasury reward for both.

### Impact Explanation
This is a direct, repeatable transfer of protocol treasury funds to an unentitled party: `T::Currency::transfer` moves `TreasuryAccount → attacker` for a reward that should be split across (or paid entirely to) whichever party actually did the work for each state machine. It also mints `ReputationAsset` reputation tokens to the attacker for work they did not perform. This matches the required impact class of "stealing or loss of funds" / "logic attacks" / "wrong beneficiary" via a public, unprivileged entrypoint — no malicious relayer, prover, or governance compromise is required, since consensus proofs for public source chains are public data by construction.

### Likelihood Explanation
Likelihood is high wherever `pallet-ismp`'s message-handling extrinsic accepts a `Vec<Message>` batch from an arbitrary/unsigned caller (as its `ensure_none`-style design and the existing "double-payment across messages for the same chain" bugfix comment both suggest batches of multiple `Message::Consensus` entries are an expected, supported operation mode). Any actor capable of running a light client for source chains (routine relayer tooling, fully public) can construct the exploit without insider access, cooperation, or timing races beyond simply choosing message order in their own submitted extrinsic.

### Recommendation
Attribute each `StateMachineUpdated` event's reward to the signer of the specific `Message::Consensus` that produced it, not to `messages[0]` globally. Concretely: build a map from `state_machine_id` (or message index) to its message's verified `signer`, derived while iterating `messages`, and only credit `highest_per_state_machine` reward to the signer whose message actually advanced that state machine. Reject batches where a `StateMachineUpdated` event cannot be matched to a verified `Message::Consensus` signer that targets that state machine.

### Proof of Concept
1. Configure `StateMachinesCostPerBlock` with a non-zero cost for state machine `B` (the target) and zero/negligible cost for a throwaway state machine `A` the attacker controls or doesn't care about.
2. Attacker fetches a currently valid, publicly available consensus proof advancing `B`'s trusted height by many blocks (same data any relayer would fetch from `B`'s own finality gadget).
3. Attacker crafts `Message::Consensus` #1 for `A` with a trivial/self-signed payload, and reuses/repackages the valid proof for `B` as `Message::Consensus` #2 (signer field irrelevant to the exploit since only message #1's signer will be inspected).
4. Attacker submits `vec![msg_A, msg_B]` to the ISMP message-handling extrinsic in one call.
5. Both messages verify against the ISMP host; `IsmpEvent::StateMachineUpdated` fires for both `A` and `B`.
6. `on_executed` extracts `relayer_account` solely from `msg_A`'s embedded signer (the attacker), then iterates `highest_per_state_machine` over both `A` and `B`, calling `process_message` for each — paying the attacker `blocks(B) * cost_per_block(B)` from the treasury plus minting matching reputation tokens, even though the attacker performed no meaningful work to produce `B`'s update. [4](#0-3)

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L77-99)
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
