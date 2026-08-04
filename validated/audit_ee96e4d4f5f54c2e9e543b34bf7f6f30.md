## Analysis

The core broken invariant in the Yieldy report is: **a per-round baseline that the constructor fails to bind to the actual current time/height, letting a single call retroactively cross multiple round boundaries and pay out/unlock rewards that should have accrued gradually.**

The local analog is in `modules/pallets/consensus-incentives/src/impls.rs`, in `Pallet::calculate_reward`:

```rust
let latest_height = host.latest_commitment_height(state_machine_id.clone())...;
let previous_height = host.previous_commitment_height(state_machine_id.clone()).unwrap_or_default();
let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);
let blocks = latest_height.saturating_sub(baseline);
let reward = blocks_as_balance.saturating_mul(block_cost);
``` [1](#0-0) 

### Title
Relayer reward over-payment via unbounded first-reward baseline in `pallet-consensus-incentives` - (`modules/pallets/consensus-incentives/src/impls.rs`)

### Summary
`calculate_reward` pays a relayer `(latest_height - baseline) * block_cost`, where `baseline` falls back to `previous_commitment_height(...).unwrap_or_default()` (i.e. `0`) whenever `LastRewardedHeight` has never been set for that `state_machine_id`. This mirrors the Yieldy bug: the "first round" baseline is not bound to the actual starting point of tracking, so the very first reward computation can span an enormous, unintended range.

### Finding Description
`LastRewardedHeight` starts empty (`OptionQuery`) for every `state_machine_id` until the pallet pays its first reward for that chain [2](#0-1) . On that first call, `baseline` falls back to `host.previous_commitment_height(...)`, which itself defaults to `0` via `unwrap_or_default()` if the host has no "previous" height recorded for that state machine (e.g., a consensus client whose initial commitment was seeded once via `create_client`/`initialize_state`, which stores only a single height with no antecedent "previous" entry — see the BEEFY pallet's `initialize_state` seeding a single height=1 commitment) [3](#0-2) .

If, at the time of the first reward computation, `latest_height` is already large (a chain's genuine current chain height, or any height reached before `update_cost_per_block` was configured / before this pallet started tracking that state machine), `blocks = latest_height - 0` is enormous, and `reward = blocks * block_cost` mints and transfers a correspondingly enormous, one-shot payout from the treasury to whichever relayer's signature is attached to that batch's first `Message::Consensus` [4](#0-3) . This is functionally identical to the Yieldy bug class: a baseline/genesis reference point that is not enforced to equal "now" (or the actual first-tracked point), letting a single state transition retroactively span many un-accrued periods and pay out for all of them at once.

The relayer identity for this payout is recovered purely from a signature over the consensus proof bytes embedded in the message itself (`FeeHandler::on_executed`) [5](#0-4) , so any account that can produce/submit a valid consensus message for a state machine that has (a) `StateMachinesCostPerBlock` configured, and (b) no prior `LastRewardedHeight` watermark, collects the full backdated reward the first time such a message lands.

### Impact Explanation
This is a direct **loss of treasury funds**: `T::Currency::transfer` moves `blocks * block_cost` (potentially the full historical block range of a chain, e.g. millions of blocks × cost) from `T::TreasuryAccount` to a relayer account in a single call, with no cap tied to actual elapsed/tracked time [6](#0-5) . It also mints a proportional amount of `ReputationAsset` [7](#0-6) , inflating the relayer's soulbound reputation used elsewhere (e.g. collator ranking, per the testsuite comments) far beyond legitimate work performed.

### Likelihood Explanation
Triggering requires only: an admin having called `update_cost_per_block` for a state machine (an ordinary governance/config action, not itself privileged in the exploit sense once configured) and then the first-ever `Message::Consensus` batch for that state machine being processed while `LastRewardedHeight` is unset. This is a normal operational sequence (e.g., re-registering a state machine's cost after removal via `remove_incentives`, then `update_cost_per_block` again while the underlying `latest_commitment_height` is already far advanced) — no malicious relayer/prover/admin collusion is needed beyond an ordinary relayer submitting the next consensus update, which is the expected, permissionless, unprivileged action in the system.

### Recommendation
Do not fall back to `0`/`previous_commitment_height` when no watermark exists. On first-ever reward computation for a `state_machine_id`, initialize `LastRewardedHeight` to the current `latest_height` (or the height current on the block `update_cost_per_block` was set) without paying a backdated reward, so `blocks` accrues only prospectively — analogous to setting the first epoch's expiry to `block.timestamp/height + duration` at initialization rather than trusting an unguarded low/zero baseline.

### Proof of Concept
1. Admin calls `update_cost_per_block(state_machine_id, cost)` for a state machine whose `latest_commitment_height` is already at height `H` (e.g. `H = 20_000_000`), with `LastRewardedHeight` unset (fresh registration or after `remove_incentives` + re-`update_cost_per_block`).
2. A relayer submits any valid `Message::Consensus` update that advances the state machine to `H+1` (or even re-triggers processing at `H`).
3. `on_executed` collapses events, calls `process_message` → `calculate_reward`: `previous_height` defaults to `0` (no antecedent stored), `baseline = LastRewardedHeight::get(..).unwrap_or(previous_height) = 0`, `blocks = H - 0 = H`.
4. `reward = H * cost_per_block` is transferred from the treasury to the relayer in one call, and `LastRewardedHeight` is set to `H`, masking that the payout already happened [8](#0-7) .

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

**File:** modules/pallets/consensus-incentives/src/lib.rs (L81-86)
```rust
	/// The highest height a relayer has already been paid for, per state machine. Rewards only
	/// ever cover the span above this watermark, so a height that is revisited after a rollback
	/// is not paid for twice.
	#[pallet::storage]
	pub type LastRewardedHeight<T: Config> =
		StorageMap<_, Blake2_128Concat, StateMachineId, u64, OptionQuery>;
```

**File:** modules/pallets/beefy-consensus-proofs/src/lib.rs (L317-339)
```rust
			// Seed an initial commitment for the host state machine at the current block height.
			pallet_ismp::Pallet::<T>::create_consensus_client(
				frame_system::RawOrigin::Root.into(),
				ismp::messaging::CreateConsensusState {
					consensus_state: state.encode(),
					consensus_client_id: ismp_beefy::BEEFY_CONSENSUS_ID,
					consensus_state_id: ismp_beefy::BEEFY_CONSENSUS_ID,
					unbonding_period: T::UnbondingPeriod::get(),
					challenge_periods: Default::default(),
					state_machine_commitments: vec![(
						StateMachineId {
							consensus_state_id: T::ConsensusStateId::get(),
							state_id: host.host_state_machine(),
						},
						StateCommitmentHeight {
							height: 1,
							commitment: StateCommitment {
								timestamp: host.timestamp().as_secs(),
								overlay_root: None,
								state_root: H256::zero(),
							},
						},
					)],
```
