### Title
Consensus-incentive rewards misattributed to first message's signer in a batch, letting an attacker steal other relayers' rewards - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives::on_executed` reduces the SEDA-style "flat cost model" bug into a payout-attribution bug: it pays out the **entire** reward for every `StateMachineUpdated` event produced by a batch of ISMP messages to the signer of only the **first** message in that batch, regardless of which message in the batch actually advanced which chain's height. Since `pallet-ismp` processes a `Vec<Message>` per extrinsic call and this batch can legitimately contain multiple `Consensus` messages from different signers, an attacker can submit a batch where their own trivial/cheap consensus message occupies index 0 while other, more valuable state-machine advances (caused by other messages in the same batch) get credited entirely to them.

### Finding Description
`FeeHandler::on_executed` for `pallet-consensus-incentives` is implemented as: [1](#0-0) 

It extracts the relayer/signer **only from `messages.get(0)`**: [2](#0-1) 

Then it iterates the *entire* `events` list for the batch, deduplicating only by `state_machine_id` (to fix a previously-discovered double-pay bug), and pays a reward for **every** `StateMachineUpdated` event found — no matter which of the batch's `Consensus` messages actually produced it: [3](#0-2) 

The reward itself is computed from a flat per-block rate (`StateMachinesCostPerBlock`) times the number of newly-committed blocks, and paid directly from the treasury in real currency plus a proportional reputation mint: [4](#0-3) 

The code's own comment acknowledges that a single batch handled in one call can contain **multiple** `StateMachineUpdated` events for possibly different (or the same) state machines, produced by multiple `Consensus` messages submitted together: [5](#0-4) 

Because `pallet-ismp`'s message handler accepts a `Vec<Message>` per call (multiple relayers' consensus updates can be bundled into the same batch that gets executed and fed to `on_executed` together), the "first message wins" attribution model is broken: whichever `Consensus` message happens to sit at index 0 of the batch determines who is paid for **all** state-machine advances resulting from the whole batch, even those driven by other signers' proofs bundled alongside it.

### Impact Explanation
This directly matches "unauthorized transaction/execution" and "wrong beneficiary" in the bounty's impact list: treasury funds (real `Currency`, not just a synthetic reputation asset) are transferred to the wrong account. An attacker who can influence batch ordering (e.g. by submitting their own trivial `Consensus` message concurrently, front of a mempool-visible batch, or by being the first to get included by the block producer/queue) receives rewards that should have accrued to a different relayer who did the actual costly consensus-proof submission for a different (or larger) chain advance. This is a direct, repeatable fund-misdirection primitive against the treasury-funded relayer incentive pool, not merely a griefing or DoS issue.

### Likelihood Explanation
Exploitability depends on the ability to control which `Consensus` message lands at index 0 of a processed batch. Since `pallet-ismp` batches are built from the extrinsic's message list (order controlled by whoever submits the extrinsic, or by however messages get aggregated before dispatch), and multiple independent relayers' consensus messages can be coalesced into a single call, an attacker who submits (or races to be first in) a batch containing their own minimal consensus update alongside another relayer's larger update can realize this. The precondition is moderate (need a shared/aggregated batch containing a genuinely bigger advance authored by someone else), but the existing partial fix (collapsing per-state-machine duplicate events) shows the team already recognizes batches carry multiple consensus messages — the remaining "first message determines all payouts" flaw was not addressed.

### Recommendation
Attribute each `StateMachineUpdated` event's reward to the signer of the specific `Consensus` message that produced it, not to `messages.get(0)`. This requires correlating each event back to its originating message (e.g., by matching `state_machine_id` to the consensus message whose proof committed that state machine, iterating all `Consensus` messages in the batch and their own signers) rather than assuming a single relayer authored the whole batch.

### Proof of Concept
1. Governance sets `StateMachinesCostPerBlock` for chain A (cheap, attacker's chain) and chain B (any chain with a large pending height advance, e.g. because it hasn't been updated in a while).
2. Attacker crafts an extrinsic/batch containing two `Consensus` messages:
   - Message 0: a trivial consensus proof for chain A, signed by the attacker's relayer key, advancing chain A by 1 block.
   - Message 1: a legitimate large consensus proof for chain B (attacker may source/replay a validly-signed proof from another relayer, or simply be the one who happens to get sequenced first when both messages land in the same processed batch), advancing chain B by N blocks.
3. `pallet-ismp` verifies both proofs and emits `StateMachineUpdated` events for both chain A and chain B, then calls `FeeHandler::on_executed(messages, events)` with the full batch.
4. `on_executed` reads `messages.get(0)` → attacker's signature → attacker's account.
5. It then loops over **all** `StateMachineUpdated` events (chain A and chain B) and calls `process_message` for each, crediting the **entire** chain B reward (potentially large, `N × block_cost`) to the attacker's account via `T::Currency::transfer` from the treasury — money that should have gone to whoever actually delivered chain B's proof.
6. `LastRewardedHeight` for chain B is advanced, so the legitimate relayer who supplied that proof gets nothing when their own message is later processed (or was in the same batch but not at index 0).

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
