## Summary of Investigation

No `WeightedFarmingPool.sol` exists in this repository, so I looked for the same underlying bug class — **a loop/batch that processes multiple periods/events but attributes the resulting value using stale or wrong per-item context** — across Hyperbridge's reward-distribution code. The strongest local analog is in `pallet-consensus-incentives`, which already carries a fix comment for a related double-payment bug but still has an unaddressed relayer-attribution flaw in the same function.

### Title
Consensus reward misattribution when a batch contains multiple consensus messages - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`on_executed` derives the reward recipient from **only the first message** in the batch (`messages.get(0)`), then loops over **all** `StateMachineUpdated` events produced by the entire batch and pays every one of them to that single relayer, regardless of which message in the batch actually produced which event.

### Finding Description
`FeeHandler::on_executed` is invoked with the full set of `messages` processed in one extrinsic call plus the full set of resulting `events`: [1](#0-0) 

The relayer identity is extracted once, from `messages[0]` only: [2](#0-1) 

The code already contains a comment acknowledging a related fix — collapsing multiple `StateMachineUpdated` events for the *same* state machine to avoid double-paying the same relayer for the same span: [3](#0-2) 

However, this fix only prevents *duplicate* payment to the correctly-identified relayer for the *same* chain; it does nothing about batches that legitimately mix consensus updates for **different state machines**, each potentially delivered/signed by different relayers. The final loop pays every one of those distinct state-machine rewards to the single relayer recovered from `messages[0]`: [4](#0-3) 

Since ISMP supports submitting multiple messages (including multiple `Message::Consensus` variants for different state machines) in a single call, and the router forwards the whole batch's `messages`/`events` to `on_executed` together, this is a reachable public-entrypoint condition: any relayer can construct or piggyback a batch that puts their own signed consensus message first, followed by other relayers' consensus messages that advance different state machines. All of those other rewards get diverted to the first relayer.

### Impact Explanation
This is a wrong-beneficiary bug: treasury funds (`$BRIDGE` + reputation asset) meant for the relayer that actually delivered a given state machine's consensus update are instead paid to whichever relayer happened to submit the first message in the batch. This is direct, protocol-level fund misdirection from the treasury, matching the "unauthorized transaction / wrong beneficiary" impact category in the bounty scope.

### Likelihood Explanation
Reaching this path requires no privileged access, malicious relayer collusion, or invalid proofs — it only requires that a normal batch submission (which any unprivileged actor building on top of `pallet-ismp`'s message-handling extrinsic can construct, subject to normal consensus-message validity) contains more than one valid `Message::Consensus` for different destination chains. Because `pallet-ismp` explicitly supports and encourages batching, and the pallet's own comment shows the team already reasoned about "a batch [containing] multiple ... events," a batch containing distinct state-machine updates from distinct signers is a realistic, not contrived, condition.

### Recommendation
Track and use the actual signer/relayer for **each** `Message::Consensus` entry in `messages`, and only pay the `StateMachineUpdated` events that correspond to the state machine(s) proven by that specific message's signer — e.g., by pairing each `Message::Consensus` with the `state_machine_id`(s) it targets before crediting rewards, instead of using a single relayer derived from `messages[0]` for the whole batch's event set.

### Proof of Concept
1. Relayer A crafts (or is first in) a batch submission to `pallet-ismp`'s message-handling extrinsic containing:
   - `messages[0]` = `Message::Consensus` signed by Relayer A, advancing `StateMachineId(X)`.
   - `messages[1]` = `Message::Consensus` signed by Relayer B, advancing `StateMachineId(Y)`.
2. `pallet-ismp` processes both messages, producing `events = [StateMachineUpdated{X, ...}, StateMachineUpdated{Y, ...}]`, and calls `FeeHandler::on_executed(messages, events)`.
3. `on_executed` decodes the signer only from `messages[0]` → Relayer A.
4. The loop over `highest_per_state_machine` pays the reward for **both** `X` and `Y` updates to Relayer A, even though Relayer B produced the `Y` update.
5. Relayer B's rightful `$BRIDGE` treasury reward and reputation-asset mint for chain `Y` are instead credited to Relayer A — confirmed by tracing `Self::process_message(..., relayer_account.clone().into())` being called once per state machine but always with the single `relayer_account` derived in step 3.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L108-123)
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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L124-145)
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
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L147-157)
```rust
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
