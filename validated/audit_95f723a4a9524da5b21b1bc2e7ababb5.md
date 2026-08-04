## Analysis

The external report's core broken invariant: a value meant to track real backing/work (staked ETH, or "who did the work") can drift/be misattributed, letting the wrong party benefit from an event they didn't actually cause or fund. The local Hyperbridge analog is a **reward-attribution bug in `pallet-consensus-incentives`**, where an entire batch's worth of state-machine-advancement rewards gets attributed to a single signer taken only from `messages[0]`, while the payout is computed over **every** `StateMachineUpdated` event the whole batch produced — regardless of which message in that batch actually generated it.

### Title
Consensus relayer reward misattribution lets an attacker steal rewards for state advances submitted by other relayers - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet_ismp::Pallet::handle_unsigned` is a permissionless, unsigned extrinsic that accepts an arbitrary `Vec<Message>` batch and executes it atomically [1](#0-0) . After execution, `FeeHandler::on_executed` is invoked once for the whole batch with all `messages` and all `events` produced [2](#0-1) . `pallet-consensus-incentives`'s implementation recovers the rewarded relayer's public key **only from `messages[0]`**, then loops over **every** `StateMachineUpdated` event in the batch (deduped to the highest height per state machine) and pays the full block-span reward for each one to that single signer [3](#0-2) .

### Finding Description
Since `handle_unsigned` is unsigned and permissionless, anyone can assemble an arbitrary batch of *already-valid* `Message::Consensus` items — including ones they did not produce (consensus proofs are broadcast in the public mempool/gossip before inclusion) — and submit them together in one transaction. `execute()` validates each message's own proof independently, so a batch containing one attacker-authored (or trivially generated, e.g. against a zero/low-`cost_per_block` state machine) consensus message plus several other relayers' pending, still-valid consensus messages will succeed in full [4](#0-3) .

`on_executed`'s attribution logic then:
1. Reads `messages[0]` and recovers the attacker's own sr25519 key from their own signed proof.
2. Reads all `IsmpEvent::StateMachineUpdated` events across the *entire executed batch*, keyed by `state_machine_id`, keeping only the highest `latest_height` per chain.
3. Pays `(latest_height - baseline) * cost_per_block` for **every** one of those state machines to the attacker's account [5](#0-4) .

There is no check binding a given `StateMachineUpdated` event to the specific message that produced it, and no check that `messages[0]`'s signer is the same relayer responsible for the other events in the batch. The reward-calculation itself (`calculate_reward`, watermark-based, replay-safe against rollbacks) is correct in isolation [6](#0-5)  — the bug is purely in *who* gets credited for the batch's aggregate progress.

### Impact Explanation
This is a direct "wrong beneficiary" fund-diversion primitive against the Hyperbridge treasury: an unprivileged attacker can redirect `$BRIDGE` rewards (plus minted reputation, which additionally feeds collator-selection weight per the relayer docs [7](#0-6) ) that should go to the relayers who actually generated and delivered each consensus proof, to themselves, simply by bundling public/gossiped valid messages behind their own cheap message in slot 0. This is loss of funds and unauthorized/incorrect execution of the reward-transfer logic, matching the bounty's "stealing or loss of funds" and "wrong beneficiary or amount" impact classes. It requires no malicious relayer, prover, or admin — only an ordinary participant able to submit an unsigned extrinsic with a batch they assembled from public data.

### Likelihood Explanation
High. `handle_unsigned` is designed to be freely composable (any `Vec<Message>`), the mempool for unsigned ISMP messages is public, and reconstructing a multi-message batch that reorders/adds a self-authored message at index 0 requires no cryptographic break — only re-submission of already-valid proofs in a different bundle before the original submitter's transaction lands. The existing test suite only covers single-message-per-batch scenarios (`test_incentivize_relayer`, `reward_covers_only_unpaid_heights_after_rollback`) and does not exercise the multi-message/multi-signer batch case [8](#0-7) .

### Recommendation
Attribute each `StateMachineUpdated` event to the specific `Message::Consensus` entry that produced it (e.g. by having the ISMP execution pipeline pair events with their originating message index/signer) rather than assuming a single batch-wide signer from `messages[0]`. Alternatively, restrict `on_executed` to only reward state-machine advances whose consensus message signer matches the relayer credited for that specific advance, and reject/split batches containing consensus messages from more than one distinct signer.

### Proof of Concept
1. Relayer A crafts and broadcasts (as an unsigned `handle_unsigned` extrinsic, per normal operation) `Message::Consensus(proof_A)` that will advance a high-value state machine `SM_high` (high `cost_per_block`) by many blocks.
2. Before A's transaction is included, attacker observes `proof_A` in the public mempool/gossip and constructs their own `handle_unsigned` batch: `[Message::Consensus(proof_attacker_cheap_for_SM_low), Message::Consensus(proof_A)]`, where `proof_attacker_cheap_for_SM_low` is a trivial, self-signed, valid consensus update for any chain (even `cost_per_block = 0`).
3. Attacker submits this batch. `execute()` validates and applies both messages, producing `StateMachineUpdated` events for both `SM_low` and `SM_high` [1](#0-0) .
4. `on_executed` recovers the signer from `messages[0]` (the attacker) and loops over both `StateMachineUpdated` events, paying the full `SM_high` block-span reward (and the `SM_low` one) to the attacker's account [9](#0-8) .
5. Relayer A's original transaction, if it lands afterward, either fails (message already applied/height already at watermark) or pays nothing new — A receives no reward for the work of producing `proof_A`, while the attacker collects the treasury payout intended for A.

### Citations

**File:** modules/pallets/ismp/src/lib.rs (L373-382)
```rust
		pub fn handle_unsigned(
			origin: OriginFor<T>,
			messages: Vec<Message>,
		) -> DispatchResultWithPostInfo {
			ensure_none(origin)?;

			Self::execute(messages.clone())?;

			Ok(().into())
		}
