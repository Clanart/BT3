## Reward Misattribution in `pallet-consensus-incentives::on_executed` — First Message's Signer Steals Rewards for the Entire Batch

### Summary
`pallet_consensus_incentives::Pallet::on_executed` (the `FeeHandler` implementation invoked by `pallet-ismp` after a batch of messages is processed) derives the reward recipient (`relayer_account`) exclusively from the **first message** in the submitted batch, but then pays that single account for **every** `StateMachineUpdated` event produced by the **entire batch**, across all state machines. An attacker who prepends their own signed consensus message to a batch that also carries other relayers' legitimate consensus proofs collects all of the rewards, while `LastRewardedHeight` is advanced for every affected chain — permanently forfeiting the rightful relayers' rewards, exactly analogous to the reported `lastStem` forfeiture pattern (a shared accounting watermark is moved by an operation that should only affect a subset of the underlying items).

### Finding Description
`on_executed` computes the reward recipient once, from `messages[0]`: [1](#0-0) 

It then builds `highest_per_state_machine` from **all** `StateMachineUpdated` events in the batch — not filtered by which message in the batch produced them — and pays `relayer_account` (i.e. the signer of `messages[0]` only) once per state machine represented in that map: [2](#0-1) 

`process_message` then both transfers the treasury reward to that single account and unconditionally advances the shared watermark: [3](#0-2) 

`LastRewardedHeight` is a single value per `StateMachineId`, not per relayer: [4](#0-3) 

`calculate_reward` uses this watermark as the floor for the next reward calculation, so once it is bumped past a height, nobody can ever be paid for that span again: [5](#0-4) 

There is no check that every consensus message in the batch was signed by the same account, nor any per-message attribution of which `StateMachineUpdated` event resulted from which message. `Message::handle`-style batch submission on `pallet-ismp` is permissionless (any caller can submit a `Vec<MessageWithWeight>` containing consensus proof messages targeting different state machines/relayers), and the "relayer" attribution used here comes purely from a self-supplied signature (`consensus_msg.signer`) over the consensus proof bytes — it authenticates *who is claiming to have delivered it*, not the underlying consensus proof's validity, which is checked elsewhere. Anyone can attach a valid signature of their own key over any consensus proof bytes they resubmit.

### Impact Explanation
This is a direct fund-theft / wrong-beneficiary bug against the Hyperbridge treasury and against honest relayers:
- An attacker can front-load a batch with their own cheaply-signed consensus message for state machine A, batched alongside honest relayers' consensus messages for state machines B, C, D that would otherwise legitimately reward those relayers.
- All resulting `StateMachineUpdated` events across A, B, C, D get attributed to the attacker's account, draining treasury `T::Currency::transfer` payouts and `ReputationAsset` mints to the attacker instead of the honest relayers.
- `LastRewardedHeight` for B, C, D is bumped to the new heights, so the legitimate relayers can never claim the reward for that span even after the theft is discovered — this is a one-time, irreversible, unauthorized transfer of funds to the wrong beneficiary, matching the bounty's "stealing or loss of funds" / "unauthorized transaction" / "wrong beneficiary or amount" categories.

### Likelihood Explanation
Any unprivileged account that can submit an ISMP message batch (via the standard `pallet-ismp` handling path) controls this: batching is a normal usage pattern already exercised by relayers submitting multiple consensus/request messages together, and the reward-attribution code path has no defense against mixed-signer batches. No malicious peer, prover, or governance actor is required — a single unprivileged caller assembling a batch is sufficient.

### Recommendation
- Attribute rewards per-message rather than per-batch: track which `Message::Consensus` (and its verified signer) produced which `StateMachineUpdated` event, and only reward the signer of the message that actually advanced that specific state machine's commitment.
- Reject or split batches containing consensus messages signed by different accounts, or require an explicit per-state-machine leg (message → event → signer) rather than defaulting to `messages[0]`.
- Only advance `LastRewardedHeight` for the specific `(state_machine_id, signer)` pair being rewarded, not implicitly for every state machine touched by an unrelated message in the same batch.

### Proof of Concept
1. Attacker prepares a batch `messages = [M_attacker, M_honest_B, M_honest_C]` where:
   - `M_attacker` is a `Message::Consensus` whose `consensus_proof`/`signer` fields are signed with the attacker's own sr25519 key (valid signature verification only requires the attacker sign the hash of their own submitted proof bytes) targeting state machine `A`.
   - `M_honest_B`, `M_honest_C` are legitimate consensus proofs relayed for state machines `B` and `C`, which would normally reward the honest relayers who produced them.
2. Attacker submits the whole batch via the normal `pallet-ismp` handling extrinsic in one call.
3. `pallet-ismp` processes the batch, producing `StateMachineUpdated` events for `A`, `B`, and `C`.
4. `FeeHandler::on_executed` is invoked once for the whole batch: `maybe_relayer_account` resolves to the attacker's key (from `messages[0]`) per [6](#0-5) .
5. The loop over `highest_per_state_machine` (built from *all* events in the batch) pays the attacker's account for `A`, `B`, and `C`, and bumps `LastRewardedHeight` for all three per [7](#0-6) .
6. The honest relayers who actually delivered `M_honest_B` and `M_honest_C` receive nothing, and can never be paid for those heights again because the watermark has moved past them.

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

**File:** modules/pallets/consensus-incentives/src/lib.rs (L81-86)
```rust
	/// The highest height a relayer has already been paid for, per state machine. Rewards only
	/// ever cover the span above this watermark, so a height that is revisited after a rollback
	/// is not paid for twice.
	#[pallet::storage]
	pub type LastRewardedHeight<T: Config> =
		StorageMap<_, Blake2_128Concat, StateMachineId, u64, OptionQuery>;
```
