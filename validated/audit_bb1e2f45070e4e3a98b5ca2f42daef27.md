### Title
First-ever consensus update for a state machine pays a windfall reward for the entire historical block range instead of actual relayed work - (File: `modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`pallet-consensus-incentives` pays relayers `(LatestHeight - PreviousHeight) * CostPerBlock` for delivering `ConsensusMessage`s. Both `PreviousHeight` (from `pallet_ismp::PreviousStateMachineHeight`) and the `LastRewardedHeight` watermark default to `0` when a state machine has never been updated before. This means the very first consensus update ever accepted for a given `StateMachineId` is rewarded as if the relayer had incrementally delivered every block from height `0` up to whatever the initial/onboarding height happens to be — even though no relayer actually did that work. This is the same broken-invariant class as the `StakingContract.lastStakedEpoch` bug: a "last checkpoint" field defaults to zero for a new entity and the reward-calculation code treats that zero as a legitimate starting point rather than "not yet initialized," letting whoever submits first collect a payout sized to the whole historical span.

### Finding Description
The reward computation lives in `calculate_reward`: [1](#0-0) 

- `previous_height` comes from `IsmpHost::previous_commitment_height`, implemented as `PreviousStateMachineHeight::<T>::get(id)` with no `Some`/`None` distinction consumed here — the caller does `unwrap_or_default()`, so a state machine that has never had `store_latest_commitment_height` called returns `previous_height = 0`. [2](#0-1) 
- `LastRewardedHeight` (this pallet's own watermark) is likewise `None` for a state machine that has never been rewarded, and `calculate_reward` falls back to `previous_height` in that case: [3](#0-2) 
- `latest_height` is whatever height the very first accepted consensus proof establishes for that chain — which is not necessarily close to `0`; it can be an arbitrarily large starting height for a chain that's been running for years before Hyperbridge onboards it.

Consequently `reward = latest_height * cost_per_block` is paid out on the very first message, to whichever account's signature is attached to that first `ConsensusMessage`. Submitting consensus messages is permissionless — any relayer can construct and submit a valid `ConsensusMessage` via `pallet_ismp::Pallet::handle_unsigned`, and `FeeHandler::on_executed` recovers the payee purely from the signature embedded in the message, with no allowlist or additional authorization check: [4](#0-3) 

This is confirmed by the pallet's own test: for a freshly configured `state_machine_id` with `cost_per_block = 100`, the very first message ever processed pays `4200` (i.e., `42 blocks * 100`) straight out of the treasury, with no prior relayed history: [5](#0-4) 

No guard exists anywhere in the path (`process_message` / `calculate_reward` / `on_executed`) that special-cases "this is the state machine's first-ever recorded update" to either skip the reward or cap it — the code structurally cannot distinguish "genuinely delivered 42 blocks of incremental consensus progress" from "the client bootstrapped at height 42 because that's where governance/the client implementation started it."

### Impact Explanation
Whoever is fastest to submit the first `ConsensusMessage` for any `StateMachineId` that has a non-zero `StateMachinesCostPerBlock` configured collects `initial_height * cost_per_block` from `T::TreasuryAccount`, regardless of how much actual relaying work that represents. For chains onboarded at a large height (which is the normal case — most connected chains have already produced millions of blocks before Hyperbridge starts tracking them), this can drain a disproportionate, governance-unintended amount of treasury funds in a single transaction. This is a direct "stealing/loss of funds" logic attack against the treasury via the reward-claim path, matching the bounty's accepted impact class (logic attack / unauthorized value extraction from protocol funds).

### Likelihood Explanation
High. No privileged role, malicious relayer collusion, or compromised key is required — any party capable of observing a state machine's genuine consensus proof (which is public data, since Hyperbridge accepts proofs freely provided by connected chains, per the protocol's own design) and submitting `handle_unsigned` first captures the reward. The unit test in the repo already demonstrates the exact code path paying out for "block 0 to block 42" on first contact, proving the defect is triggerable with ordinary usage, not a contrived edge case.

### Recommendation
Do not default `previous_height`/`LastRewardedHeight` to `0` for a state machine's first-ever reward calculation. Instead:
- Initialize `LastRewardedHeight` (or an equivalent "onboarding height" marker) explicitly to the state machine's actual bootstrap height at the point the consensus client/state machine is registered (e.g., in the same extrinsic that sets `StateMachinesCostPerBlock` or adds the consensus state), so the first reward calculation has a correct, non-zero baseline.
- Alternatively, make `calculate_reward` skip rewarding on a state machine's first-ever update (`LastRewardedHeight` is `None`) and only start metering from the second update onward, recording the first `latest_height` as the initial watermark without paying for it.

### Proof of Concept
The existing test `test_incentivize_relayer` in `modules/pallets/testsuite/src/tests/pallet_consensus_incentives.rs:87-112` is itself the PoC:
1. `update_cost_per_block(root, state_machine_id, 100)` configures a reward rate for a state machine that has never been touched (`PreviousStateMachineHeight` and `LastRewardedHeight` are both unset).
2. A single `ConsensusMessage` signed by `relayer_account` is submitted via `pallet_ismp::Pallet::handle_unsigned`. The mock consensus client establishes the state machine at height `42` (`setup_mock_client`).
3. `on_executed` computes `previous_height = 0` (default), `latest_height = 42`, reward `= 42 * 100 = 4200`, and transfers it from the treasury to `relayer_account` — verified by `assert_eq!(Balances::balance(&relayer_account), UNIT + 4200)`.

In production this generalizes: an attacker races to submit the first valid consensus proof for any newly onboarded chain (which realistically starts at a much larger height than 42), collecting a reward proportional to that entire historical height rather than to any relaying work actually performed. [5](#0-4)

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L82-99)
```rust
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
```

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

**File:** modules/pallets/ismp/src/host.rs (L337-339)
```rust
	fn previous_commitment_height(&self, id: StateMachineId) -> Option<u64> {
		PreviousStateMachineHeight::<T>::get(id)
	}
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
