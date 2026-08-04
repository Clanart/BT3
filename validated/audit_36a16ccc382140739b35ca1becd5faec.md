## Title
Reward misattribution in `on_executed` lets one relayer collect the entire consensus-incentive treasury payout for a batch, denying legitimate co-relayers their reward - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

## Summary
`pallet-consensus-incentives::on_executed` identifies "the relayer to reward" by decoding the signer embedded in **only the first message** of the batch (`messages.get(0)`), but then pays that single signer for **every** `StateMachineUpdated` event produced by the *entire* batch, regardless of which underlying `ConsensusMessage` (and therefore which embedded `signer`) actually produced each event.

## Finding Description
This mirrors the Derby bug's broken invariant: a value that is supposed to track "who/what actually produced this yield/update" is decoupled from the value that pays out rewards, so the payout no longer reflects the real contribution.

In `on_executed`: [1](#0-0) 

- The relayer identity is extracted once, from `messages.get(0)`, decoding `consensus_msg.signer` and recovering the sr25519 public key.
- The function then iterates over **all** `IsmpEvent::StateMachineUpdated` events in the batch (which can be produced by multiple distinct `ConsensusMessage`s bundled in the same `handle_messages` call, each potentially carrying a different, independently verified `signer`), collapses them to one highest-height event per state machine, and pays `relayer_account` (the message-0 signer) for every one of them via `process_message` / `calculate_reward`: [2](#0-1) 

Because consensus proofs are public data by protocol design (anyone can submit anyone's valid, publicly-broadcast consensus proof — this is explicitly documented as intentional permissionless relaying), an attacker does not need to compromise a peer, prover, or relayer to exploit this. They only need to construct a single `handle_messages` submission that bundles:
1. Their own trivially-verifiable `ConsensusMessage` as index 0 (signed by themselves), and
2. One or more additional, independently valid `ConsensusMessage`s for other state machines that happen to be sitting in the public mempool/gossip layer (submitted or about-to-be-submitted by other honest relayers), each carrying a *different* signer in their payload.

If the attacker's batch lands first, `on_executed` still only reads `messages[0].signer`, so the treasury reward for *every* `StateMachineUpdated` in the batch — including the ones whose message body was signed by other relayers — is transferred entirely to the attacker's account, and the corresponding reputation asset is minted only to the attacker. The honest relayers whose messages were absorbed into someone else's batch receive nothing for their (real, separately verified) consensus updates.

No existing guard checks that the paid-out signer matches the signer embedded in the specific message that produced each `StateMachineUpdated` event; the code comment only defends against *duplicate* rewards for the *same* state machine within a batch, not against *misattributed* rewards across different signers in the same batch.

## Impact Explanation
This is a direct "wrong beneficiary" fund-movement bug against the Hyperbridge treasury (`T::TreasuryAccount`): BRIDGE tokens and reputation-asset mints that are supposed to go to the relayer who actually produced/signed a given consensus update instead go to whichever unrelated relayer happened to submit the enclosing batch first. This satisfies the bounty's "wrong beneficiary or amount" and "unauthorized... execution / transaction manipulation" categories — treasury funds are moved to a party with no valid claim to them for the misattributed events, at the honest relayer's expense. It requires no admin, governance, prover compromise, or malicious peer — an ordinary unprivileged actor able to submit `handle_messages` (an unsigned/permissionless extrinsic path) can trigger it using only publicly observable data.

## Likelihood Explanation
Likelihood is significant in any period with multiple active relayers, since consensus messages are batched per `handle_messages` call and it is normal/expected for an operator's tesseract client to submit several `ConsensusMessage`s together in one call. An attacker only needs to append a foreign, already-broadcast consensus proof to their own batch before the honest relayer's own submission lands — a timing race that is easy to win against relayers who batch less aggressively or run less frequently, and requires no cryptographic forgery since the foreign proof/signature is valid and public.

## Recommendation
Attribute and pay the reward per-message rather than per-batch: for each `Message::Consensus` in `messages`, decode its own embedded `signer`, and only credit rewards for the `StateMachineUpdated` events that are causally linked to that specific message's height range (or that message's own state-machine id), rather than crediting the first message's signer for the union of all events in the entire batch.

## Proof of Concept
1. Honest relayer B constructs a valid `ConsensusMessage` for `StateMachineId::X`, embedding `signer = B`, and broadcasts/submits it (or it is visible pre-inclusion since consensus proofs are public data).
2. Attacker A observes B's pending/broadcast consensus proof, copies the raw proof bytes, and constructs a `handle_messages` call whose `messages` vector is `[A's own trivial/cheap ConsensusMessage for chain Y (signer=A), B's copied ConsensusMessage for chain X (signer=B, unmodified — still cryptographically valid because A didn't need to forge anything)]`.
3. A submits this batch first. `pallet-ismp` verifies both messages independently and successfully applies both consensus updates, emitting `StateMachineUpdated` for both X and Y.
4. `on_executed` is invoked with `messages = [A_msg, B_msg]` and `events = [Update(X), Update(Y)]`. It decodes the relayer only from `messages[0]` (A), then calls `process_message` for **both** X and Y using `relayer_account = A`.
5. Result: A receives the treasury `$BRIDGE` reward and reputation-asset mint for **both** chain X and chain Y advances, even though B independently produced and signed the valid proof for X. B receives nothing for that update.

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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L108-146)
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

```
