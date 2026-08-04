### Title
Reward-theft in `pallet-consensus-incentives`: relayer credit for an entire batch is attributed to the signer of message index 0 only - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`FeeHandler::on_executed` in `pallet-consensus-incentives` extracts the rewarded relayer identity **only** from `messages[0]`, then pays that single account for **every** `StateMachineUpdated` event produced anywhere in the whole executed batch, regardless of which consensus message actually produced each event. [1](#0-0) 

### Finding Description
`pallet-ismp`'s permissionless message-execution entrypoint accepts an arbitrary `Vec<Message>` batch from any signed account and, after processing, invokes `FeeHandler::on_executed(messages, events)` with the full list of processed messages and the full list of emitted `IsmpEvent`s for that batch.

`pallet-consensus-incentives::on_executed` derives the *rewarded* relayer identity from only the **first** message in the batch: [2](#0-1) 

It then walks **all** events in the batch, collapses them to the highest `latest_height` per `state_machine_id` (a de-dup step meant to avoid double-paying repeated updates to the same chain within one batch), and pays that single `relayer_account` — the signer recovered from `messages[0]` — for every distinct state machine that advanced in the batch: [3](#0-2) 

There is no check that the `StateMachineUpdated` event for a given `state_machine_id` actually originated from `messages[0]`'s `Consensus` proof. Since `pallet-ismp` batches are freely composable by any submitting account, an unprivileged caller can:

1. Craft their own valid `Message::Consensus` for state machine A (which they sign) as `messages[0]`.
2. Append one or more **already-valid, publicly observable** `Message::Consensus` proofs for other state machines (B, C, …) — for example, proofs originally crafted/signed by other relayers that are visible in the mempool, on an explorer, or via any public consensus-update feed, or their own additional consensus updates for unrelated chains — as subsequent batch entries.
3. Submit the whole batch in a single extrinsic.

`pallet_ismp` will process and validate every message in the batch independently (proof validity is per-message and unaffected by ordering), so all `StateMachineUpdated` events fire normally. But the FeeHandler only recovers `relayer_account` from `messages[0]`, and pays that single account the treasury reward for **all** state machine advances in the batch — including the ones the attacker did no cryptographic/consensus work to produce and that were meant to reward whoever actually built proof B, C, etc.

This is a direct violation of the reward-claim invariant: rewards must move exactly once and only to the rightful beneficiary. Existing guards (the per-state-machine watermark `LastRewardedHeight` and the `highest_per_state_machine` collapse) only prevent double-payment for the *same* chain within a batch — they do nothing to verify that the credited account is the one whose proof produced the event. There is no check that `state_machine_id` in the loop corresponds to `messages[0]`'s target state machine.

### Impact Explanation
This lets any unprivileged account siphon consensus-relaying rewards from the treasury that are meant for the party who actually procured/submitted the underlying proof for other state machines, simply by bundling any publicly-available valid consensus message with their own in the same extrinsic and putting their own message first. This is a fund-diversion bug in the reward/incentive path — an unauthorized transfer to the wrong beneficiary — not a DoS or governance/relayer-compromise issue, and it needs no malicious relayer, prover, or admin: any transaction sender who observes a pending or already-broadcastable consensus proof can rebatch it under their own account.

### Likelihood Explanation
Consensus proofs are not secrets — they are inherently public artifacts submitted to the chain (or otherwise observable pre-inclusion), so an attacker only needs to construct a batch extrinsic with their own message at index 0 and any other valid consensus message(s) appended. No special access, timing race with a specific relayer, or governance/relayer compromise is required, making this practically exploitable whenever `StateMachinesCostPerBlock` is non-zero for more than one state machine, which is the pallet's normal operating condition.

### Recommendation
Attribute each `StateMachineUpdated` reward to the relayer that actually authored the specific `Message::Consensus` responsible for that event, rather than defaulting to `messages[0]`. Concretely:
- Iterate `messages` (not just index 0), and for each `Message::Consensus`, recover its own signer and associate it with the `state_machine_id`(s) it is proven to have advanced.
- Only pay `Self::process_message` using the relayer recovered from the specific message that produced the matching `StateMachineUpdated` event, rejecting/skip-crediting events that cannot be matched to a message in the same batch, instead of blanket-crediting all events to the first message's signer.

### Proof of Concept
Conceptually (Rust pseudo-flow reflecting the actual code path):
1. Attacker submits `pallet_ismp` batch execution with `messages = [Consensus(proof_A, signed_by_attacker), Consensus(proof_B, signed_by_relayer_B)]` where `proof_B` is a legitimate, already-observed consensus proof for a different state machine that relayer B intended to submit for their own reward.
2. `pallet_ismp` processes both messages successfully (each verifies independently), emitting `StateMachineUpdated{state_machine_id: A, ...}` and `StateMachineUpdated{state_machine_id: B, ...}`.
3. `on_executed` is invoked with `messages` and `events`. `maybe_relayer_account` is computed only from `messages[0]` → attacker's account. [2](#0-1) 
4. The `highest_per_state_machine` map now contains both `A` and `B`; the loop calls `process_message` for both, using the attacker's `relayer_account` for both: [4](#0-3) 
5. `calculate_reward` computes and pays out the full block-span reward for state machine `B` to the attacker's account from the treasury, even though the attacker contributed nothing toward advancing `B`'s consensus state — relayer B, who produced `proof_B`, receives nothing for that update.

### Citations

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
