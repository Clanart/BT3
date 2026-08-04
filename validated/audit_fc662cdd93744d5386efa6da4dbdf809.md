### Title
`RelayerIncentives::on_executed` attributes every consensus reward in a batch to the first message's signer, letting an attacker steal other relayers' rewards - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`pallet-consensus-incentives`'s `FeeHandler::on_executed` receives a batch of `MessageWithWeight` (multiple `ConsensusMessage`s can be submitted together in one unsigned `handle_unsigned` call, since ISMP consensus submission is permissionless) plus the `IsmpEvent`s that batch produced. It derives the reward beneficiary **once**, from `messages.get(0)` only, then pays that single relayer for **every** `StateMachineUpdated` event in the batch — even ones produced by other `ConsensusMessage`s in the same batch that are signed by different relayers.

### Finding Description
`on_executed` in [1](#0-0)  recovers a single `relayer_account` by decoding the signature carried in `messages[0]` (`Message::Consensus`), binding it only to that first message's own `consensus_proof` bytes. It then collapses all `StateMachineUpdated` events across the whole batch into one entry per `state_machine_id` (picking the highest `latest_height`) and pays `Self::process_message` — i.e. the full `(latest_height - baseline) * cost_per_block` treasury reward — to that same first-message relayer for **every** state machine advanced in the batch: [2](#0-1) .

The reward payout has no per-event linkage back to which specific `ConsensusMessage` (and therefore which relayer's signature) actually produced each `StateMachineUpdated` event; it only ever looks at `messages[0]`. Since `pallet-ismp`'s consensus submission is unsigned/permissionless, and the batch of `Message`s dispatched in one extrinsic is fully attacker-controlled (an attacker packages their own consensus message as index 0, alongside one or more other already-broadcast, independently-signed `ConsensusMessage`s targeting different state machines — each such message is self-contained and verifiable on its own merits, so the ISMP verification pipeline in `modules/ismp/core/src/handlers/consensus.rs` will happily process and accept them regardless of batch order), the attacker collects the reward for state-machine advances they did not actually deliver the proof for, while the relayer who genuinely produced/signed those other consensus messages receives nothing.

This is a direct analog of the Astaria `_makePayment` bug: a batch operation over multiple "debts" (state-machine advances) determines the payee from the first item only, and the entire payout for the whole batch is routed to that first payee, "wrong beneficiary" for every subsequent item in the batch.

### Impact Explanation
Every treasury-funded consensus-relay reward in a multi-message batch beyond the first is misattributed. The rightful relayer(s) who actually verified and delivered proofs for the other state machines lose their earned `$BRIDGE` reward and reputation mint, while an attacker who merely rebroadcasts already-public signed messages (bundling them behind their own message) collects rewards they did not earn. This is a direct, repeatable "wrong beneficiary" fund-loss bug against the protocol treasury and against honest relayers, matching the bounty's "stealing or loss of funds ... transaction manipulation ... false ... acceptance" categories. It also incentivizes griefing: an attacker with negligible relaying work can drain treasury rewards intended for the operators who actually keep Hyperbridge's view of connected chains current.

### Likelihood Explanation
The exploit path requires only an unprivileged, permissionless actor able to submit an unsigned ISMP message batch (which is exactly how ordinary consensus relaying works) — no malicious relayer/operator collusion, no admin/governance action, and no reliance on breaking any cryptographic signature (each individual `ConsensusMessage` remains independently valid; only the reward-attribution logic is broken). The only "front-running" element is that the attacker must observe an already-signed but not-yet-executed consensus message (public data, since it's an unsigned transaction sitting in a public mempool with an embedded signature over its own proof) and re-submit it bundled behind their own message — this is incidental to the core logic bug (wrong beneficiary picked from `messages[0]`), not the primary mechanism of the bug itself. Given consensus relaying is an active, competitive, permissionless market on Hyperbridge, batches containing multiple consensus messages are an expected occurrence, making the misattribution realistically triggerable.

### Recommendation
Do not derive a single relayer account for the whole batch. Instead, for each `Message::Consensus` in `messages`, recover its own signer and pair it with the specific `StateMachineUpdated` event(s) that message actually produced (e.g., correlate via the state machine id(s)/heights present in that message's own intermediate states, or have `update_client` return the state updates keyed per originating message rather than as a flat `Vec<Event>` disconnected from `messages`). Reward should only ever be paid to the relayer whose signature covers the specific consensus proof that produced a given `StateMachineUpdated` event.

### Proof of Concept
1. Relayer `R1` builds and broadcasts (as an unsigned extrinsic) a batch `[ConsensusMessage_A]` advancing `StateMachine::X`, signed with `R1`'s key over `keccak256(consensus_proof_A)`. This transaction is visible, unconfirmed, in the public mempool.
2. Attacker `A` observes `ConsensusMessage_A` in the mempool (it is public, unsigned-extrinsic data) and constructs their own batch: `[ConsensusMessage_B, ConsensusMessage_A]`, where `ConsensusMessage_B` is `A`'s own valid consensus proof advancing `StateMachine::Y`, placed at index 0, and `ConsensusMessage_A` (copied verbatim, still signed by `R1`) is placed after it.
3. Attacker submits this batch first (or with higher priority). `pallet-ismp`'s `handle_unsigned` verifies each message and calls `update_client`, producing `StateMachineUpdated` events for both `X` and `Y` (see [3](#0-2) ).
4. `FeeHandler::on_executed` is invoked with `messages = [ConsensusMessage_B, ConsensusMessage_A]` and `events = [StateMachineUpdated(X), StateMachineUpdated(Y)]`. It reads `messages.get(0)` → `ConsensusMessage_B` → recovers `relayer_account = A` [4](#0-3) .
5. The collapsed `highest_per_state_machine` loop then calls `process_message` for **both** `X` and `Y` using `relayer_account = A` [5](#0-4) , transferring treasury funds and minting reputation to `A` for the `X` update that `R1` actually produced and signed.
6. `R1`'s original transaction, once it eventually lands (or fails as a no-op duplicate since the state was already advanced by the copied message), earns `R1` nothing for the work of producing `ConsensusMessage_A`.

Note: I could not fully trace `handle_unsigned`'s exact batching/dispatch code path in this pass (only found references, not the full implementation body) to confirm there is no batch-level signer/origin binding that would prevent an attacker from freely composing `[own_message, copied_message]` in one submission; this should be verified directly in `modules/pallets/ismp/src/lib.rs` / `impls.rs` before treating this as fully confirmed, but the reward-attribution logic itself (`messages.get(0)` deciding the payee for the whole batch) is confirmed present and is the root defect regardless of exact batching mechanics.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L108-122)
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
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L124-157)
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
		}
```

**File:** modules/ismp/core/src/handlers/consensus.rs (L41-80)
```rust
	let (new_state, intermediate_states) = consensus_client.verify_consensus(
		host,
		msg.consensus_state_id,
		trusted_state,
		msg.consensus_proof,
	)?;
	host.store_consensus_state(msg.consensus_state_id, new_state)?;
	let timestamp = host.timestamp();
	host.store_consensus_update_time(msg.consensus_state_id, timestamp)?;
	let mut state_updates = vec![];
	for (id, mut commitment_heights) in intermediate_states {
		commitment_heights.sort_unstable_by(|a, b| a.height.cmp(&b.height));
		let previous_latest_height = host.latest_commitment_height(id)?;
		let mut last_commitment_height = None;
		for commitment_height in commitment_heights.iter() {
			let state_height = StateMachineHeight { id, height: commitment_height.height };

			// Only allow heights greater than latest height
			if previous_latest_height > commitment_height.height {
				continue;
			}

			// Skip duplicate states
			if host.state_machine_commitment(state_height).is_ok() {
				continue;
			}

			last_commitment_height = Some(state_height);
			host.store_state_machine_commitment(state_height, commitment_height.commitment)?;
			host.store_state_machine_update_time(state_height, host.timestamp())?;
		}

		if let Some(latest_height) = last_commitment_height {
			let latest_height = StateMachineHeight { id, height: latest_height.height };
			state_updates.push(Event::StateMachineUpdated(StateMachineUpdated {
				state_machine_id: id,
				latest_height: latest_height.height,
			}));
			host.store_latest_commitment_height(latest_height)?;
		}
```
