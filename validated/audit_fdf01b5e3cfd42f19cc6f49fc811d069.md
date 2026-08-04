## Analysis

The core broken invariant in the pSTAKE report is: **reward = (elapsed span) × (rate read at claim time)**, with no accounting for how the rate varied across that elapsed span. Whoever controls *when* they claim can extract more than they earned by waiting for a rate increase or rushing ahead of a rate decrease.

Hyperbridge's `pallet-consensus-incentives` reproduces this exact pattern for relayer consensus-delivery rewards.

### The vulnerable calculation

`calculate_reward` computes the reward for delivering a consensus proof as: [1](#0-0) 

```rust
let baseline = LastRewardedHeight::<T>::get(state_machine_id).unwrap_or(previous_height);
let blocks = latest_height.saturating_sub(baseline);
let blocks_as_balance = blocks.saturated_into();
let reward = blocks_as_balance.saturating_mul(block_cost);
```

`block_cost` is `StateMachinesCostPerBlock::<T>::get(state_machine_id)` — read fresh, at call time — and multiplied against the *entire* unpaid block span since `LastRewardedHeight`. Governance can change this rate at any time: [2](#0-1) 

There is no per-block or historical rate accounting — every block in the pending span is priced at whatever `StateMachinesCostPerBlock` happens to be *right now*, when `process_message` runs: [3](#0-2) 

Submission of a `Message::Consensus` proof is permissionless — any relayer with a valid consensus proof can submit it whenever they choose; there's no deadline forcing prompt delivery, and the reward-eligible relayer is derived purely from the signature on the message: [4](#0-3) 

### Title
Stale-rate reward exploitation in `pallet-consensus-incentives`: relayers can withhold valid consensus proofs to have an entire unpaid block-span retroactively priced at a future (higher) `StateMachinesCostPerBlock` rate — ([File: modules/pallets/consensus-incentives/src/impls.rs])

### Summary
`calculate_reward` prices the *whole* unrewarded block span (`latest_height - LastRewardedHeight`) using whatever `StateMachinesCostPerBlock` rate is active at the moment the relayer chooses to submit, not the rate(s) that were actually in effect while those blocks accrued. Because proof submission is permissionless and has no deadline, an unprivileged relayer can strategically delay delivering an already-valid consensus proof until after a rate increase (or rush before a decrease), causing the treasury to pay far more than the span actually earned under the historically correct rate schedule — a direct structural analog of the pSTAKE "reward rate not accounted for" bug.

### Finding Description
`process_message` and `calculate_reward` compute `reward = blocks × block_cost`, where `blocks = latest_commitment_height - LastRewardedHeight` and `block_cost` is read from `StateMachinesCostPerBlock` at execution time [5](#0-4) . This design assumes the rate is constant across the unpaid span, but `update_cost_per_block` lets governance change the rate at any time without retroactively splitting the pending span [6](#0-5) .

Because nothing in the pallet or the ISMP dispatch path enforces prompt delivery of consensus proofs (submission is driven entirely by whichever relayer chooses to relay), a relayer who observes that source-chain consensus has already advanced can simply hold off submitting the corresponding `Message::Consensus` update. If the relayer anticipates (or observes) an upcoming increase to `StateMachinesCostPerBlock`, they wait; once the new, higher rate is live, they submit the proof and collect a reward computed as `(all_pending_blocks) × (new_higher_rate)` instead of the correct mix of old/new rates. Symmetrically, if a decrease is imminent, the optimal move is to rush submission before it lands. This is the same "wait to gather rewards" incentive distortion described in the source report, just with the direction of exploitation flipped to whichever party controls delivery timing (the relayer) rather than the LP staker.

The reward is paid unconditionally from the treasury via `T::Currency::transfer` and matching reputation is minted, with no cap tying the payout to the rate(s) actually in force during the accrued span [7](#0-6) .

### Impact Explanation
This is a fund-extraction logic flaw against the protocol treasury: the amount paid out for delivering a given consensus update depends on submission timing rather than the actual historical cost schedule, letting a relayer capture windfall rewards disproportionate to the work performed, directly draining `T::TreasuryAccount`. The larger the accumulated unpaid block span (e.g., after a period of inactivity on a lower-traffic chain) and the larger the rate increase, the larger the improper extraction — this scales with treasury funds available.

### Likelihood Explanation
Any relayer can trigger this without any privileged role — they only need to hold a valid, deliverable consensus proof and choose *when* to submit it, which is entirely within their control and requires no compromise of governance, other relayers, or provers. Rate changes via `update_cost_per_block` are normal, expected governance operations (visible on-chain ahead of finalization / observable in the mempool), so timing around them is a realistic, low-effort strategy rather than a contrived edge case.

### Recommendation
Do not price the entire pending span at the currently active rate. Either (a) checkpoint/settle the reward at every rate change so each block-range is priced at the rate that was active during it (e.g., snapshot `(rate, height)` history and integrate over it), or (b) force settlement of any pending unpaid span at the old rate immediately before `update_cost_per_block` takes effect, so no historical blocks are ever paid at a future rate.

### Proof of Concept
1. Governance sets `StateMachinesCostPerBlock[X] = 1` and the chain accrues height from `H0` to `H0+1000` with no consensus update submitted (relayer withholds a proof it already has for `H0+1000`).
2. Governance later raises the rate: `update_cost_per_block(X, 1000)`.
3. Relayer now submits the withheld consensus proof for `H0+1000`. `calculate_reward` computes `blocks = 1000`, `block_cost = 1000` (the new rate), yielding `reward = 1_000_000` instead of the ~`1_000` that should have accrued under the rate that was actually in force for those blocks.
4. `LastRewardedHeight` advances to `H0+1000`, and the transfer/mint in `process_message` pays out the inflated amount from the treasury — see [8](#0-7) .

### Citations

**File:** modules/pallets/consensus-incentives/src/impls.rs (L41-100)
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

**File:** modules/pallets/consensus-incentives/src/impls.rs (L108-124)
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
```

**File:** modules/pallets/consensus-incentives/src/lib.rs (L130-150)
```rust
		/// Update cost per block for a state machine
		#[pallet::call_index(0)]
		#[pallet::weight(T::WeightInfo::update_cost_per_block())]
		pub fn update_cost_per_block(
			origin: OriginFor<T>,
			state_machine_id: StateMachineId,
			cost_per_block: <T as pallet_ismp::Config>::Balance,
		) -> DispatchResult {
			T::IncentivesOrigin::ensure_origin(origin)?;

			StateMachinesCostPerBlock::<T>::mutate(state_machine_id.clone(), |maybe_cost| {
				*maybe_cost = Some(cost_per_block);
			});

			Self::deposit_event(Event::<T>::StateMachineCostPerBlockUpdated {
				state_machine_id,
				cost_per_block,
			});

			Ok(())
		}
```
