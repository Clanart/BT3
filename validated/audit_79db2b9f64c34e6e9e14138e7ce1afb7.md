Based on my investigation, I found a real analog in `pallet-consensus-incentives`.

### Title
Relayer reward misattribution in batched consensus messages — first-message signer collects rewards for all state machines advanced in the batch - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`FeeHandler::on_executed` recovers exactly one relayer identity — from `messages.get(0)` — and then pays that single recovered relayer for **every** `StateMachineUpdated` event present in the whole batch, even when the batch contains multiple `ConsensusMessage`s (potentially advancing different state machines, each cryptographically signed by a *different* relayer's key). [1](#0-0) 

### Finding Description
`on_executed` looks only at `messages.get(0)` to recover a relayer's public key from that single consensus message's embedded signature: [2](#0-1) 

It then iterates over **all** `StateMachineUpdated` events in the batch (collapsed per state machine to the highest height) and calls `process_message`/`calculate_reward` for each one, crediting the reward and reputation asset to that single `relayer_account` recovered from message 0: [3](#0-2) 

Since ISMP consensus proofs and their embedded relayer signatures are public data (any relayer's proof + signature can be freely observed once broadcast, as the report's own architecture relies on "anyone can generate/submit proofs"), an attacker can:
1. Grab another relayer's already-signed `ConsensusMessage` for chain B (public, since it must be publicly verifiable) that they never delivered.
2. Craft/attach their own `ConsensusMessage` for chain A, signed with their own key, as `messages[0]`.
3. Submit the two-message batch via `handle_unsigned` so that both consensus updates land, and thus both `StateMachineUpdated` events for A and B are emitted.

Because only `messages[0]`'s signer is recovered, the attacker's own account (message 0) receives the reward for **both** chain A's and chain B's advance — including the reward that should have gone to the actual relayer who produced/signed chain B's proof. This is directly analogous to `MyStaking.sol`'s bug: a single "identity/checkpoint" value (there `stakeBegin`, here `relayer_account` from message 0) is computed once and then incorrectly reused across every reward calculation in the batch, producing an incorrect beneficiary/fee outcome for all but the first entry.

### Impact Explanation
This is a direct fund-diversion bug: the `TreasuryAccount` pays out `Reward` tokens and mints `ReputationAsset` to the wrong account for every additional state-machine advance batched alongside the attacker's own message. The actual relayer who performed the work for chain B receives nothing, while the attacker receives rewards for work they never did — satisfying "stealing or loss of funds" / "wrong beneficiary or amount" under the impact gate. No malicious peer, prover, or relayer collusion is required beyond the attacker's own account submitting a normal unsigned extrinsic bundling publicly-available consensus data.

### Likelihood Explanation
Any unprivileged actor able to submit ISMP messages (this is an unsigned, permissionless inherent-style call per the pallet's design — `pallet_ismp::Pallet::handle_unsigned`) can construct such a batch as long as they can obtain a second chain's already-produced/broadcast consensus proof message. Given multiple relayers operate concurrently and proofs must be publicly verifiable to be useful at all, this condition is easily met in production, making the likelihood high whenever more than one state machine's consensus update lands in the same processed batch — a normal, expected occurrence, not an edge case.

### Recommendation
Do not derive a single relayer identity for a whole batch. Instead, recover and pay the relayer per `Message::Consensus` entry individually, matching each recovered signer to the specific `StateMachineUpdated` event(s) that its own proof caused, e.g. by tracking `(state_machine_id -> signer)` pairs from each consensus message's signature before iterating `events`, and paying only the signer whose message actually produced that state machine's update.

### Proof of Concept
1. Relayer R2 submits/broadcasts a valid `ConsensusMessage` (`msg_B`) that would advance state machine B, signed with R2's key.
2. Attacker R1 observes `msg_B` (public data) before it lands, or intercepts it prior to inclusion.
3. Attacker submits `handle_unsigned` with `messages = [msg_A (signed by R1), msg_B (signed by R2, copied from step 1)]`.
4. Both messages process successfully; `on_executed` receives `events` containing `StateMachineUpdated` for both A and B.
5. `on_executed` recovers only `R1` from `messages[0]` and calls `process_message` for both A's and B's height advances, crediting `R1`'s account with the reward and reputation asset for chain B's advance — money that should have gone to R2. [4](#0-3)

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
