## Analysis

The Float Capital bug is a **broken invariant between the metric used to compute a payout and the actual work/risk being compensated** — the formula pays based on the wrong basis, decoupling reward from real contribution. The closest local analog in Hyperbridge is in `pallet-consensus-incentives`, where the relayer reward for a **batch** of consensus updates is attributed entirely to the signer of the *first* message in the batch, regardless of which message actually produced which `StateMachineUpdated` event. [1](#0-0) 

### Title
Consensus relayer reward is attributed to the first message's signer instead of the event's actual submitter, enabling reward hijacking - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`FeeHandler::on_executed` derives the sole reward recipient from `messages.get(0)` — the signer of the *first* message in the whole batch — then pays that single account for **every** `StateMachineUpdated` event produced by **any** message in the batch, across all state machines. Consensus proofs are public data (that is their entire purpose), so any unprivileged account can resubmit a legitimately verifiable, high-value consensus proof (large block-span advance) alongside a cheap, self-authored, independently-valid consensus proof placed first in the batch, and collect the reward for the large advance while contributing nothing to it.

### Finding Description
`Pallet::execute` in `modules/pallets/ismp/src/lib.rs`/`impls.rs` processes an arbitrary `Vec<Message>` submitted via the permissionless, unsigned `handle_unsigned` extrinsic: [2](#0-1) 

Each `Message::Consensus` carries its own `signer` field, verified independently against that message's own proof data — nothing ties a specific signer to a specific resulting `StateMachineUpdated` event beyond position in the batch.

In `pallet-consensus-incentives::on_executed`, the relayer is picked once from `messages.get(0)`: [3](#0-2) 

Then, for the **entire batch's** flattened `events` (across every message, every state machine), the code aggregates the highest height reached per `state_machine_id` and pays that single first-message signer for each one: [4](#0-3) 

`calculate_reward` sizes the payout purely by `(latest_height - baseline) * cost_per_block` for that chain — a large, legitimate block-span advance pays a large reward: [5](#0-4) 

None of the existing guards address this: the `challenge_period`/proof-verification checks in `handlers::handle_incoming_message` only validate that each consensus message is cryptographically and temporally valid, not who should be credited; the `LastRewardedHeight` watermark only prevents re-paying the *same* height span twice, not paying the *wrong* account for it.

### Impact Explanation
This is a "wrong beneficiary" logic attack on a live treasury-funded reward path: `T::Currency::transfer` moves real balance from `TreasuryAccount` to an attacker-controlled account, and `T::ReputationAsset::mint_into` mints reputation to that same wrong account: [6](#0-5) 

An attacker can systematically drain consensus-incentive rewards intended for the relayer that actually delivered the high-value proof, by front-loading their own cheap, self-signed consensus message ahead of any publicly observable proof for another chain in the same unsigned batch.

### Likelihood Explanation
Likelihood is high: `handle_unsigned` is explicitly permissionless/free by design (documented as "Execute the provided batch of ISMP messages for free with valid proofs"), consensus proofs are inherently public artifacts (BEEFY/consensus commitments, not secrets), and constructing a batch with attacker-message-first plus a legitimately-valid-but-not-attacker-authored consensus message requires no privileged role, no relayer/prover collusion, and no interception of a specific victim's pending transaction — only observation of publicly broadcast proof data, which any node can pick up from gossip.

### Recommendation
Attribute each `StateMachineUpdated` reward to the signer of the specific `Message::Consensus` that produced it, not to `messages[0]`. Pair each event with its originating message index/signer when building `highest_per_state_machine`, e.g. by zipping `messages_with_weights` (which already carries the `Message`) with the events they generated, rather than deriving one relayer for the whole batch from position 0.

### Proof of Concept
1. Legitimate relayer `R` broadcasts (or it becomes publicly visible via mempool/gossip) a valid `ConsensusMessage` `M_big` proving a large advance for `StateMachineId(A)` (e.g. `latest_height - baseline = 1000` blocks), signed by `R`.
2. Attacker `E` independently obtains/produces their own trivially valid `ConsensusMessage` `M_small` for `StateMachineId(B)` (e.g. a 1-block advance), signed by `E`.
3. `E` submits `Call::handle_unsigned { messages: [M_small, M_big] }` as an unsigned extrinsic (no fee, no signature required at the extrinsic level).
4. `Pallet::execute` processes both messages successfully; `events` contains `StateMachineUpdated` for both `A` and `B`.
5. `pallet_consensus_incentives::on_executed` reads `messages.get(0)` → `M_small`, recovers signer `E`; builds `highest_per_state_machine` from the full `events` list (both `A` and `B`); calls `process_message` for **both** state machines with `relayer_account = E`.
6. `calculate_reward` for `A` computes the large `1000 * cost_per_block` reward and transfers it from `TreasuryAccount` to `E`, even though `E` did not author `M_big` and contributed nothing to the state machine `A` update.

### Citations

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

**File:** modules/pallets/ismp/src/impls.rs (L40-57)
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
```
