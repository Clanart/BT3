### Title
Reward misattribution in `pallet-consensus-incentives::on_executed` allows an attacker to steal other relayers' consensus rewards - ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
The external report's core defect is an **order/attribution bug**: a shared resource (yield/reward) computed over a batch of state changes is credited to the wrong party because the code assumes a single implicit actor is responsible for the whole batch, when in fact multiple distinct contributions are folded into it. The local analog is in `pallet-consensus-incentives::on_executed`, which pays out the entire `$BRIDGE` treasury reward for **every** `StateMachineUpdated` event in a processed batch to whichever account signed **only the first message** in that batch — even when other, unrelated messages in the same batch are what actually produced some of those events.

### Finding Description
`on_executed` [1](#0-0)  extracts a single `relayer_account` exclusively from `messages.get(0)`, decoding the embedded self-signature and recovering the sr25519 public key of whoever authored that first `Message::Consensus`.

It then walks the **entire** `events` list for the call, collapsing all `IsmpEvent::StateMachineUpdated` occurrences — which can span multiple, unrelated `state_machine_id`s, as the code's own comment acknowledges ("When a batch contains multiple `StateMachineUpdated` events for the same `state_machine_id`... Collapse the per-state-machine event stream...") [2](#0-1)  — and pays the **single** `relayer_account` extracted from message 0 for every one of them via `process_message`, which transfers `$BRIDGE` from the treasury and mints reputation [3](#0-2) .

`ConsensusMessage.signer` is a **self-embedded attestation** signed client-side by whoever builds the extrinsic — it is not tied to block-production authority or to which proof within the batch is "theirs." Because BEEFY/consensus proofs for tracked state machines are public, permissionless data (anyone can independently fetch or reconstruct a valid proof once it exists), an unprivileged actor can:
1. Craft or acquire any valid `Message::Consensus` for a state machine with a high `CostPerBlock` (large reward) — this proof need not be theirs; it only has to be independently valid.
2. Prepend a trivial, self-signed `Message::Consensus` (even one with zero or minimal progress, so its own reward is negligible) as `messages[0]`.
3. Submit both in a single unsigned batch to `pallet-ismp`'s message handler.

`on_executed` receives the full batch, extracts the signer from message 0 (the attacker), and — because reward attribution ignores which message actually produced which `StateMachineUpdated` event — pays out the reward for **every** state machine advanced by the batch, including the one whose proof the attacker merely copied, to the attacker's account.

### Impact Explanation
This is unauthorized diversion of treasury funds to the wrong beneficiary: `$BRIDGE` intended to reward the relayer who actually delivered a given state machine's consensus update instead flows to an attacker who contributed nothing but a throwaway self-signed message placed first in the batch. This directly matches the bounty's "stealing or loss of funds" and "wrong beneficiary or amount" categories, and requires no malicious relayer/prover/admin — any account able to submit an unsigned extrinsic and observe public consensus proofs can exploit it.

### Likelihood Explanation
The precondition is only that a single call to `on_executed` process more than one `Message::Consensus` whose combined `events` span more than one `state_machine_id` (or otherwise attribute more reward than message 0 alone earned) — a scenario the pallet's own code comments explicitly anticipate ("a batch contains multiple `StateMachineUpdated` events... for the same state_machine_id" and the `BTreeMap<StateMachineId, u64>` collapsing logic which only makes sense if multiple distinct state machines can appear). Consensus proofs are public once produced, so acquiring a valid proof "for someone else's" pending update is not privileged. This makes the attack realistically executable by any unprivileged, off-chain observer of the relayer network.

### Recommendation
Attribute each `StateMachineUpdated` event to the signer of the specific `Message::Consensus` that produced it, rather than defaulting to `messages[0]`'s signer for the whole batch. Concretely, pair each `Message::Consensus` entry with the `StateMachineUpdated` event(s) it generates (e.g., by index or by `state_machine_id` correlation at dispatch time in `pallet-ismp`), recover a signer per message, and call `process_message` with that message's own recovered account instead of a single batch-wide account.

### Proof of Concept
Not independently executable without a running node/testnet; the exploit path is derived purely from static analysis of the cited code:
1. Attacker constructs `msg_A = Message::Consensus { consensus_proof: <trivial/self, e.g. zero-progress proof for a cheap chain>, signer: <attacker's own signature over msg_A> }`.
2. Attacker independently obtains `msg_B = Message::Consensus { consensus_proof: <valid, publicly available proof advancing a high-`CostPerBlock` state machine>, signer: <anything, unused> }`.
3. Attacker submits `handle_messages([msg_A, msg_B])` (or the pallet-ismp equivalent unsigned batch call).
4. `pallet-ismp` processes both, emitting `StateMachineUpdated` for both state machines, and calls `on_executed(vec![msg_A_weighted, msg_B_weighted], events)`.
5. `on_executed` derives `relayer_account` solely from `msg_A`'s signer (the attacker) at [4](#0-3) , then rewards that account for **both** state machine updates, including the one from `msg_B` which the attacker did not originate, via the loop at [5](#0-4) .

### Citations

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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L104-122)
```rust
impl<T: Config> FeeHandler for Pallet<T>
where
	<T as frame_system::Config>::AccountId: From<[u8; 32]>,
{
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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L147-156)
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
```
