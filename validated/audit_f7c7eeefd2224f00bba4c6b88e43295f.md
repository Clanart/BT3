`handle_unsigned` accepts an arbitrary `Vec<Message>` via `ensure_none` (no signature check on the caller), so anyone can submit a batch. `on_executed` in `pallet-consensus-incentives` attributes **every** reward in the batch to the signer recovered from `messages.get(0)` alone [1](#0-0) , then loops over *all* `StateMachineUpdated` events in the batch — including ones produced by other `Message::Consensus` entries later in the same batch — and pays the first message's signer for each of them [2](#0-1) .

### Title
Reward Misattribution in `pallet-consensus-incentives::on_executed` via Batched Consensus Messages - (File: modules/pallets/consensus-incentives/src/impls.rs)

### Summary
`on_executed` is invoked once per `handle_unsigned` call with the full `Vec<MessageWithWeight>` and `Vec<IsmpEvent>` produced by that call. It recovers a single relayer identity from `messages[0]` and then pays that one identity for the reward of *every* `StateMachineUpdated` event in the batch, regardless of which underlying consensus message (and therefore which real submitter/signer) actually produced each state-machine advance.

### Finding Description
`handle_unsigned` is a `ensure_none` origin extrinsic [3](#0-2) , meaning any unprivileged account can submit an arbitrary `Vec<Message>` in one call. Multiple independent `Message::Consensus` entries — each carrying its own embedded `signer` field proving who produced that specific consensus proof — can be bundled into one call. When `pallet-ismp` executes the batch, it collects all resulting `IsmpEvent::StateMachineUpdated` events across every message and passes the whole list, plus the whole `messages` vec, to `FeeHandler::on_executed` in one shot.

`on_executed` derives `maybe_relayer_account` exclusively from `messages.get(0)` — the *first* message's embedded signature — as the account to be paid for the batch [1](#0-0) . It then collapses all `StateMachineUpdated` events (correctly, to avoid the previously-noted double-count-per-height bug) but still pays every collapsed `(state_machine_id, latest_height)` entry to that single first-message signer [4](#0-3) . There is no check that the signer recovered from the first message actually corresponds to the specific `state_machine_id` whose `StateMachineUpdated` event is being rewarded.

An attacker can craft a batch consisting of: (1) their own cheap/no-op or self-authored consensus message placed at index 0, and (2) one or more genuine consensus messages/proofs for other, unrelated state machines (which can be legitimately re-submitted by anyone since consensus proofs are public data, not secrets) that will trigger real `StateMachineUpdated` events. Because reward attribution only looks at `messages[0]`'s signer, the attacker collects the `RewardTransferFailed`-checked treasury transfer and reputation mint for every state machine advanced in the batch, not just the one they authored.

### Impact Explanation
This directly causes **loss of funds and wrong-beneficiary payment** from the incentive treasury: `T::Currency::transfer` moves treasury balance to an account that did not deliver the corresponding consensus proof for that specific state machine [5](#0-4) , and `ReputationAsset::mint_into` similarly credits reputation to the wrong party [6](#0-5) . This matches the bounty's "stealing or loss of funds" / "wrong beneficiary or amount" impact class for relayer rewards.

### Likelihood Explanation
The attack requires no privileged role, no compromised relayer/prover, and no front-running — just constructing a batch of otherwise-valid, independently obtainable consensus messages/proofs and submitting them via the unsigned `handle_unsigned` entrypoint with the attacker's own message first. Consensus proofs for public chains are not secret, so an attacker can always append/repackage a legitimate proof someone else could have submitted, provided the state machine hasn't already been advanced to that height (in which case the legitimate submitter's own later resubmission would simply be a no-op producing no event, or would race the attacker).

### Recommendation
Attribute reward for each `StateMachineUpdated` event to the signer of the specific `Message::Consensus` entry that produced it, not to `messages[0]` globally. Iterate `messages` paired with the events each one emits (or match `consensus_state_id` from the event/state update back to the originating message) and recover/reward the correct per-message signer before applying `process_message`.

### Proof of Concept
1. Attacker observes an unprocessed, publicly available consensus proof `P` for chain `X` that would legitimately reward relayer `R` under `pallet-consensus-incentives`.
2. Attacker builds `Message::Consensus` `M0` for some other/no-op consensus update signed with the attacker's own keypair (any state machine with `StateMachinesCostPerBlock` configured, even a low-cost or already-caught-up one, to guarantee `messages[0]` is attacker-controlled).
3. Attacker submits `handle_unsigned(origin_none, vec![M0, P])` in a single extrinsic.
4. `pallet_ismp` executes both messages, producing `StateMachineUpdated` events for both `M0`'s and `P`'s target chains.
5. `on_executed` recovers the relayer solely from `M0`'s signer (the attacker) and pays the attacker for both the `M0` chain's block-span reward **and** the `X` chain's block-span reward intended for `R`, as shown by the reward loop keying off `messages.get(0)` alone [7](#0-6) .
6. `R`, who actually produced/would have submitted proof `P`, receives nothing for chain `X`'s advance; the attacker walks away with treasury funds and reputation tokens for work they did not perform.

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L53-59)
```rust
			T::Currency::transfer(
				&T::TreasuryAccount::get().into_account_truncating(),
				&relayer_account,
				reward,
				Preservation::Expendable,
			)
			.map_err(|_| Error::<T>::RewardTransferFailed)?;
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L67-68)
```rust
			T::ReputationAsset::mint_into(&relayer_account, reward.saturated_into())
				.map_err(|_| Error::<T>::ReputationMintFailed)?;
```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L112-156)
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

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L103-107)
```rust
		pallet_ismp::Pallet::<Test>::handle_unsigned(
			RuntimeOrigin::none(),
			vec![consensus_message],
		)
		.unwrap();
```