```

**File:** modules/pallets/ismp/src/lib.rs (L614-626)
```rust
		fn validate_unsigned(_source: TransactionSource, call: &Self::Call) -> TransactionValidity {
			use ismp::{
				messaging::{hash_request, ConsensusMessage, FraudProofMessage, RequestMessage},
				router::Request,
			};
			let messages = match call {
				Call::handle_unsigned { messages } => messages,
				_ => Err(TransactionValidityError::Invalid(InvalidTransaction::Call))?,
			};

			let events =
				Self::execute(messages.clone()).map_err(|_| InvalidTransaction::BadProof)?;

```

**File:** modules/pallets/consensus-incentives/src/impls.rs (L78-100)
```rust
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

**File:** docs/content/developers/explore/relayers.mdx (L73-81)
```text
Consensus relayers are paid in `$BRIDGE` on every accepted update,
plus a **non-transferable reputation asset** at a 1:1 ratio.
Reputation is the primary input to [collator selection](/developers/network/collator):
the more proofs you submit, the better your chances of being selected
to author Hyperbridge blocks.

Only the **first relayer** to submit a proof for a given state update
is rewarded. Later submitters for the same update receive nothing.
This is what drives the competitive market in low-latency operation.
```

**File:** modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs (L87-112)
```rust
#[test]
fn test_incentivize_relayer() {
	let mut ext = new_test_ext();
	ext.execute_with(|| {
		let host = Ismp::default();
		let state_machine_id = setup_state_machine();

		pallet_consensus_incentives::Pallet::<Test>::update_cost_per_block(
			RuntimeOrigin::root(),
			state_machine_id,
			100,
		)
		.unwrap();

		let (consensus_message, relayer_account) = setup_host_and_message(&host);

		pallet_ismp::Pallet::<Test>::handle_unsigned(
			RuntimeOrigin::none(),
			vec![consensus_message],
		)
		.unwrap();

		assert_eq!(Balances::balance(&relayer_account), UNIT + 4200);
		assert_eq!(Assets::balance(ReputationAssetId::get(), &relayer_account), 4200);
	})
}
```
